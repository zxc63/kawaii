#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
storage.py — слой хранения на SQLite для Karzen Business Bot.

Почему не JSON:
  • JSON переписывается ЦЕЛИКОМ при каждом сохранении → на 500 юзерах
    это мегабайты записи каждые 5 секунд
  • нет индексов: «покажи активных за сутки» = перебор всего файла
  • падение посреди записи = битый файл (частично лечится .tmp, но всё равно)
  • список known-диалогов рос внутри юзера и его пришлось резать до 3000

Схема:
  users        — профиль + настройки (JSON в одной колонке) + индексируемые поля
  connections  — активные бизнес-подключения
  peers        — собеседники: знаком / доверенный (вместо списков внутри юзера)
  catches      — журнал пойманного спама (для модерации и статистики)

Режим работы: данные юзеров держим в памяти (быстро), на диск пишем
write-behind — только изменённые записи, раз в N секунд. WAL включён,
так что чтение не блокируется записью.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS users (
    uid        INTEGER PRIMARY KEY,
    name       TEXT    DEFAULT '',
    username   TEXT    DEFAULT '',
    first_seen INTEGER DEFAULT 0,
    last_seen  INTEGER DEFAULT 0,
    banned     INTEGER DEFAULT 0,
    caught     INTEGER DEFAULT 0,
    cmds       INTEGER DEFAULT 0,
    data       TEXT    DEFAULT '{}'      -- настройки: моды, antiscam, filter, pairs
);
CREATE INDEX IF NOT EXISTS idx_users_last  ON users(last_seen);
CREATE INDEX IF NOT EXISTS idx_users_ban   ON users(banned);

