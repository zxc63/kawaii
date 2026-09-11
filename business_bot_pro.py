#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
  Karzen Business Bot  ♡  PRO
  aiogram 3 · Bot API 9.x · Telegram Business
=========================================================
Подключение: Telegram → Настройки → Telegram для бизнеса
             → Чат-боты → выбрать этого бота

Права при подключении (важно!):
  ✅ Отвечать на сообщения
  ✅ Читать сообщения
  ✅ Удалять отправленные сообщения   ← без этого моды не работают

Возможности:
  🗑  анти-делит (сохраняет удалённые сообщения)
  🌸  словесные моды: kawaii / tsundere / yandere / leet
  🤝  совместный мод с подтверждением кнопкой
  🎬  медиа: .gif .fv .lq .story .nk
  ⌨️  .type, .sw, .flip, .dice, .love
  💎  подписки (free / premium) + админ-панель и рассылка

Зависимости:
    pip install aiogram pillow aiohttp
    + ffmpeg в системе
=========================================================
"""

import asyncio
import io
import json
import logging
import os
import random
import re
import subprocess
import tempfile
import time
from collections import OrderedDict
from datetime import datetime, timedelta

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    BusinessConnection,
    BusinessMessagesDeleted,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

# ═════════════════════════════════════════════════════════
#  КОНФИГ
# ═════════════════════════════════════════════════════════
BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN"
ADMINS = [123456789]            # <-- твой telegram id
PREFIX = "."
STATE_FILE = "bizbot_state.json"
CACHE_LIMIT = 8000
TRIAL_DAYS = 3                  # премиум-триал новым юзерам (0 = выключить)

# что доступно только по подписке
PREMIUM_FEATURES = {"tsundere", "yandere", "pair", "gif", "fv", "story", "lq"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ═════════════════════════════════════════════════════════
#  ХРАНИЛИЩЕ
# ═════════════════════════════════════════════════════════
USER_TPL = {
    "name": "", "username": "", "first_seen": 0, "last_seen": 0,
    "premium_until": 0, "banned": False, "cmds": 0,
    "mode": None, "antidelete": True, "pairs": {},
}


def load():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as ex:
            logging.error("state load: %s", ex)
    return {"connections": {}, "users": {}}


S = load()
S.setdefault("connections", {})
S.setdefault("users", {})
_dirty = False


def save():
    global _dirty
    _dirty = True


async def autosave_loop():
    global _dirty
    while True:
        await asyncio.sleep(5)
        if _dirty:
            _dirty = False
            try:
                tmp = STATE_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(S, f, ensure_ascii=False, indent=2)
                os.replace(tmp, STATE_FILE)
            except Exception as ex:
                logging.error("state save: %s", ex)


def user(uid, tg=None) -> dict:
    u = S["users"].setdefault(str(uid), dict(USER_TPL))
    for k, v in USER_TPL.items():
        u.setdefault(k, v if not isinstance(v, dict) else {})
    if not u["first_seen"]:
        u["first_seen"] = int(time.time())
        if TRIAL_DAYS:
            u["premium_until"] = int(time.time()) + TRIAL_DAYS * 86400
    if tg:
        u["name"] = tg.full_name
        u["username"] = tg.username or ""
    u["last_seen"] = int(time.time())
    save()
    return u


def conn_of(uid):
    return S["connections"].get(str(uid))


def is_premium(uid) -> bool:
    return user(uid)["premium_until"] > time.time()


def premium_left(uid) -> str:
    u = user(uid)
    if u["premium_until"] <= time.time():
        return "нет"
    d = (u["premium_until"] - time.time()) / 86400
    return f"{d:.1f} дн."


def gate(uid, feature) -> bool:
    """True если фича доступна."""
    return feature not in PREMIUM_FEATURES or is_premium(uid)


# ═════════════════════════════════════════════════════════
#  СТИЛИ ТЕКСТА
# ═════════════════════════════════════════════════════════
KAOMOJI = ["(◕‿◕✿)", "(｡♥‿♥｡)", "(*≧ω≦*)", "ʕ•ᴥ•ʔ", "(っ◔◡◔)っ ♡", "(˘⌣˘)♡",
           "ヽ(=^･ω･^=)丿", "(´｡• ᵕ •｡`)", "(⁄ ⁄•⁄ω⁄•⁄ ⁄)", "(◍•ᴗ•◍)♡", "(*ฅ́˘ฅ̀*)♡"]
NYA = ["ня", "ня~", "мур", "мурр~", "уву", "мяу", "нявушки"]


def kawaii(t: str) -> str:
    t = t.replace("!", "! ♡").replace("?", "? >_<")
    t = t.replace("л", "ль").replace("р", "рь")
    t = re.sub(r"\bда\b", "дя", t, flags=re.I)
    return f"{t} {random.choice(NYA)}~ {random.choice(KAOMOJI)}"


def tsundere(t: str) -> str:
    pre = random.choice(["Н-не то чтобы я специально, но ", "Х-хмф! ",
                         "Б-бака! ", "Не думай ничего такого, но "])
    post = random.choice([" ...и вообще, мне всё равно! (>﹏<)", " ...б-бака.",
                          " ...не благодари, ясно?! (๑•̀ㅁ•́๑)", " Хмф! (￣^￣)"])
    return f"{pre}{t.lower()}{post}"


def yandere(t: str) -> str:
    return t + random.choice([" Ты ведь только мой, да?~ 🔪♡",
                              " Я никому тебя не отдам... никогда~ (◡‿◡✿)",
                              " Не смотри на других, хорошо?~ ♡"])


def leet(t: str) -> str:
    return t.translate(str.maketrans({"a": "4", "e": "3", "i": "1", "o": "0",
                                      "s": "5", "t": "7", "а": "@", "е": "3",
                                      "о": "0", "и": "1"}))


MODES = {"kawaii": kawaii, "tsundere": tsundere, "yandere": yandere, "leet": leet}

RU = "йцукенгшщзхъфывапролджэячсмитьбю."
EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,./"


def switch_layout(t: str) -> str:
    ru2en, en2ru = dict(zip(RU, EN)), dict(zip(EN, RU))
    tbl = ru2en if sum(c.lower() in ru2en for c in t) >= sum(c.lower() in en2ru for c in t) else en2ru
    return "".join(tbl.get(c, tbl.get(c.lower(), c)) for c in t)


# ═════════════════════════════════════════════════════════
#  ПОДКЛЮЧЕНИЕ БИЗНЕС-АККАУНТА
# ═════════════════════════════════════════════════════════
@dp.business_connection()
async def on_connect(bc: BusinessConnection):
    uid = bc.user.id
    user(uid, bc.user)
    if bc.is_enabled:
        S["connections"][str(uid)] = {"cid": bc.id, "chat": bc.user_chat_id}
        save()
        missing = []
        r = bc.rights
        if r:
            if not r.can_reply:
                missing.append("отвечать на сообщения")
            if not (r.can_delete_sent_messages or r.can_delete_outgoing_messages):
                missing.append("удалять отправленные сообщения")
            if not r.can_read_messages:
                missing.append("читать сообщения")
        warn = ("\n\n⚠️ <b>Не хватает прав:</b> " + ", ".join(missing) +
                "\nБез них часть функций не сработает.") if missing else ""
        await bot.send_message(bc.user_chat_id,
                               "✅ Подключено! Жми /start — там меню." + warn)
    else:
        S["connections"].pop(str(uid), None)
        save()


# ═════════════════════════════════════════════════════════
#  АНТИ-ДЕЛИТ
# ═════════════════════════════════════════════════════════
cache: "OrderedDict[tuple, dict]" = OrderedDict()


def cache_put(m: Message, owner_id: int):
    cache[(m.chat.id, m.message_id)] = {
        "owner": owner_id,
        "text": m.text or m.caption or "",
        "from": m.from_user.full_name if m.from_user else "?",
        "from_id": m.from_user.id if m.from_user else 0,
        "chat": m.chat.id, "mid": m.message_id,
        "media": bool(m.photo or m.video or m.voice or m.video_note
                      or m.document or m.sticker or m.animation),
    }
    while len(cache) > CACHE_LIMIT:
        cache.popitem(last=False)


def owner_by_cid(cid):
    for uid, c in S["connections"].items():
        if c["cid"] == cid:
            return int(uid), c
    return None, None


@dp.deleted_business_messages()
async def on_deleted(ev: BusinessMessagesDeleted):
    owner_id, c = owner_by_cid(ev.business_connection_id)
    if owner_id is None or not user(owner_id)["antidelete"]:
        return
    for mid in ev.message_ids:
        info = cache.pop((ev.chat.id, mid), None)
        if not info:
            continue
        head = (f"🗑 <b>Удалённое сообщение</b>\n"
                f"👤 {info['from']} (<code>{info['from_id']}</code>)")
        try:
            if info["media"]:
                try:
                    await bot.forward_message(c["chat"], info["chat"], info["mid"])
                except Exception:
                    pass
            body = f"\n\n💬 {info['text']}" if info["text"] else "\n\n📎 (медиа)"
            await bot.send_message(c["chat"], head + body)
        except Exception as ex:
            logging.warning("antidelete: %s", ex)


# ═════════════════════════════════════════════════════════
#  РОУТЕР БИЗНЕС-СООБЩЕНИЙ
# ═════════════════════════════════════════════════════════
@dp.business_message()
async def on_business_message(m: Message):
    owner_id, _ = owner_by_cid(m.business_connection_id)
    if owner_id is None or m.sender_business_bot is not None:
        return
    cache_put(m, owner_id)

    if not (m.from_user and m.from_user.id == owner_id):
        return
    u = user(owner_id, m.from_user)
    if u["banned"]:
        return

    text = m.text or ""
    if text.startswith(PREFIX):
        u["cmds"] += 1
        save()
        await handle_cmd(m, owner_id, text[len(PREFIX):])
        return

    mode = u["pairs"].get(str(m.chat.id)) or u["mode"]
    if mode and mode in MODES and text.strip() and gate(owner_id, mode):
        await replace_with(m, MODES[mode](text))


async def replace_with(m: Message, new_text: str):
    """Удалить оригинал владельца и отправить обработанный текст от его имени."""
    if not await drop(m):
        return
    try:
        await bot.send_message(m.chat.id, new_text,
                               business_connection_id=m.business_connection_id)
    except Exception as ex:
        logging.warning("send: %s", ex)


async def drop(m: Message) -> bool:
    try:
        await bot.delete_business_messages(
            business_connection_id=m.business_connection_id,
            message_ids=[m.message_id])
        return True
    except Exception as ex:
        logging.warning("delete (нет прав?): %s", ex)
        return False


async def dm(uid, text, **kw):
    c = conn_of(uid)
    if c:
        try:
            await bot.send_message(c["chat"], text, **kw)
        except Exception:
            pass


# ═════════════════════════════════════════════════════════
#  МЕДИА-УТИЛИТЫ
# ═════════════════════════════════════════════════════════
async def dl(file_id) -> bytes:
    buf = await bot.download(file_id)
    return buf.read()


def ff(args, src_bytes, in_ext, out_ext) -> bytes:
    """Прогнать байты через ffmpeg."""
    src = tempfile.mktemp(suffix=in_ext)
    dst = tempfile.mktemp(suffix=out_ext)
    try:
        with open(src, "wb") as f:
            f.write(src_bytes)
        subprocess.run(["ffmpeg", "-y", "-i", src] + args + [dst],
                       check=True, capture_output=True, timeout=120)
        with open(dst, "rb") as f:
            return f.read()
    finally:
        for p in (src, dst):
            try:
                os.remove(p)
            except OSError:
                pass


def deepfry(data: bytes) -> bytes:
    from PIL import Image, ImageEnhance
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = ImageEnhance.Contrast(img).enhance(3.0)
    img = ImageEnhance.Color(img).enhance(4.0)
    for _ in range(6):
        b = io.BytesIO()
        img.save(b, "JPEG", quality=random.randint(4, 12))
        b.seek(0)
        img = Image.open(b).convert("RGB")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=8)
    return out.getvalue()


def to_stories(data: bytes):
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGB")
    W, H = img.size
    step = max(1, int(H * 9 / 16))
    parts = []
    x = 0
    while x < W and len(parts) < 10:
        buf = io.BytesIO()
        img.crop((x, 0, min(x + step, W), H)).save(buf, "JPEG", quality=92)
        parts.append(buf.getvalue())
        x += step
    return parts


# ═════════════════════════════════════════════════════════
#  КОМАНДЫ (префикс ".", пишешь сам в любом чате)
# ═════════════════════════════════════════════════════════
CMD_HELP = """✨ <b>Команды</b> (префикс <code>.</code>)

