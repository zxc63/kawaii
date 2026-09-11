#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
storage_pg.py — хранение на Postgres (в т.ч. Neon) для Karzen Business Bot.

Публичный API идентичен storage.py (SQLite), поэтому в боте меняется
ОДНА строка импорта. Отличия внутри:

  • Neon — это сеть. Каждый запрос = round-trip (из Люксембурга во Франкфурт
    обычно 10-30 мс). Дёргать БД на каждое входящее сообщение нельзя.
  • Поэтому здесь ЗЕРКАЛО В ПАМЯТИ: при старте всё читается в RAM, чтение
    идёт из памяти (0 мс), а запись уходит пачкой раз в N секунд.
  • Postgres тут = надёжное хранилище, а не хот-путь.

ВАЖНО: такая схема рассчитана на ОДИН процесс бота. Если запустишь два
воркера на одну базу — зеркала разъедутся. Тогда надо убирать кэш и
ходить в БД напрямую (готово в методах *_db ниже).

Neon: включи pooled-подключение (endpoint с -pooler) и sslmode=require.
    DATABASE_URL=postgresql://user:pass@ep-xxx-pooler.eu-central-1.aws.neon.tech/db?sslmode=require
"""

import asyncio
import json
import logging
import os
import time

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid        BIGINT PRIMARY KEY,
    name       TEXT    DEFAULT '',
    username   TEXT    DEFAULT '',
    first_seen BIGINT  DEFAULT 0,
    last_seen  BIGINT  DEFAULT 0,
    banned     BOOLEAN DEFAULT FALSE,
    caught     INTEGER DEFAULT 0,
    cmds       INTEGER DEFAULT 0,
    data       JSONB   DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_users_last ON users(last_seen DESC);

CREATE TABLE IF NOT EXISTS connections (
    uid  BIGINT PRIMARY KEY,
    cid  TEXT   NOT NULL,
    chat BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS peers (
    owner   BIGINT  NOT NULL,
    peer    BIGINT  NOT NULL,
    known   BOOLEAN DEFAULT FALSE,
    trusted BOOLEAN DEFAULT FALSE,
    seen    BIGINT  DEFAULT 0,
    PRIMARY KEY (owner, peer)
);

CREATE TABLE IF NOT EXISTS catches (
    id     BIGSERIAL PRIMARY KEY,
    owner  BIGINT NOT NULL,
    peer   BIGINT NOT NULL,
    ts     BIGINT NOT NULL,
    score  INTEGER DEFAULT 0,
    reason TEXT    DEFAULT '',
    text   TEXT    DEFAULT '',
    action TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_catch_owner ON catches(owner, ts DESC);
"""

USER_COLS = ("name", "username", "first_seen", "last_seen", "banned", "caught", "cmds")