CREATE TABLE IF NOT EXISTS connections (
    uid  INTEGER PRIMARY KEY,
    cid  TEXT NOT NULL,
    chat INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conn_cid ON connections(cid);

CREATE TABLE IF NOT EXISTS peers (
    owner   INTEGER NOT NULL,
    peer    INTEGER NOT NULL,
    known   INTEGER DEFAULT 0,
    trusted INTEGER DEFAULT 0,
    seen    INTEGER DEFAULT 0,
    PRIMARY KEY (owner, peer)
);

CREATE TABLE IF NOT EXISTS catches (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    owner  INTEGER NOT NULL,
    peer   INTEGER NOT NULL,
    ts     INTEGER NOT NULL,
    score  INTEGER DEFAULT 0,
    reason TEXT    DEFAULT '',
    text   TEXT    DEFAULT '',
    action TEXT    DEFAULT ''            -- deleted / notified
);
CREATE INDEX IF NOT EXISTS idx_catch_owner ON catches(owner, ts);

CREATE TABLE IF NOT EXISTS media (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner    INTEGER NOT NULL,
    peer     INTEGER NOT NULL,
    sender   INTEGER NOT NULL,
    ts       INTEGER NOT NULL,
    kind     TEXT DEFAULT '',        -- photo/video/voice/video_note/document/animation
    file_id  TEXT DEFAULT '',
    file_uid TEXT DEFAULT '',        -- стабильный id: по нему ловим дубли
    size     INTEGER DEFAULT 0,
    saved    INTEGER DEFAULT 0,      -- message_id копии в архиве
    note     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_media_owner ON media(owner, ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_media_uid ON media(owner, file_uid);
"""

USER_COLS = ("name", "username", "first_seen", "last_seen", "banned", "caught", "cmds")


class Storage:
    def __init__(self, path="bot.db", defaults: dict | None = None):
        self.path = path
        self.defaults = defaults or {}
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._cache: dict[int, dict] = {}
        self._dirty: set[int] = set()
        self._conns: dict[int, dict] = {}
        self._load_connections()

    # ── подключения ──────────────────────────────────────
    def _load_connections(self):
        for r in self.db.execute("SELECT * FROM connections"):
            self._conns[r["uid"]] = {"cid": r["cid"], "chat": r["chat"]}

    def set_connection(self, uid, cid, chat):
        self._conns[int(uid)] = {"cid": cid, "chat": chat}
        self.db.execute("INSERT INTO connections(uid,cid,chat) VALUES(?,?,?) "
                        "ON CONFLICT(uid) DO UPDATE SET cid=excluded.cid, chat=excluded.chat",
                        (int(uid), cid, chat))
        self.db.commit()

    def drop_connection(self, uid):
        self._conns.pop(int(uid), None)
        self.db.execute("DELETE FROM connections WHERE uid=?", (int(uid),))
        self.db.commit()

    def conn(self, uid):
        return self._conns.get(int(uid))

    def owner_by_cid(self, cid):
        for uid, c in self._conns.items():
            if c["cid"] == cid:
                return uid, c
        return None, None

    def connections_count(self):
        return len(self._conns)

    # ── пользователи ─────────────────────────────────────
    def user(self, uid, tg=None) -> dict:
        uid = int(uid)
        u = self._cache.get(uid)
        if u is None:
            row = self.db.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
            if row:
                u = {k: row[k] for k in USER_COLS}
                u["banned"] = bool(u["banned"])
                u.update(json.loads(row["data"] or "{}"))
            else:
                u = json.loads(json.dumps(self.defaults))
                u["first_seen"] = int(time.time())
                self.db.execute("INSERT OR IGNORE INTO users(uid,first_seen) VALUES(?,?)",
                                (uid, u["first_seen"]))
                self.db.commit()
            u["uid"] = uid
            # добить недостающие ключи из шаблона (миграция настроек)
            for k, v in self.defaults.items():
                if k not in u:
                    u[k] = json.loads(json.dumps(v))
                elif isinstance(v, dict):
                    for kk, vv in v.items():
                        u[k].setdefault(kk, vv)
            self._cache[uid] = u
        if tg:
            u["name"] = tg.full_name
            u["username"] = tg.username or ""
        u["last_seen"] = int(time.time())
        self._dirty.add(uid)
        return u

    def mark(self, uid):
        self._dirty.add(int(uid))

    def flush(self):
        if not self._dirty:
            return 0
        n = 0
        for uid in list(self._dirty):
            u = self._cache.get(uid)
            if not u:
                continue
            data = {k: v for k, v in u.items()
                    if k not in USER_COLS and k != "uid"}
            self.db.execute(
                "INSERT INTO users(uid,name,username,first_seen,last_seen,banned,caught,cmds,data)"
                " VALUES(?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(uid) DO UPDATE SET name=excluded.name,username=excluded.username,"
                " last_seen=excluded.last_seen,banned=excluded.banned,caught=excluded.caught,"
                " cmds=excluded.cmds,data=excluded.data",
                (uid, u.get("name", ""), u.get("username", ""), u.get("first_seen", 0),
                 u.get("last_seen", 0), int(bool(u.get("banned"))), u.get("caught", 0),
                 u.get("cmds", 0), json.dumps(data, ensure_ascii=False)))
            n += 1
        self.db.commit()
        self._dirty.clear()
        return n

    async def flush_loop(self, every=5):
        while True:
            await asyncio.sleep(every)
            try:
                await asyncio.to_thread(self.flush)
            except Exception as ex:
                logging.error("flush: %s", ex)

    # ── собеседники ──────────────────────────────────────
    def is_known(self, owner, peer) -> bool:
        r = self.db.execute("SELECT known FROM peers WHERE owner=? AND peer=?",
                            (int(owner), int(peer))).fetchone()
        return bool(r and r["known"])

    def is_trusted(self, owner, peer) -> bool:
        r = self.db.execute("SELECT trusted FROM peers WHERE owner=? AND peer=?",
                            (int(owner), int(peer))).fetchone()
        return bool(r and r["trusted"])

    def add_known(self, owner, peer):
        self.db.execute(
            "INSERT INTO peers(owner,peer,known,seen) VALUES(?,?,1,?) "
            "ON CONFLICT(owner,peer) DO UPDATE SET known=1, seen=excluded.seen",
            (int(owner), int(peer), int(time.time())))
        self.db.commit()

    def set_trust(self, owner, peer, trusted=True):
        self.db.execute(
            "INSERT INTO peers(owner,peer,trusted,seen) VALUES(?,?,?,?) "
            "ON CONFLICT(owner,peer) DO UPDATE SET trusted=excluded.trusted",
            (int(owner), int(peer), int(bool(trusted)), int(time.time())))
        self.db.commit()

    def counts(self, owner):
        r = self.db.execute(
            "SELECT SUM(known) k, SUM(trusted) t FROM peers WHERE owner=?",
            (int(owner),)).fetchone()
        return (r["k"] or 0), (r["t"] or 0)

    # ── журнал пойманного ────────────────────────────────
    def log_catch(self, owner, peer, score, reason, text, action):
        self.db.execute(
            "INSERT INTO catches(owner,peer,ts,score,reason,text,action) VALUES(?,?,?,?,?,?,?)",
            (int(owner), int(peer), int(time.time()), score, reason, (text or "")[:1000], action))
        self.db.commit()

    def recent_catches(self, owner, limit=10):
        return self.db.execute(
            "SELECT * FROM catches WHERE owner=? ORDER BY ts DESC LIMIT ?",
            (int(owner), limit)).fetchall()

    # ── архив медиа ──────────────────────────────────────
    def log_media(self, owner, peer, sender, kind, file_id, file_uid, size, saved, note=""):
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO media(owner,peer,sender,ts,kind,file_id,file_uid,"
                "size,saved,note) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (int(owner), int(peer), int(sender), int(time.time()), kind,
                 file_id, file_uid, size or 0, saved or 0, note))
            self.db.commit()
            return True
        except Exception as ex:
            logging.error("log_media: %s", ex)
            return False

    def has_media(self, owner, file_uid) -> bool:
        return bool(self.db.execute("SELECT 1 FROM media WHERE owner=? AND file_uid=?",
                                    (int(owner), file_uid)).fetchone())

    def media_saved_id(self, owner, file_uid):
        r = self.db.execute("SELECT saved FROM media WHERE owner=? AND file_uid=?",
                            (int(owner), file_uid)).fetchone()
        return r["saved"] if r else None

    def media_stats(self, owner):
        r = self.db.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(size),0) s FROM media WHERE owner=?",
            (int(owner),)).fetchone()
        return r["n"], r["s"]

    def recent_media(self, owner, limit=10):
        return self.db.execute("SELECT * FROM media WHERE owner=? ORDER BY ts DESC LIMIT ?",
                               (int(owner), limit)).fetchall()

    # ── статистика / списки (SQL вместо перебора) ────────
    def stats(self) -> dict:
        now = int(time.time())
        q = lambda sql, *a: self.db.execute(sql, a).fetchone()[0] or 0
        return {
            "total": q("SELECT COUNT(*) FROM users"),
            "connected": len(self._conns),
            "week": q("SELECT COUNT(*) FROM users WHERE first_seen>?", now - 7 * 86400),
            "day": q("SELECT COUNT(*) FROM users WHERE last_seen>?", now - 86400),
            "banned": q("SELECT COUNT(*) FROM users WHERE banned=1"),
            "caught": q("SELECT COALESCE(SUM(caught),0) FROM users"),
            "cmds": q("SELECT COALESCE(SUM(cmds),0) FROM users"),
            "catches_day": q("SELECT COUNT(*) FROM catches WHERE ts>?", now - 86400),
        }

    def page(self, offset=0, limit=8):
        rows = self.db.execute(
            "SELECT uid,name,username,banned,caught FROM users "
            "ORDER BY last_seen DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        total = self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return rows, total

    def find(self, query):
        q = f"%{query.lower()}%"
        return self.db.execute(
            "SELECT uid,name,username,banned,caught FROM users "
            "WHERE LOWER(name) LIKE ? OR LOWER(username) LIKE ? OR CAST(uid AS TEXT) LIKE ? "
            "LIMIT 20", (q, q, q)).fetchall()

    def all_uids(self):
        return [r[0] for r in self.db.execute("SELECT uid FROM users")]

    def close(self):
        self.flush()
        self.db.close()


# ═════════════════════════════════════════════════════════
#  МИГРАЦИЯ СО СТАРОГО JSON
# ═════════════════════════════════════════════════════════
def migrate_json(json_path="bizbot_state.json", db_path="bot.db", defaults=None):
    if not os.path.exists(json_path):
        print(f"нет {json_path} — миграция не нужна")
        return
    with open(json_path, encoding="utf-8") as f:
        S = json.load(f)
    st = Storage(db_path, defaults or {})
    users = S.get("users", {})
    for uid, u in users.items():
        uid = int(uid)
        known = u.pop("known", []) or []
        white = u.pop("white", []) or []
        cur = st.user(uid)
        cur.update(u)
        st.mark(uid)
        for p in known:
            st.add_known(uid, p)
        for p in white:
            st.set_trust(uid, p, True)
    for uid, c in S.get("connections", {}).items():
        st.set_connection(int(uid), c["cid"], c["chat"])
    st.flush()
    os.rename(json_path, json_path + ".migrated")
    print(f"✅ перенесено юзеров: {len(users)}, подключений: {len(S.get('connections', {}))}")
    print(f"   старый файл → {json_path}.migrated")
    st.close()


if __name__ == "__main__":
    migrate_json()