<b>Моды</b>
<code>.mode kawaii|tsundere|yandere|leet|off</code>
<code>.kawaii</code> <code>.tsundere</code>💎 <code>.yandere</code>💎 <code>.leet</code>

<b>Совместный мод</b> 💎
<code>.pair kawaii</code> — предложить общий стиль
<code>.unpair</code> · <code>.pairs</code>

<b>Медиа</b> (ответом на сообщение)
<code>.gif</code>💎 — видео → GIF
<code>.fv</code>💎 — усилить голосовое
<code>.lq</code>💎 — зашакалить фото
<code>.story</code>💎 — фото → нарезка 9:16
<code>.nk</code> — неко-тян

<b>Прочее</b>
<code>.type текст</code> · <code>.sw</code> · <code>.flip</code> · <code>.dice</code> · <code>.love</code>
<code>.ad</code> — анти-делит вкл/выкл
<code>.me</code> — статус подписки

💎 — только для премиума
"""


async def handle_cmd(m: Message, uid: int, raw: str):
    parts = raw.split(maxsplit=1)
    name = (parts[0].lower() if parts else "")
    args = parts[1] if len(parts) > 1 else ""
    u = user(uid)
    cid = m.business_connection_id
    peer = str(m.chat.id)
    rep = m.reply_to_message

    async def out(t):
        await replace_with(m, t)

    async def note(t):
        await drop(m)
        await dm(uid, t)

    if not gate(uid, name):
        return await note(f"💎 Команда <code>.{name}</code> доступна по подписке.\n"
                          f"Твой премиум: <b>{premium_left(uid)}</b>\n/buy — оформить")

    # ── справка / статус ──
    if name in ("help", "h"):
        return await note(CMD_HELP)
    if name == "me":
        return await note(f"👤 <b>{u['name']}</b>\n💎 Премиум: <b>{premium_left(uid)}</b>\n"
                          f"🌸 Мод: <b>{u['mode'] or 'выкл'}</b>\n"
                          f"🗑 Анти-делит: {'вкл' if u['antidelete'] else 'выкл'}\n"
                          f"🤝 Пар: {len(u['pairs'])}")

    # ── моды ──
    if name == "mode":
        a = args.strip().lower()
        if a in ("off", "", "none"):
            u["mode"] = None
            save()
            return await note("🔕 Мод выключен.")
        if a not in MODES:
            return await note("⚠️ Моды: " + ", ".join(MODES))
        if not gate(uid, a):
            return await note(f"💎 Мод <b>{a}</b> — премиум. /buy")
        u["mode"] = a
        save()
        return await note(f"✅ Мод: <b>{a}</b> ♡")

    if name in MODES:
        return await out(MODES[name](args or (rep.text if rep and rep.text else " ")))

    if name == "sw":
        return await out(switch_layout(args or (rep.text if rep else "")))

    if name == "type":
        txt = args or "..."
        await drop(m)
        try:
            sent = await bot.send_message(m.chat.id, "▌", business_connection_id=cid)
            acc = ""
            for ch in txt:
                acc += ch
                try:
                    await bot.edit_message_text(acc + "▌", chat_id=m.chat.id,
                                                message_id=sent.message_id,
                                                business_connection_id=cid)
                except Exception:
                    pass
                await asyncio.sleep(0.07)
            await bot.edit_message_text(acc, chat_id=m.chat.id,
                                        message_id=sent.message_id,
                                        business_connection_id=cid)
        except Exception as ex:
            await dm(uid, f"⚠️ .type: {ex}")
        return

    if name == "flip":
        return await out("🪙 " + random.choice(["Орёл", "Решка"]))
    if name == "dice":
        return await out(f"🎲 {random.randint(1, 6)}")
    if name == "love":
        return await out(" ".join(random.sample("❤️🧡💛💚💙💜🤍💗", 8)))
    if name == "ad":
        u["antidelete"] = not u["antidelete"]
        save()
        return await note(f"🗑 Анти-делит: {'вкл ✅' if u['antidelete'] else 'выкл ❌'}")

    # ── медиа ──
    if name == "nk":
        await drop(m)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://nekos.best/api/v2/neko") as r:
                    url = (await r.json())["results"][0]["url"]
                async with s.get(url) as r:
                    img = await r.read()
            await bot.send_photo(m.chat.id, BufferedInputFile(img, "neko.png"),
                                 caption="ня~ ♡", business_connection_id=cid)
        except Exception as ex:
            await dm(uid, f"⚠️ .nk: {ex}")
        return

    if name in ("gif", "fv", "lq", "story"):
        if not rep:
            return await note(f"↩️ Ответь на сообщение командой <code>.{name}</code>")
        await drop(m)
        try:
            if name == "gif":
                src = rep.video or rep.animation or rep.video_note
                if not src:
                    return await dm(uid, "⚠️ Нужно видео.")
                data = await dl(src.file_id)
                gif = ff(["-vf", "fps=15,scale=320:-1:flags=lanczos"], data, ".mp4", ".gif")
                await bot.send_animation(m.chat.id, BufferedInputFile(gif, "a.gif"),
                                         business_connection_id=cid)

            elif name == "fv":
                if not rep.voice:
                    return await dm(uid, "⚠️ Нужно голосовое.")
                data = await dl(rep.voice.file_id)
                ogg = ff(["-af", "volume=8dB,acompressor", "-c:a", "libopus"],
                         data, ".ogg", ".ogg")
                await bot.send_voice(m.chat.id, BufferedInputFile(ogg, "v.ogg"),
                                     business_connection_id=cid)

            elif name == "lq":
                if not rep.photo:
                    return await dm(uid, "⚠️ Нужно фото.")
                data = await dl(rep.photo[-1].file_id)
                await bot.send_photo(m.chat.id,
                                     BufferedInputFile(deepfry(data), "s.jpg"),
                                     caption="🐺 зашакалено", business_connection_id=cid)

            elif name == "story":
                if not rep.photo:
                    return await dm(uid, "⚠️ Нужно фото.")
                data = await dl(rep.photo[-1].file_id)
                parts = to_stories(data)
                media = [InputMediaPhoto(media=BufferedInputFile(p, f"s{i}.jpg"))
                         for i, p in enumerate(parts)]
                if len(media) == 1:
                    await bot.send_photo(m.chat.id, media[0].media,
                                         business_connection_id=cid)
                else:
                    await bot.send_media_group(m.chat.id, media,
                                               business_connection_id=cid)
        except subprocess.CalledProcessError as ex:
            await dm(uid, f"⚠️ ffmpeg упал: {ex.stderr[-300:].decode(errors='ignore')}")
        except Exception as ex:
            await dm(uid, f"⚠️ .{name}: {ex}")
        return

    # ── совместный мод ──
    if name == "pair":
        mode = args.strip().lower()
        if mode not in MODES:
            return await note("⚠️ <code>.pair kawaii|tsundere|yandere|leet</code>")
        if m.chat.type != "private":
            return await note("⚠️ Только в личных диалогах.")
        if not conn_of(m.chat.id):
            return await note("😕 У собеседника не подключён бот — пары не будет.")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Принять", callback_data=f"pair:ok:{uid}:{mode}"),
            InlineKeyboardButton(text="🚫 Отклонить", callback_data=f"pair:no:{uid}:{mode}")]])
        await drop(m)
        await dm(m.chat.id, f"🤝 <b>{u['name']}</b> предлагает совместный мод "
                            f"<b>{mode}</b>.\nТвои сообщения в этом диалоге "
                            f"тоже будут в этом стиле.", reply_markup=kb)
        return await dm(uid, f"📨 Приглашение отправлено ({mode}).")

    if name == "unpair":
        had = u["pairs"].pop(peer, None)
        pu = user(m.chat.id)
        if pu["pairs"].pop(str(uid), None):
            await dm(m.chat.id, "👋 Собеседник выключил совместный мод.")
        save()
        return await note("👋 Совместный мод выключен." if had else "🤷 Пары не было.")

    if name == "pairs":
        if not u["pairs"]:
            return await note("📭 Пар нет.")
        return await note("🤝 <b>Пары:</b>\n" + "\n".join(
            f"• <code>{k}</code> — <b>{v}</b>" for k, v in u["pairs"].items()))


@dp.callback_query(F.data.startswith("pair:"))
async def on_pair(cb: CallbackQuery):
    _, verdict, initiator, mode = cb.data.split(":")
    initiator, responder = int(initiator), cb.from_user.id
    if verdict == "no":
        await cb.message.edit_text("🚫 Отклонено.")
        await dm(initiator, "🚫 Собеседник отклонил совместный мод.")
        return await cb.answer()
    user(responder)["pairs"][str(initiator)] = mode
    user(initiator)["pairs"][str(responder)] = mode
    save()
    await cb.message.edit_text(f"🤝 Совместный мод <b>{mode}</b> включён ♡")
    await dm(initiator, f"✅ Согласие получено! Мод <b>{mode}</b> активен ♡")
    await cb.answer("Готово!")


# ═════════════════════════════════════════════════════════
#  МЕНЮ ПОЛЬЗОВАТЕЛЯ
# ═════════════════════════════════════════════════════════
def main_kb(uid) -> InlineKeyboardMarkup:
    u = user(uid)
    rows = [
        [InlineKeyboardButton(text=f"🌸 Мод: {u['mode'] or 'выкл'}", callback_data="menu:mode")],
        [InlineKeyboardButton(text=f"{'🟢' if u['antidelete'] else '🔴'} Анти-делит",
                              callback_data="menu:ad")],
        [InlineKeyboardButton(text=f"💎 Подписка: {premium_left(uid)}", callback_data="menu:buy")],
        [InlineKeyboardButton(text="❓ Команды", callback_data="menu:help")],
    ]
    if uid in ADMINS:
        rows.append([InlineKeyboardButton(text="🛠 Админка", callback_data="adm:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def start(m: Message):
    user(m.from_user.id, m.from_user)
    ok = conn_of(m.from_user.id) is not None
    st = ("✅ подключён" if ok else
          "⚠️ не подключён\nНастройки → Telegram для бизнеса → Чат-боты → выбери меня")
    await m.answer(f"♡ <b>Karzen Bot</b>\n\nСтатус: {st}", reply_markup=main_kb(m.from_user.id))


@dp.message(Command("buy"))
async def buy(m: Message):
    await m.answer("💎 <b>Премиум</b>\n\nОткрывает: tsundere/yandere моды, совместный мод, "
                   ".gif .fv .lq .story\n\n<i>Тут подключи оплату — Telegram Stars "
                   "(sendInvoice с XTR) или ручную выдачу через админку.</i>")


@dp.callback_query(F.data.startswith("menu:"))
async def menu(cb: CallbackQuery):
    what = cb.data.split(":")[1]
    uid = cb.from_user.id
    u = user(uid, cb.from_user)
    back = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="‹ Назад", callback_data="menu:root")]])
    if what == "mode":
        rows = [[InlineKeyboardButton(
            text=("💎 " if m_ in PREMIUM_FEATURES else "") + m_,
            callback_data=f"setmode:{m_}")] for m_ in MODES]
        rows += [[InlineKeyboardButton(text="🔕 Выключить", callback_data="setmode:off")],
                 [InlineKeyboardButton(text="‹ Назад", callback_data="menu:root")]]
        await cb.message.edit_text("🌸 Выбери стиль:",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif what == "ad":
        u["antidelete"] = not u["antidelete"]
        save()
        await cb.message.edit_reply_markup(reply_markup=main_kb(uid))
    elif what == "help":
        await cb.message.edit_text(CMD_HELP, reply_markup=back)
    elif what == "buy":
        await cb.message.edit_text(
            f"💎 Премиум: <b>{premium_left(uid)}</b>\n\n"
            "Открывает tsundere/yandere, совместный мод и все медиа-команды.",
            reply_markup=back)
    else:
        await cb.message.edit_text("♡ <b>Karzen Bot</b>", reply_markup=main_kb(uid))
    await cb.answer()


@dp.callback_query(F.data.startswith("setmode:"))
async def setmode(cb: CallbackQuery):
    m_ = cb.data.split(":")[1]
    uid = cb.from_user.id
    if m_ != "off" and not gate(uid, m_):
        return await cb.answer("💎 Только для премиума", show_alert=True)
    user(uid)["mode"] = None if m_ == "off" else m_
    save()
    await cb.message.edit_text("♡ <b>Karzen Bot</b>", reply_markup=main_kb(uid))
    await cb.answer("Сохранено")


# ═════════════════════════════════════════════════════════
#  АДМИНКА / МЕНЕДЖМЕНТ ЮЗЕРОВ
# ═════════════════════════════════════════════════════════
def is_admin(uid) -> bool:
    return uid in ADMINS


def stats_text() -> str:
    us = S["users"]
    now = time.time()
    total = len(us)
    prem = sum(1 for u in us.values() if u.get("premium_until", 0) > now)
    conn = len(S["connections"])
    ban = sum(1 for u in us.values() if u.get("banned"))
    day = sum(1 for u in us.values() if u.get("last_seen", 0) > now - 86400)
    week = sum(1 for u in us.values() if u.get("first_seen", 0) > now - 7 * 86400)
    cmds = sum(u.get("cmds", 0) for u in us.values())
    return (f"📊 <b>Статистика</b>\n\n"
            f"👥 Всего: <b>{total}</b>\n"
            f"🔌 Подключено: <b>{conn}</b>\n"
            f"💎 Премиум: <b>{prem}</b>\n"
            f"🆕 За неделю: <b>{week}</b>\n"
            f"🔥 Активны за 24ч: <b>{day}</b>\n"
            f"⛔ Забанено: <b>{ban}</b>\n"
            f"⌨️ Команд выполнено: <b>{cmds}</b>")


def users_page(page=0, per=8) -> tuple:
    items = sorted(S["users"].items(), key=lambda kv: -kv[1].get("last_seen", 0))
    pages = max(1, (len(items) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    chunk = items[page * per:(page + 1) * per]
    lines = []
    for uid, u in chunk:
        badge = "💎" if u.get("premium_until", 0) > time.time() else "▫️"
        if u.get("banned"):
            badge = "⛔"
        conn = "🔌" if conn_of(uid) else "  "
        un = f"@{u['username']}" if u.get("username") else "—"
        lines.append(f"{badge}{conn} <code>{uid}</code> · {u.get('name','?')[:18]} · {un}")
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="‹", callback_data=f"adm:users:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="adm:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="›", callback_data=f"adm:users:{page+1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        nav, [InlineKeyboardButton(text="‹ Админка", callback_data="adm:root")]])
    body = "\n".join(lines) or "пусто"
    return (f"👥 <b>Пользователи</b>\n\n{body}\n\n"
            f"<i>Управление: /grant id дни · /revoke id · /ban id · /unban id · /u id</i>"), kb


ADMIN_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
    [InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:users:0")],
    [InlineKeyboardButton(text="📢 Рассылка (/bc текст)", callback_data="adm:noop")],
    [InlineKeyboardButton(text="‹ Назад", callback_data="menu:root")],
])


@dp.callback_query(F.data.startswith("adm:"))
async def admin_cb(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Не для тебя 🙃", show_alert=True)
    p = cb.data.split(":")
    if p[1] == "stats":
        await cb.message.edit_text(stats_text(), reply_markup=ADMIN_KB)
    elif p[1] == "users":
        txt, kb = users_page(int(p[2]) if len(p) > 2 else 0)
        await cb.message.edit_text(txt, reply_markup=kb)
    elif p[1] == "noop":
        pass
    else:
        await cb.message.edit_text("🛠 <b>Админка</b>", reply_markup=ADMIN_KB)
    await cb.answer()


@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    if is_admin(m.from_user.id):
        await m.answer(stats_text(), reply_markup=ADMIN_KB)


@dp.message(Command("u"))
async def cmd_user_info(m: Message):
    if not is_admin(m.from_user.id):
        return
    a = m.text.split()
    if len(a) < 2:
        return await m.answer("Использование: <code>/u user_id</code>")
    u = S["users"].get(a[1])
    if not u:
        return await m.answer("Не найден.")
    fs = datetime.fromtimestamp(u["first_seen"]).strftime("%d.%m.%Y")
    ls = datetime.fromtimestamp(u["last_seen"]).strftime("%d.%m %H:%M")
    await m.answer(
        f"👤 <b>{u['name']}</b> (@{u['username'] or '—'})\n"
        f"🆔 <code>{a[1]}</code>\n"
        f"💎 Премиум: <b>{premium_left(int(a[1]))}</b>\n"
        f"🔌 Подключён: {'да' if conn_of(a[1]) else 'нет'}\n"
        f"⛔ Бан: {'да' if u['banned'] else 'нет'}\n"
        f"🌸 Мод: {u['mode'] or 'выкл'} · 🤝 пар: {len(u['pairs'])}\n"
        f"⌨️ Команд: {u['cmds']}\n"
        f"📅 С нами с {fs} · был(а) {ls}")


@dp.message(Command("grant"))
async def cmd_grant(m: Message):
    if not is_admin(m.from_user.id):
        return
    a = m.text.split()
    if len(a) < 3:
        return await m.answer("Использование: <code>/grant user_id дни</code>")
    uid, days = a[1], int(a[2])
    u = user(int(uid))
    base = max(u["premium_until"], time.time())
    u["premium_until"] = int(base + days * 86400)
    save()
    until = datetime.fromtimestamp(u["premium_until"]).strftime("%d.%m.%Y")
    await m.answer(f"✅ Выдал {days} дн. → <code>{uid}</code> (до {until})")
    await dm(int(uid), f"💎 Тебе выдан премиум на {days} дн. (до {until}) ♡")


@dp.message(Command("revoke"))
async def cmd_revoke(m: Message):
    if not is_admin(m.from_user.id):
        return
    a = m.text.split()
    if len(a) < 2:
        return await m.answer("Использование: <code>/revoke user_id</code>")
    user(int(a[1]))["premium_until"] = 0
    save()
    await m.answer(f"🚫 Премиум снят у <code>{a[1]}</code>")


@dp.message(Command("ban"))
async def cmd_ban(m: Message):
    if not is_admin(m.from_user.id):
        return
    a = m.text.split()
    if len(a) < 2:
        return await m.answer("Использование: <code>/ban user_id</code>")
    user(int(a[1]))["banned"] = True
    save()
    await m.answer(f"⛔ Забанен <code>{a[1]}</code>")


@dp.message(Command("unban"))
async def cmd_unban(m: Message):
    if not is_admin(m.from_user.id):
        return
    a = m.text.split()
    if len(a) < 2:
        return await m.answer("Использование: <code>/unban user_id</code>")
    user(int(a[1]))["banned"] = False
    save()
    await m.answer(f"✅ Разбанен <code>{a[1]}</code>")


@dp.message(Command("bc"))
async def cmd_broadcast(m: Message):
    if not is_admin(m.from_user.id):
        return
    text = m.text.split(maxsplit=1)
    if len(text) < 2:
        return await m.answer("Использование: <code>/bc текст рассылки</code>")
    body = text[1]
    targets = [int(u) for u in S["users"]]
    ok = fail = 0
    status = await m.answer(f"📢 Рассылка на {len(targets)}...")
    for i, uid in enumerate(targets):
        try:
            await bot.send_message(uid, body)
            ok += 1
        except Exception:
            fail += 1
        if i % 25 == 0:
            await asyncio.sleep(1)          # антифлуд
        else:
            await asyncio.sleep(0.05)
    await status.edit_text(f"📢 Готово: ✅ {ok} · ❌ {fail}")


# ═════════════════════════════════════════════════════════
async def main():
    asyncio.create_task(autosave_loop())
    me = await bot.get_me()
    print(f"♡ @{me.username} стартовал. Юзеров: {len(S['users'])}")
    await dp.start_polling(bot, allowed_updates=[
        "message", "callback_query", "business_connection", "business_message",
        "edited_business_message", "deleted_business_messages",
    ])


if __name__ == "__main__":
    asyncio.run(main())
