#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
  Karzen UserBot  ♡  (Telethon)
=========================================================
Юзербот крутится на ТВОЁМ аккаунте (не bot-токен!).
Команды пишешь сам себе в любой чат с префиксом "."

Что внутри:
  • анти-делит (ловит удалённые сообщения -> в Избранное)
  • kawaii / tsundere / yandere трансформации
  • "словесный мод" (.mode) — авто-стиль на ВСЁ что ты пишешь
  • форматирование, afk, время/статус в имени, мут, приколы

⚠️  Автоматизация юзер-аккаунта против ToS Telegram.
    Юзай второй/левый аккаунт, а не основной — могут прилететь ограничения.

Установка:
    pip install telethon pillow
    + ffmpeg в системе (для .gif / .fv / .story)

Первый запуск попросит номер телефона + код из Telegram.
=========================================================
"""

import asyncio
import io
import json
import os
import random
import re
import subprocess
import tempfile
import time
from collections import OrderedDict
from datetime import datetime

from telethon import TelegramClient, events, functions, types
from telethon.errors import MessageNotModifiedError

# ─────────────────────────────────────────────────────────
#  КОНФИГ  — вставь свои api_id / api_hash с my.telegram.org
# ─────────────────────────────────────────────────────────
API_ID = 123456                     # <-- сюда
API_HASH = "your_api_hash_here"     # <-- сюда
SESSION = "karzen_userbot"
PREFIX = "."

STATE_FILE = "ub_state.json"
ANTIDELETE_CACHE = 5000             # сколько последних сообщений держать в памяти

client = TelegramClient(SESSION, API_ID, API_HASH)

# ─────────────────────────────────────────────────────────
#  ХРАНИЛКА СОСТОЯНИЯ (моды/afk/имя)
# ─────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"mode": None, "afk": None, "orig_last_name": None,
            "chat_modes": {}, "pending_in": {}, "pending_out": {}}

def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(STATE, f, ensure_ascii=False, indent=2)

STATE = load_state()
for _k, _v in (("chat_modes", {}), ("pending_in", {}), ("pending_out", {})):
    STATE.setdefault(_k, _v)

# ─────────────────────────────────────────────────────────
#  ТРАНСФОРМАЦИИ ТЕКСТА
# ─────────────────────────────────────────────────────────
KAOMOJI = [
    "(◕‿◕✿)", "(｡♥‿♥｡)", "(*≧ω≦*)", "ʕ•ᴥ•ʔ", "(っ◔◡◔)っ ♡",
    "(˘⌣˘)♡", "ヽ(=^･ω･^=)丿", "(´｡• ᵕ •｡`)", "♡(˃͈ દ ˂͈ )", "(⁄ ⁄•⁄ω⁄•⁄ ⁄)",
    "(◍•ᴗ•◍)♡", "( ˶˘ ³˘)♡", "(*ฅ́˘ฅ̀*)♡", "ｷｬｯ♡"
]
KAWAII_WORDS = ["ня", "мур", "ня~", "уву", "нявушки", "мяу"]

def kawaii(text: str) -> str:
    if not text:
        return text
    t = text
    # мягкость + няшность
    t = t.replace("!", "! ♡").replace("?", "? >_<")
    t = re.sub(r"([bpBРр])", lambda m: m.group(1), t)   # плейсхолдер, можно расширять
    t = t.replace("л", "ль").replace("р", "рь")          # чуть шепелявим
    fill = random.choice(KAWAII_WORDS)
    return f"{t} {fill}~ {random.choice(KAOMOJI)}"

def tsundere(text: str) -> str:
    if not text:
        return text
    pre = random.choice([
        "Н-не то чтобы я специально, но ",
        "Х-хмф! ",
        "Б-бака! ",
        "Не думай ничего такого, но ",
    ])
    post = random.choice([
        " ...и вообще, мне всё равно! (>﹏<)",
        " ...б-бака.",
        " ...не благодари, ясно?! (๑•̀ㅁ•́๑)",
        " Хмф! (￣^￣)",
    ])
    return f"{pre}{text.lower()}{post}"

def yandere(text: str) -> str:
    if not text:
        return text
    post = random.choice([
        " Ты ведь только мой, да?~ 🔪♡",
        " Я никому тебя не отдам... никогда~ (◡‿◡✿)🔪",
        " Не смотри на других, хорошо?~ ♡",
        " Мы будем вместе. Навсегда. ♡🔪",
    ])
    return f"{text}{post}"

def leet(text: str) -> str:
    table = str.maketrans({
        "a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7",
        "A": "4", "E": "3", "I": "1", "O": "0", "S": "5", "T": "7",
        "а": "@", "е": "3", "о": "0", "и": "1",
    })
    return text.translate(table)

MODES = {
    "kawaii": kawaii,
    "tsundere": tsundere,
    "yandere": yandere,
    "leet": leet,
}

# раскладка для .sw (RU <-> EN qwerty)
RU = "йцукенгшщзхъфывапролджэячсмитьбю.ё"
EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,./`"
def switch_layout(text: str) -> str:
    ru2en = {r: e for r, e in zip(RU, EN)}
    en2ru = {e: r for r, e in zip(RU, EN)}
    # угадываем направление по большинству символов
    ru_hits = sum(c.lower() in ru2en for c in text)
    en_hits = sum(c.lower() in en2ru for c in text)
    table = ru2en if ru_hits >= en_hits else en2ru
    return "".join(table.get(c, table.get(c.lower(), c)) for c in text)

# ─────────────────────────────────────────────────────────
#  РЕЕСТР КОМАНД
# ─────────────────────────────────────────────────────────
COMMANDS = {}

def cmd(*names):
    def deco(fn):
        for n in names:
            COMMANDS[n] = fn
        return fn
    return deco

# ─────────────────────────────────────────────────────────
#  СОВМЕСТНЫЙ МОД (co-op sync между двумя юзерботами)
# ─────────────────────────────────────────────────────────
#  Логика: НИКАКОГО удалённого управления. Только приглашение.
#    ты:  .pair kawaii   -> уходит приглашение (+ скрытый payload)
#    он:  его бот спрашивает -> он пишет .accept / .deny
#    оба: мод включается ТОЛЬКО в этом чате, у каждого локально
#    любой: .unpair -> выключается у обоих
# ─────────────────────────────────────────────────────────
ZW0, ZW1, ZWSEP = "\u200b", "\u200c", "\u2060"   # zero-width: 0, 1, разделитель

def zw_encode(payload: str) -> str:
    bits = "".join(f"{b:08b}" for b in payload.encode("utf-8"))
    return ZWSEP + "".join(ZW1 if b == "1" else ZW0 for b in bits) + ZWSEP

def zw_decode(text: str):
    if text.count(ZWSEP) < 2:
        return None
    try:
        body = text.split(ZWSEP)[1]
        bits = "".join("1" if c == ZW1 else "0" for c in body if c in (ZW0, ZW1))
        if len(bits) < 8:
            return None
        data = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits) - len(bits) % 8, 8))
        s = data.decode("utf-8", errors="ignore")
        return s if s.startswith("UBSYNC|") else None
    except Exception:
        return None

def has_payload(text: str) -> bool:
    return bool(text) and text.count(ZWSEP) >= 2

def chat_mode(chat_id) -> str | None:
    return STATE["chat_modes"].get(str(chat_id))

def set_chat_mode(chat_id, mode):
    if mode:
        STATE["chat_modes"][str(chat_id)] = mode
    else:
        STATE["chat_modes"].pop(str(chat_id), None)
    save_state()

# ── команды пары ──────────────────────────────────────────
@cmd("pair")
async def _pair(e, args):
    m = args.strip().lower()
    if m not in MODES:
        return await e.edit(f"⚠️ `.pair <{'|'.join(MODES)}>`")
    STATE["pending_out"][str(e.chat_id)] = m
    save_state()
    await e.edit(
        f"🤝 Предлагаю **совместный мод: {m}**\n"
        f"Если у тебя стоит такой же бот — ответь `.accept` (или `.deny`)."
        + zw_encode(f"UBSYNC|REQ|{m}")
    )

@cmd("accept")
async def _accept(e, args):
    m = STATE["pending_in"].pop(str(e.chat_id), None)
    save_state()
    if not m:
        return await e.edit("🤷 Тут нет активного приглашения.")
    set_chat_mode(e.chat_id, m)
    await e.edit(f"🤝 Принято! Совместный мод **{m}** включён ♡"
                 + zw_encode(f"UBSYNC|OK|{m}"))

@cmd("deny")
async def _deny(e, args):
    m = STATE["pending_in"].pop(str(e.chat_id), None)
    save_state()
    if not m:
        return await e.edit("🤷 Тут нет активного приглашения.")
    await e.edit("🚫 Отклонено." + zw_encode("UBSYNC|NO|-"))

@cmd("unpair")
async def _unpair(e, args):
    set_chat_mode(e.chat_id, None)
    STATE["pending_in"].pop(str(e.chat_id), None)
    STATE["pending_out"].pop(str(e.chat_id), None)
    save_state()
    await e.edit("👋 Совместный мод выключен." + zw_encode("UBSYNC|END|-"))

@cmd("pairs")
async def _pairs(e, args):
    cm = STATE["chat_modes"]
    if not cm:
        return await e.edit("📭 Активных пар нет.")
    lines = []
    for cid, m in cm.items():
        try:
            ent = await client.get_entity(int(cid))
            nm = getattr(ent, "first_name", None) or getattr(ent, "title", cid)
        except Exception:
            nm = cid
        lines.append(f"• {nm} — **{m}**")
    await e.edit("🤝 **Совместные моды:**\n" + "\n".join(lines))

# ── приём протокольных сообщений ──────────────────────────
async def handle_sync(e):
    payload = zw_decode(e.message.message or "")
    if not payload:
        return False
    _, kind, val = (payload.split("|") + ["", ""])[:3]
    cid = str(e.chat_id)

    if kind == "REQ" and val in MODES:
        STATE["pending_in"][cid] = val
        save_state()
        try:
            ent = await e.get_sender()
            nm = getattr(ent, "first_name", "Собеседник")
            await client.send_message(
                "me",
                f"🤝 **{nm}** предлагает совместный мод **{val}**\n"
                f"Ответь в том чате: `.accept` или `.deny`")
        except Exception:
            pass

    elif kind == "OK" and val in MODES:
        if STATE["pending_out"].pop(cid, None) == val or True:
            set_chat_mode(e.chat_id, val)
            save_state()
            await client.send_message("me", f"✅ Совместный мод **{val}** подтверждён ♡")

    elif kind == "NO":
        STATE["pending_out"].pop(cid, None)
        save_state()

    elif kind == "END":
        set_chat_mode(e.chat_id, None)
        STATE["pending_in"].pop(cid, None)
        STATE["pending_out"].pop(cid, None)
        save_state()
        await client.send_message("me", "👋 Собеседник выключил совместный мод.")
    return True


# ─────────────────────────────────────────────────────────
#  СГОРАЮЩИЕ МЕДИА (одноразовый просмотр / таймер)
# ─────────────────────────────────────────────────────────
#  Работает ТОЛЬКО здесь, через MTProto: у Telethon в media есть
#  ttl_seconds. В Bot API такого поля нет вообще — поэтому бизнес-бот
#  сгорающие медиа увидеть не может, это ограничение самого Telegram.
#
#  ⚠️ Отправитель выбрал одноразовый просмотр намеренно. Сохраняя такое,
#     ты нарушаешь его ожидания — а для интимного контента в ЕС это ещё
#     и уголовная статья при пересылке. Решай сам, но знай, что делаешь.
SAVE_TTL = True          # автосохранение сгорающих медиа


def ttl_of(msg):
    """ttl_seconds у фото/видео, если сообщение одноразовое."""
    media = getattr(msg, "media", None)
    if media is None:
        return None
    for attr in ("ttl_seconds",):
        v = getattr(media, attr, None)
        if v:
            return v
        inner = getattr(media, "photo", None) or getattr(media, "document", None)
        if inner is not None:
            v = getattr(media, attr, None)
            if v:
                return v
    return None


async def save_burning(e):
    """Скачать сгорающее медиа ДО просмотра и положить в Избранное."""
    if not SAVE_TTL:
        return False
    msg = e.message
    ttl = ttl_of(msg)
    if not ttl:
        return False
    try:
        data = await msg.download_media(bytes)
        if not data:
            return False
        ent = await msg.get_sender()
        who = getattr(ent, "first_name", None) or getattr(ent, "title", str(msg.sender_id))
        kind = "видео" if getattr(msg, "video", None) else "фото"
        buf = io.BytesIO(data)
        buf.name = f"burning_{msg.id}.{'mp4' if kind == 'видео' else 'jpg'}"
        await client.send_file(
            "me", buf,
            caption=(f"🔥 <b>Сгорающее {kind}</b>\n"
                     f"👤 {who} (id {msg.sender_id})\n"
                     f"⏱ таймер: {ttl} сек · {datetime.now():%d.%m %H:%M}"),
            parse_mode="html")
        return True
    except Exception as ex:
        try:
            await client.send_message("me", f"⚠️ Не смог сохранить сгорающее медиа: {ex}")
        except Exception:
            pass
        return False


# ─────────────────────────────────────────────────────────
#  АНТИ-ДЕЛИТ: кэш последних сообщений
# ─────────────────────────────────────────────────────────
msg_cache = OrderedDict()  # msg_id -> dict(chat_id, sender, text, message_obj)

def cache_put(msg):
    try:
        msg_cache[msg.id] = {
            "chat_id": msg.chat_id,
            "sender": msg.sender_id,
            "text": msg.message or "",
            "obj": msg,
            "ts": time.time(),
        }
        while len(msg_cache) > ANTIDELETE_CACHE:
            msg_cache.popitem(last=False)
    except Exception:
        pass

@client.on(events.NewMessage(incoming=True))
async def _cache_incoming(e):
    cache_put(e.message)
    await save_burning(e)         # сгорающее медиа — хватаем сразу
    if await handle_sync(e):      # протокольное сообщение — дальше не идём
        return
    await afk_autoreply(e)

@client.on(events.MessageDeleted)
async def _on_delete(e):
    for mid in e.deleted_ids:
        info = msg_cache.get(mid)
        if not info:
            continue
        who = info["sender"]
        text = info["text"]
        try:
            name = who
            try:
                ent = await client.get_entity(who)
                name = getattr(ent, "first_name", None) or getattr(ent, "title", str(who))
            except Exception:
                pass
            header = f"🗑 Удалённое сообщение\n👤 {name} (id {who})\n"
            if text:
                await client.send_message("me", header + f"\n💬 {text}")
            else:
                # медиа — пробуем переслать сохранённый объект
                try:
                    await client.forward_messages("me", info["obj"])
                    await client.send_message("me", header + "\n📎 (медиа выше)")
                except Exception:
                    await client.send_message("me", header + "\n📎 (медиа не удалось сохранить)")
        except Exception:
            pass
        msg_cache.pop(mid, None)

# ─────────────────────────────────────────────────────────
#  AFK
# ─────────────────────────────────────────────────────────
_afk_cooldown = {}

async def afk_autoreply(e):
    if not STATE.get("afk"):
        return
    if not (e.is_private or (e.mentioned)):
        return
    uid = e.sender_id
    now = time.time()
    if now - _afk_cooldown.get(uid, 0) < 60:   # не спамим чаще раза в минуту
        return
    _afk_cooldown[uid] = now
    reason = STATE["afk"]
    txt = "💤 Я сейчас afk"
    if reason and reason != "1":
        txt += f": {reason}"
    try:
        await e.reply(txt)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────
#  ХЕЛПЕРЫ
# ─────────────────────────────────────────────────────────
async def get_target_text(event, args):
    """Текст из аргументов команды, либо из отвеченного сообщения."""
    if args.strip():
        return args
    reply = await event.get_reply_message()
    if reply and reply.message:
        return reply.message
    return ""

# ── справка ───────────────────────────────────────────────
HELP_TEXT = """✨ **Karzen UserBot** ✨