class Storage:
    def __init__(self, dsn: str | None = None, defaults: dict | None = None):
        self.dsn = dsn or os.getenv("DATABASE_URL")
        if not self.dsn:
            raise RuntimeError("Нет DATABASE_URL — вставь строку подключения Neon")
        self.defaults = defaults or {}
        self.pool: asyncpg.Pool | None = None
        self._cache: dict[int, dict] = {}
        self._dirty: set[int] = set()
        self._conns: dict[int, dict] = {}
        self._peers: dict[tuple, dict] = {}       # (owner,peer) -> {known,trusted}
        self._peer_writes: list = []
        self._catch_writes: list = []

    # ── старт ────────────────────────────────────────────
    async def init(self, preload=True):
        self.pool = await asyncpg.create_pool(
            self.dsn, min_size=1, max_size=5, command_timeout=30,
            # Neon засыпает при простое — не держим мёртвые коннекты
            max_inactive_connection_lifetime=60)
        async with self.pool.acquire() as c:
            await c.execute(SCHEMA)
        if preload:
            await self._preload()

    async def _preload(self):
        t0 = time.time()
        async with self.pool.acquire() as c:
            for r in await c.fetch("SELECT * FROM connections"):
                self._conns[r["uid"]] = {"cid": r["cid"], "chat": r["chat"]}
            for r in await c.fetch("SELECT * FROM users"):
                u = {k: r[k] for k in USER_COLS}
                u.update(json.loads(r["data"] or "{}"))
                u["uid"] = r["uid"]
                self._fill_defaults(u)
                self._cache[r["uid"]] = u
            for r in await c.fetch("SELECT owner,peer,known,trusted FROM peers"):
                self._peers[(r["owner"], r["peer"])] = {"known": r["known"],
                                                        "trusted": r["trusted"]}
        logging.info("preload: %d юзеров, %d peers, %d подключений за %.2fs",
                     len(self._cache), len(self._peers), len(self._conns), time.time() - t0)

    def _fill_defaults(self, u):
        for k, v in self.defaults.items():
            if k not in u:
                u[k] = json.loads(json.dumps(v))
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    u[k].setdefault(kk, vv)

    # ── подключения ──────────────────────────────────────
    def set_connection(self, uid, cid, chat):
        uid = int(uid)
        self._conns[uid] = {"cid": cid, "chat": chat}
        self._fire("""INSERT INTO connections(uid,cid,chat) VALUES($1,$2,$3)
                      ON CONFLICT(uid) DO UPDATE SET cid=$2, chat=$3""", uid, cid, chat)

    def drop_connection(self, uid):
        uid = int(uid)
        self._conns.pop(uid, None)
        self._fire("DELETE FROM connections WHERE uid=$1", uid)

    def conn(self, uid):
        return self._conns.get(int(uid))

    def owner_by_cid(self, cid):
        for uid, c in self._conns.items():
            if c["cid"] == cid:
                return uid, c
        return None, None

    def connections_count(self):
        return len(self._conns)

    # ── пользователи (чтение из памяти) ──────────────────
    def user(self, uid, tg=None) -> dict:
        uid = int(uid)
        u = self._cache.get(uid)
        if u is None:
            u = json.loads(json.dumps(self.defaults))
            u["first_seen"] = int(time.time())
            u["uid"] = uid
            self._fill_defaults(u)
            self._cache[uid] = u
        if tg:
            u["name"] = tg.full_name
            u["username"] = tg.username or ""
        u["last_seen"] = int(time.time())
        self._dirty.add(uid)
        return u

    def mark(self, uid):
        self._dirty.add(int(uid))

    # ── собеседники (чтение из памяти) ───────────────────
    def is_known(self, owner, peer) -> bool:
        p = self._peers.get((int(owner), int(peer)))
        return bool(p and p["known"])

    def is_trusted(self, owner, peer) -> bool:
        p = self._peers.get((int(owner), int(peer)))
        return bool(p and p["trusted"])

    def add_known(self, owner, peer):
        k = (int(owner), int(peer))
        p = self._peers.setdefault(k, {"known": False, "trusted": False})
        p["known"] = True
        self._peer_writes.append((k[0], k[1], True, p["trusted"], int(time.time())))

    def set_trust(self, owner, peer, trusted=True):
        k = (int(owner), int(peer))
        p = self._peers.setdefault(k, {"known": False, "trusted": False})
        p["trusted"] = bool(trusted)
        self._peer_writes.append((k[0], k[1], p["known"], p["trusted"], int(time.time())))

    def counts(self, owner):
        owner = int(owner)
        k = sum(1 for (o, _), v in self._peers.items() if o == owner and v["known"])
        t = sum(1 for (o, _), v in self._peers.items() if o == owner and v["trusted"])
        return k, t

    # ── журнал ───────────────────────────────────────────
    def log_catch(self, owner, peer, score, reason, text, action):
        self._catch_writes.append((int(owner), int(peer), int(time.time()), score,
                                   reason, (text or "")[:1000], action))

    async def recent_catches(self, owner, limit=10):
        async with self.pool.acquire() as c:
            return await c.fetch("SELECT * FROM catches WHERE owner=$1 "
                                 "ORDER BY ts DESC LIMIT $2", int(owner), limit)

    # ── запись пачкой ────────────────────────────────────
    def _fire(self, sql, *args):
        """Одиночная запись, не блокирующая обработчик."""
        if self.pool:
            asyncio.create_task(self._exec(sql, *args))

    async def _exec(self, sql, *args):
        try:
            async with self.pool.acquire() as c:
                await c.execute(sql, *args)
        except Exception as ex:
            logging.error("pg exec: %s", ex)

    async def flush(self):
        if not self.pool:
            return
        users = [self._cache[u] for u in list(self._dirty) if u in self._cache]
        self._dirty.clear()
        peers, self._peer_writes = self._peer_writes, []
        catches, self._catch_writes = self._catch_writes, []
        if not (users or peers or catches):
            return
        try:
            async with self.pool.acquire() as c:
                async with c.transaction():
                    if users:
                        await c.executemany("""
                            INSERT INTO users(uid,name,username,first_seen,last_seen,
                                              banned,caught,cmds,data)
                            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
                            ON CONFLICT(uid) DO UPDATE SET name=$2,username=$3,
                              last_seen=$5,banned=$6,caught=$7,cmds=$8,data=$9::jsonb""",
                            [(u["uid"], u.get("name", ""), u.get("username", ""),
                              u.get("first_seen", 0), u.get("last_seen", 0),
                              bool(u.get("banned")), u.get("caught", 0), u.get("cmds", 0),
                              json.dumps({k: v for k, v in u.items()
                                          if k not in USER_COLS and k != "uid"},
                                         ensure_ascii=False)) for u in users])
                    if peers:
                        await c.executemany("""
                            INSERT INTO peers(owner,peer,known,trusted,seen)
                            VALUES($1,$2,$3,$4,$5)
                            ON CONFLICT(owner,peer) DO UPDATE
                              SET known=peers.known OR $3, trusted=$4, seen=$5""", peers)
                    if catches:
                        await c.executemany("""
                            INSERT INTO catches(owner,peer,ts,score,reason,text,action)
                            VALUES($1,$2,$3,$4,$5,$6,$7)""", catches)
        except Exception as ex:
            logging.error("pg flush: %s", ex)
            # вернём в очередь, попробуем в следующий раз
            self._peer_writes = peers + self._peer_writes
            self._catch_writes = catches + self._catch_writes
            for u in users:
                self._dirty.add(u["uid"])

    async def flush_loop(self, every=5):
        while True:
            await asyncio.sleep(every)
            await self.flush()

    # ── статистика / списки (из памяти — мгновенно) ──────
    def stats(self) -> dict:
        now = time.time()
        us = self._cache.values()
        return {
            "total": len(self._cache),
            "connected": len(self._conns),
            "week": sum(1 for u in us if u.get("first_seen", 0) > now - 7 * 86400),
            "day": sum(1 for u in us if u.get("last_seen", 0) > now - 86400),
            "banned": sum(1 for u in us if u.get("banned")),
            "caught": sum(u.get("caught", 0) for u in us),
            "cmds": sum(u.get("cmds", 0) for u in us),
            "catches_day": 0,     # см. stats_db() если нужна точная цифра
        }

    async def stats_db(self) -> dict:
        d = self.stats()
        async with self.pool.acquire() as c:
            d["catches_day"] = await c.fetchval(
                "SELECT COUNT(*) FROM catches WHERE ts>$1", int(time.time()) - 86400)
        return d

    def page(self, offset=0, limit=8):
        items = sorted(self._cache.values(), key=lambda u: -u.get("last_seen", 0))
        return items[offset:offset + limit], len(items)

    def find(self, query):
        q = query.lower()
        return [u for u in self._cache.values()
                if q in (u.get("name", "") or "").lower()
                or q in (u.get("username", "") or "").lower()
                or q in str(u["uid"])][:20]

    def all_uids(self):
        return list(self._cache)

    async def close(self):
        await self.flush()
        if self.pool:
            await self.pool.close()


# ═════════════════════════════════════════════════════════
#  ПЕРЕЛИВ ИЗ SQLITE В POSTGRES/NEON
# ═════════════════════════════════════════════════════════
async def migrate_sqlite(sqlite_path="bot.db", dsn=None, defaults=None):
    import sqlite3
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    st = Storage(dsn, defaults or {})
    await st.init(preload=False)

    for r in src.execute("SELECT * FROM users"):
        u = st.user(r["uid"])
        for k in USER_COLS:
            u[k] = r[k]
        u["banned"] = bool(r["banned"])
        u.update(json.loads(r["data"] or "{}"))
        st.mark(r["uid"])
    for r in src.execute("SELECT * FROM peers"):
        if r["known"]:
            st.add_known(r["owner"], r["peer"])
        if r["trusted"]:
            st.set_trust(r["owner"], r["peer"], True)
    for r in src.execute("SELECT * FROM catches"):
        st._catch_writes.append((r["owner"], r["peer"], r["ts"], r["score"],
                                 r["reason"], r["text"], r["action"]))
    await st.flush()
    for r in src.execute("SELECT * FROM connections"):
        st.set_connection(r["uid"], r["cid"], r["chat"])
    await asyncio.sleep(0.5)
    n = len(st._cache)
    await st.close()
    src.close()
    print(f"✅ перелито в Postgres: {n} юзеров")


if __name__ == "__main__":
    asyncio.run(migrate_sqlite(dsn=os.getenv("DATABASE_URL")))