▫️ **Текст / моды**
`.mode <kawaii|tsundere|yandere|leet|off>` — словесный мод (авто-стиль на всё)
`.kawaii` `.tsundere` `.yandere` `.leet` — разово
`.bold` `.italic` `.mono` `.under` — форматирование
`.sw` — сменить раскладку (ответом)
`.type <текст>` — печатает и отправляет

▫️ **Совместный мод** 🤝
`.pair <мод>` — предложить собеседнику общий мод
`.accept` / `.deny` — ответ на приглашение
`.unpair` — выключить у обоих
`.pairs` — список активных пар

▫️ **Утилиты**
`.burn` — сохранить сгорающее медиа (ответом)
`.afk [причина]` / `.unafk`
`.time [off]` — время в имя
`.status <текст>` / `.status off`
`.mute` / `.unmute` — этот чат
`.id` — инфо (ответом или тут)

▫️ **Приколы / медиа**
`.love` — ❤️
`.flip` `.dice`
`.nk` — неко-тян
`.lq` — зашакалить фото (ответом)
`.gif` — видео/фото → gif (ответом)
`.fv` — усилить голосовое (ответом)
`.story` — фото → сторис 9:16 (ответом)

🗑 Анти-делит работает всегда → удалённое летит в Избранное.
"""

@cmd("help", "h")
async def _help(e, args):
    await e.edit(HELP_TEXT)

# ── моды ──────────────────────────────────────────────────
@cmd("mode")
async def _mode(e, args):
    a = args.strip().lower()
    if a in ("off", "0", "none", ""):
        STATE["mode"] = None
        save_state()
        return await e.edit("🔕 Словесный мод выключен.")
    if a not in MODES:
        return await e.edit(f"⚠️ Моды: {', '.join(MODES)} | off")
    STATE["mode"] = a
    save_state()
    await e.edit(f"✅ Словесный мод: **{a}** — теперь всё что пишу трансформится ♡")

@cmd("kawaii")
async def _kawaii(e, args):
    await e.edit(kawaii(await get_target_text(e, args)))

@cmd("tsundere")
async def _tsun(e, args):
    await e.edit(tsundere(await get_target_text(e, args)))

@cmd("yandere")
async def _yan(e, args):
    await e.edit(yandere(await get_target_text(e, args)))

@cmd("leet")
async def _leet(e, args):
    await e.edit(leet(await get_target_text(e, args)))

# ── форматирование (Markdown Telegram) ────────────────────
@cmd("bold", "b")
async def _bold(e, args):
    await e.edit(f"**{await get_target_text(e, args)}**")

@cmd("italic", "i")
async def _italic(e, args):
    await e.edit(f"__{await get_target_text(e, args)}__")

@cmd("mono", "monospace")
async def _mono(e, args):
    await e.edit(f"`{await get_target_text(e, args)}`")

@cmd("under", "underline")
async def _under(e, args):
    # подчёркнутый через html-entity
    txt = await get_target_text(e, args)
    await e.edit(f"<u>{txt}</u>", parse_mode="html")

@cmd("sw")
async def _sw(e, args):
    await e.edit(switch_layout(await get_target_text(e, args)))

@cmd("type")
async def _type(e, args):
    txt = args or "..."
    out = ""
    for ch in txt:
        out += ch
        try:
            await e.edit(out + "▌")
        except MessageNotModifiedError:
            pass
        await asyncio.sleep(0.06)
    await e.edit(out)

# ── afk / статус / время ──────────────────────────────────
@cmd("afk")
async def _afk(e, args):
    STATE["afk"] = args.strip() or "1"
    save_state()
    await e.edit("💤 AFK включён." + (f" Причина: {args}" if args.strip() else ""))

@cmd("unafk")
async def _unafk(e, args):
    STATE["afk"] = None
    save_state()
    await e.edit("🌞 С возвращением!")

async def _set_last_name(suffix):
    me = await client.get_me()
    if STATE.get("orig_last_name") is None:
        STATE["orig_last_name"] = me.last_name or ""
        save_state()
    base = STATE["orig_last_name"]
    new_last = f"{base} {suffix}".strip() if suffix else base
    await client(functions.account.UpdateProfileRequest(last_name=new_last[:64]))

@cmd("time")
async def _time(e, args):
    if args.strip().lower() == "off":
        await _set_last_name("")
        return await e.edit("⏰ Убрал время из имени.")
    now = datetime.now().strftime("%H:%M")
    await _set_last_name(f"| {now}")
    await e.edit(f"⏰ Поставил {now} в имя.")

@cmd("status")
async def _status(e, args):
    if args.strip().lower() == "off" or not args.strip():
        await _set_last_name("")
        return await e.edit("🧹 Статус убран.")
    await _set_last_name(f"| {args.strip()}")
    await e.edit("✅ Статус в имени обновлён.")

# ── мут (своя сторона) ────────────────────────────────────
@cmd("mute")
async def _mute(e, args):
    await client(functions.account.UpdateNotifySettingsRequest(
        peer=await e.get_input_chat(),
        settings=types.InputPeerNotifySettings(mute_until=2**31 - 1)))
    await e.edit("🔇 Чат замучен (у тебя).")

@cmd("unmute")
async def _unmute(e, args):
    await client(functions.account.UpdateNotifySettingsRequest(
        peer=await e.get_input_chat(),
        settings=types.InputPeerNotifySettings(mute_until=0)))
    await e.edit("🔊 Мут снят.")

@cmd("id", "info")
async def _id(e, args):
    reply = await e.get_reply_message()
    ent = await reply.get_sender() if reply else await e.get_me()
    uname = f"@{ent.username}" if getattr(ent, "username", None) else "—"
    await e.edit(
        f"👤 **{getattr(ent,'first_name','')} {getattr(ent,'last_name','') or ''}**\n"
        f"🆔 `{ent.id}`\n🔗 {uname}"
    )

# ── приколы ───────────────────────────────────────────────
@cmd("burn")
async def _burn(e, args):
    """Сохранить сгорающее медиа из отвеченного сообщения."""
    reply = await e.get_reply_message()
    if not reply:
        return await e.edit("↩️ Ответь на сгорающее сообщение.")

    class _E:
        message = reply
    ok = await save_burning(_E())
    await e.edit("🔥 Сохранено в Избранное" if ok
                 else "🤷 Это не сгорающее медиа (или уже просмотрено).")


@cmd("love")
async def _love(e, args):
    hearts = "❤️🧡💛💚💙💜🤍💗💓💞"
    await e.edit(" ".join(random.sample(hearts, len(hearts))))

@cmd("flip")
async def _flip(e, args):
    await e.edit("🪙 " + random.choice(["Орёл", "Решка"]))

@cmd("dice")
async def _dice(e, args):
    await e.edit(f"🎲 Выпало: **{random.randint(1,6)}**")

@cmd("nk")
async def _nk(e, args):
    # неко-тян с публичного API
    try:
        import urllib.request
        with urllib.request.urlopen("https://nekos.best/api/v2/neko") as r:
            url = json.loads(r.read())["results"][0]["url"]
        await e.delete()
        await client.send_file(e.chat_id, url, caption="ня~ ♡")
    except Exception as ex:
        await e.edit(f"⚠️ Неко сбежала: {ex}")

@cmd("lq")
async def _lq(e, args):
    # зашакалить фото (deep-fry через жёсткое JPEG-сжатие)
    reply = await e.get_reply_message()
    if not (reply and reply.photo):
        return await e.edit("↩️ Ответь на фото.")
    try:
        from PIL import Image, ImageEnhance
        data = await reply.download_media(bytes)
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img = ImageEnhance.Contrast(img).enhance(3.0)
        img = ImageEnhance.Color(img).enhance(4.0)
        for _ in range(6):
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=random.randint(4, 12))
            buf.seek(0)
            img = Image.open(buf).convert("RGB")
        out = io.BytesIO(); out.name = "shakal.jpg"
        img.save(out, "JPEG", quality=8); out.seek(0)
        await e.delete()
        await client.send_file(e.chat_id, out, caption="🐺 зашакалено")
    except Exception as ex:
        await e.edit(f"⚠️ {ex}")

@cmd("gif")
async def _gif(e, args):
    reply = await e.get_reply_message()
    if not (reply and (reply.video or reply.photo)):
        return await e.edit("↩️ Ответь на видео/фото.")
    await e.edit("⏳ Делаю gif...")
    src = await reply.download_media(tempfile.mktemp())
    dst = tempfile.mktemp(suffix=".gif")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vf",
             "fps=15,scale=320:-1:flags=lanczos", dst],
            check=True, capture_output=True)
        await e.delete()
        await client.send_file(e.chat_id, dst)
    except Exception as ex:
        await e.edit(f"⚠️ ffmpeg: {ex}")
    finally:
        for f in (src, dst):
            try: os.remove(f)
            except Exception: pass

@cmd("fv")
async def _fv(e, args):
    reply = await e.get_reply_message()
    if not (reply and reply.voice):
        return await e.edit("↩️ Ответь на голосовое.")
    await e.edit("🔊 Усиливаю...")
    src = await reply.download_media(tempfile.mktemp(suffix=".ogg"))
    dst = tempfile.mktemp(suffix=".ogg")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-af", "volume=8dB,acompressor",
             "-c:a", "libopus", dst],
            check=True, capture_output=True)
        await e.delete()
        await client.send_file(e.chat_id, dst, voice_note=True)
    except Exception as ex:
        await e.edit(f"⚠️ ffmpeg: {ex}")
    finally:
        for f in (src, dst):
            try: os.remove(f)
            except Exception: pass

@cmd("story")
async def _story(e, args):
    reply = await e.get_reply_message()
    if not (reply and reply.photo):
        return await e.edit("↩️ Ответь на фото.")
    try:
        from PIL import Image
        data = await reply.download_media(bytes)
        img = Image.open(io.BytesIO(data)).convert("RGB")
        W, H = img.size
        # режем по вертикали на куски 9:16
        piece_w = int(H * 9 / 16)
        pieces = []
        x = 0
        while x < W:
            crop = img.crop((x, 0, min(x + piece_w, W), H))
            buf = io.BytesIO(); buf.name = "story.jpg"
            crop.save(buf, "JPEG", quality=90); buf.seek(0)
            pieces.append(buf)
            x += piece_w
        await e.delete()
        await client.send_file(e.chat_id, pieces, caption="🖼 нарезка на сторис")
    except Exception as ex:
        await e.edit(f"⚠️ {ex}")

# ── заглушки (нужны внешние API/кнопки — допилишь под себя) ─
@cmd("short")
async def _short(e, args):
    await e.edit("🧩 TODO: подключи LLM (напр. Anthropic API) для пересказа диалога.")

@cmd("yars")
async def _yars(e, args):
    await e.edit("🧩 TODO: подключи API реверс-поиска по фото (Yandex/Google).")

@cmd("check")
async def _check(e, args):
    await e.edit("🧩 TODO: опиши, что должен анализировать .check — допилю.")

@cmd("ttt", "chk", "duel", "bw")
async def _games(e, args):
    await e.edit("🎮 Игры с кнопками юзерботу недоступны (нет inline-клавиатур). "
                 "Их лучше вынести в обычного бота с bot-токеном.")

# ─────────────────────────────────────────────────────────
#  ГЛАВНЫЙ ОБРАБОТЧИК ИСХОДЯЩИХ (команды + словесный мод)
# ─────────────────────────────────────────────────────────
@client.on(events.NewMessage(outgoing=True))
async def _dispatch(e):
    text = e.message.message or ""

    # 0) наше же протокольное сообщение — не трогаем
    if has_payload(text):
        return

    # 1) команда?
    if text.startswith(PREFIX):
        parts = text[len(PREFIX):].split(maxsplit=1)
        if not parts:
            return
        name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        fn = COMMANDS.get(name)
        if fn:
            try:
                await fn(e, args)
            except Exception as ex:
                try:
                    await e.edit(f"💥 Ошибка `{name}`: {ex}")
                except Exception:
                    pass
        return

    # 2) активен словесный мод -> трансформим обычное сообщение
    #    приоритет: совместный мод этого чата > глобальный
    mode = chat_mode(e.chat_id) or STATE.get("mode")
    if mode and mode in MODES and text.strip() and not e.message.media:
        try:
            await e.edit(MODES[mode](text))
        except (MessageNotModifiedError, Exception):
            pass

# ─────────────────────────────────────────────────────────
def main():
    print("♡ Karzen UserBot запускается...")
    with client:
        me = client.loop.run_until_complete(client.get_me())
        print(f"✅ Залогинен как {me.first_name} (@{me.username}) | id {me.id}")
        print("Пиши команды с префиксом '.' себе в любой чат. .help — список.")
        client.run_until_disconnected()

if __name__ == "__main__":
    main()
