#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
  Karzen Business Bot  ♡  (aiogram 3 · Bot API 9.x)
=========================================================
Подключение: Настройки → Telegram для бизнеса → Чат-боты

Права при подключении:
  ✅ Отвечать на сообщения
  ✅ Читать сообщения
  ✅ Удалять отправленные сообщения  ← для словесных модов
  ✅ Удалять любые сообщения         ← для автоудаления скама

Блоки:
  🛡 АНТИСКАМ — детект развода на первом контакте, уведомления
  🔪 ФИЛЬТР  — автоудаление подозрительных первых сообщений
  🗑 АНТИ-ДЕЛИТ — сохраняет удалённые сообщения
  🌸 МОДЫ — kawaii / tsundere / yandere / leet + совместный мод
  🎬 МЕДИА — .gif .fv .lq .story .nk .type
  🛠 АДМИНКА — статистика, список юзеров, бан, рассылка

    pip install aiogram pillow aiohttp     (+ ffmpeg в системе)
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
from datetime import datetime, timedelta, timezone

# Сервер живёт в UTC. TZ_OFFSET — твой сдвиг в часах (Люксембург: 1 зимой, 2 летом)
TZ_OFFSET = float(os.getenv("TZ_OFFSET", "2"))


def now_local() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)


def ts_local(ts) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=TZ_OFFSET)

import aiohttp
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
# Бэкенд хранения выбирается сам:
#   есть DATABASE_URL (Neon/Postgres) → storage_pg  (нужен на Render:
#   там диск эфемерный и bot.db стирается при каждом деплое)
#   нет                               → storage     (SQLite, для VPS/локалки)
# ⚠️ ОБА файла (storage.py и storage_pg.py) должны лежать рядом с этим
#    скриптом в репозитории, иначе ModuleNotFoundError при старте.
if os.getenv("DATABASE_URL"):
    from storage_pg import Storage
    _BACKEND = "postgres"
else:
    from storage import Storage
    _BACKEND = "sqlite"
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile, BusinessConnection, BusinessMessagesDeleted, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message,
)

# ═════════════════════════════════════════════════════════
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN")

# ── Хостинг ──────────────────────────────────────────────
#  Render (и любой PaaS с веб-сервисом) требует открытый порт и не даёт
#  гарантий, что старый инстанс умер до старта нового → при поллинге
#  получаешь TelegramConflictError: terminated by other getUpdates.
#  Поэтому на хостинге работаем ВЕБХУКАМИ: Telegram сам стучится к нам,
#  порт открыт, второй инстанс физически не может «перехватить» апдейты.
#
#  Render сам выставляет RENDER_EXTERNAL_URL и PORT.
#  Локально ничего не задаёшь → включится обычный поллинг.
WEBHOOK_BASE = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL", "")
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me-please")
WEBHOOK_PATH = "/tg/webhook"
ADMINS = [123456789]              # <-- твой telegram id
PREFIX = "."
DB_FILE = "bot.db"
CACHE_LIMIT = 8000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ═════════════════════════════════════════════════════════
#  ХРАНИЛИЩЕ
# ═════════════════════════════════════════════════════════
ANTISCAM_TPL = {
    "enabled": True,
    "new_dialog": True,        # уведомлять о новых диалогах
    "unknown_bot": True,       # писал незнакомый бот
    "exec_files": True,        # прислали .apk/.exe и подобное
    "scam": True,              # сработал детектор развода
}
FILTER_TPL = {
    "enabled": False,          # ВЫКЛ по умолчанию — сначала посмотри на уведомления
    "delete": True,            # удалять, а не только помечать
    "links": True,
    "numbers": True,
    "buttons": True,
    "words_on": False,
    "words": [],
}
USER_TPL = {
    "name": "", "username": "", "first_seen": 0, "last_seen": 0,
    "banned": False, "cmds": 0, "caught": 0,
    "mode": None, "antidelete": True, "pairs": {},
    "ref": 0,                  # кто пригласил (deep link)
    "save_media": True,        # архивировать входящие медиа
    "save_own": False,         # архивировать и свои тоже
    "antiscam": dict(ANTISCAM_TPL), "filter": dict(FILTER_TPL),
}


st = Storage(os.getenv("DATABASE_URL") or DB_FILE, defaults=USER_TPL)


async def storage_start():
    """У Postgres-слоя есть асинхронный init (пул + схема + preload)."""
    if hasattr(st, "init"):
        await st.init()
    logging.info("storage: %s", _BACKEND)


async def storage_stop():
    r = st.close()
    if asyncio.iscoroutine(r):
        await r


def user(uid, tg=None) -> dict:
    return st.user(uid, tg)


def save(uid=None):
    if uid is not None:
        st.mark(uid)


def conn_of(uid):
    return st.conn(uid)


def owner_by_cid(cid):
    return st.owner_by_cid(cid)


async def dm(uid, text, **kw):
    c = conn_of(uid)
    if c:
        try:
            return await bot.send_message(c["chat"], text, **kw)
        except Exception:
            pass


# ═════════════════════════════════════════════════════════
#  🛡 АНТИСКАМ — детектор
# ═════════════════════════════════════════════════════════

# ── нормализация текста (против обхода фильтров) ──────────
#  Скамеры маскируют слова: латиница вместо кириллицы (pабota),
#  растяжка (рааабота), разделители (р.а.б.о.т.а), невидимые символы.
#  Чистим ТОЛЬКО для поиска слов; ссылки/номера ищем в оригинале.
HOMOGLYPH = str.maketrans({
    "a": "а", "b": "ь", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
    "o": "о", "p": "р", "s": "ѕ", "t": "т", "u": "и", "x": "х", "y": "у",
    "A": "а", "B": "в", "C": "с", "E": "е", "H": "н", "K": "к", "M": "м",
    "O": "о", "P": "р", "T": "т", "X": "х", "Y": "у",
    "ё": "е", "Ё": "е", "і": "и", "ї": "и", "є": "е", "ў": "у",
})
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff\u00ad]")
PUNCT_SPLIT = re.compile(r"(?<=\b\w)[._\-*|/]{1,3}(?=\w\b)")        # к.а.з.и.н.о
SPACE_SPLIT = re.compile(r"\b(?:\w[ ]){2,}\w\b")                     # к а з и н о
MIXED_WORD = re.compile(r"\b(?=\w*[а-яё])(?=\w*[a-z])\w{3,}\b", re.I)


def normalize(text: str) -> str:
    import unicodedata
    t = ZERO_WIDTH.sub("", text or "")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().translate(HOMOGLYPH)
    t = re.sub(r"(.)\1{2,}", r"\1", t)          # рааабота -> работа
    for _ in range(3):                           # р.а.б.о.т.а -> работа
        t = PUNCT_SPLIT.sub("", t)
    t = SPACE_SPLIT.sub(lambda mm: mm.group(0).replace(" ", ""), t)   # р а б о т а
    return re.sub(r"\s{2,}", " ", t).strip()


def is_masked(text: str) -> bool:
    """Явные признаки маскировки: невидимые символы или латиница внутри
    русских слов (pабota). Обычный текст сюда не попадает."""
    if ZERO_WIDTH.search(text or ""):
        return True
    return bool(MIXED_WORD.search(text or ""))

SCAM_RULES = [
    # (вес, название, regex)
    (3, "предложение работы", r"(подработк|вакансі|ваканс|ищем\s+сотрудник|набираем\s+люд|"
                              r"требуют?ся\s+|работа\s+(на\s+дому|удал[её]нн)|"
                              r"зараб\w*\s+от\s*\d|доход\s+от\s*\d|от\s*\d{3,}\s*(р|руб|\$|€)\s*в\s*день)"),
    (3, "крипта/инвестиции", r"(инвест|крипт|трейдинг|бинанс|binance|усдт|usdt|"
                             r"пассивн\w+\s+доход|удвоим\s+ваш|вложени)"),
    (3, "фишинг аккаунта", r"(код\s+из\s+(смс|sms|телеграм)|подтверд\w+\s+вход|"
                           r"проголосуй\s+за|помоги\s+проголосовать|"
                           r"перейди\s+по\s+ссылк\w+\s+и\s+(войди|авториз))"),
    (2, "розыгрыш/приз", r"(вы\s+выиграл|розыгрыш|бесплатн\w+\s+(звёзд|звезд|premium|премиум|nft)|"
                         r"забери\s+подарок|поздравляем.{0,20}приз)"),
    (2, "срочность/давление", r"(срочно|только\s+сегодня|осталось\s+\d+\s+мест|успей|"
                              r"не\s+упусти|последний\s+шанс)"),
    (2, "просьба денег", r"(скинь\w*\s+(деньг|\d+\s*(р|руб))|переведи\w*\s+\d|"
                         r"одолж\w+|нужна\s+помощь\s+деньгами|карта\s*[:\-]?\s*\d{4})"),
    (1, "сумма денег", r"(\d{3,}\s*(руб|р\b|₽|\$|€|usd|дол)|\d+\s*(к|k)\s*(в|за)\s*(день|неделю|час))"),
    (1, "личка/переход", r"(пиши\s+в\s+лс|напиши\s+мне\s+в|переходи\s+в\s+бот|жми\s+сюда)"),
]
EXEC_EXT = (".apk", ".exe", ".bat", ".cmd", ".scr", ".msi", ".jar",
            ".vbs", ".ps1", ".sh", ".com", ".dmg")
URL_RE = re.compile(r"(https?://|t\.me/|telegra\.ph|@[A-Za-z]\w{3,})", re.I)
PHONE_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{8,}\d)")
CARD_RE = re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b")


def analyze(m: Message, first_contact: bool) -> dict:
    """Возвращает {'score':int, 'reasons':[str]} для входящего сообщения."""
    text = (m.text or m.caption or "")
    low = normalize(text)              # ← очищенный текст для поиска слов
    score, reasons = 0, []

    if is_masked(text):
        score += 1
        reasons.append("маскировка символов")

    for weight, label, rx in SCAM_RULES:
        if re.search(rx, low):
            score += weight
            reasons.append(label)

    if URL_RE.search(text):
        score += 2 if first_contact else 1
        reasons.append("ссылка")
    if PHONE_RE.search(text):
        score += 1
        reasons.append("номер телефона")
    if CARD_RE.search(text):
        score += 3
        reasons.append("номер карты")
    if m.reply_markup and getattr(m.reply_markup, "inline_keyboard", None):
        score += 2
        reasons.append("инлайн-кнопки")
    if m.forward_origin is not None:
        score += 1
        reasons.append("переслано")
    if m.document and m.document.file_name and \
            m.document.file_name.lower().endswith(EXEC_EXT):
        score += 4
        reasons.append(f"исполняемый файл ({m.document.file_name})")
    if m.from_user and m.from_user.is_bot:
        score += 1
        reasons.append("отправитель — бот")
    if first_contact:
        score += 1

    return {"score": score, "reasons": reasons, "text": text}


SCAM_THRESHOLD = 4       # от скольки баллов считаем подозрительным
#  ↑ ниже = строже (больше ложных), выше = мягче. Подкрути под себя.


def filter_hit(m: Message, f: dict, verdict: dict) -> str | None:
    """Правила «Тесака»: что именно поймали. None — чисто."""
    text = m.text or m.caption or ""
    if f["links"] and URL_RE.search(text):
        return "ссылка"
    if f["numbers"] and (PHONE_RE.search(text) or CARD_RE.search(text)):
        return "номер"
    if f["buttons"] and m.reply_markup and getattr(m.reply_markup, "inline_keyboard", None):
        return "кнопки"
    if f["words_on"] and f["words"]:
        low = normalize(text)
        for w in f["words"]:
            if w and normalize(w) in low:
                return f"слово «{w}»"
    if verdict["score"] >= SCAM_THRESHOLD:
        return "скам-детектор: " + ", ".join(verdict["reasons"][:3])
    return None


async def guard_incoming(m: Message, owner_id: int):
    """Главная проверка входящего сообщения от собеседника."""
    u = user(owner_id)
    peer = m.chat.id
    a, f = u["antiscam"], u["filter"]

    if st.is_trusted(owner_id, peer) or peer == owner_id:
        return
    first_contact = not st.is_known(owner_id, peer)

    # 1) уведомления
    if a["enabled"]:
        if first_contact and a["new_dialog"]:
            who = m.from_user.full_name if m.from_user else "?"
            un = f"@{m.from_user.username}" if (m.from_user and m.from_user.username) else "—"
            await dm(owner_id,
                     f"💬 <b>Новый диалог</b>\n👤 {who} · {un} · <code>{peer}</code>",
                     reply_markup=peer_kb(peer))
        if a["unknown_bot"] and m.from_user and m.from_user.is_bot and first_contact:
            await dm(owner_id, f"🤖 <b>Незнакомый бот</b> написал тебе: "
                               f"@{m.from_user.username or '?'} (<code>{peer}</code>)",
                     reply_markup=peer_kb(peer))

    verdict = analyze(m, first_contact)

    if a["enabled"] and a["exec_files"] and m.document and m.document.file_name \
            and m.document.file_name.lower().endswith(EXEC_EXT):
        await dm(owner_id, f"⚠️ <b>Исполняемый файл!</b>\n"
                           f"<code>{m.document.file_name}</code> от <code>{peer}</code>\n"
                           f"Не открывай это на телефоне.")

    # 2) фильтр / автоудаление
    hit = filter_hit(m, f, verdict) if f["enabled"] and first_contact else None
    if hit:
        deleted = False
        if f["delete"]:
            try:
                await bot.delete_business_messages(
                    business_connection_id=m.business_connection_id,
                    message_ids=[m.message_id])
                deleted = True
            except Exception as ex:
                logging.warning("filter delete: %s", ex)
        u["caught"] += 1
        save(owner_id)
        st.log_catch(owner_id, peer, verdict["score"], hit, verdict["text"],
                     "deleted" if deleted else "notified")
        head = "🔪 <b>Сообщение удалено</b>" if deleted else "🔪 <b>Подозрительное сообщение</b>"
        body = (verdict["text"][:600] or "(без текста)")
        await dm(owner_id, f"{head}\n🎯 Причина: {hit}\n"
                           f"👤 <code>{peer}</code>\n\n<blockquote>{body}</blockquote>",
                 reply_markup=peer_kb(peer))
    elif a["enabled"] and a["scam"] and verdict["score"] >= SCAM_THRESHOLD:
        st.log_catch(owner_id, peer, verdict["score"],
                     ", ".join(verdict["reasons"][:3]), verdict["text"], "notified")
        await dm(owner_id,
                 f"🚨 <b>Похоже на развод</b> ({verdict['score']} баллов)\n"
                 f"🎯 {', '.join(verdict['reasons'][:4])}\n👤 <code>{peer}</code>\n\n"
                 f"<blockquote>{verdict['text'][:600]}</blockquote>",
                 reply_markup=peer_kb(peer))

    # запоминаем диалог
    if first_contact:
        st.add_known(owner_id, peer)


def peer_kb(peer) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Доверять", callback_data=f"peer:white:{peer}"),
        InlineKeyboardButton(text="🚫 Не доверять", callback_data=f"peer:unwhite:{peer}"),
    ]])


@dp.callback_query(F.data.startswith("peer:"))
async def peer_cb(cb: CallbackQuery):
    _, act, peer = cb.data.split(":")
    u = user(cb.from_user.id)
    peer = int(peer)
    if act == "white":
        st.set_trust(cb.from_user.id, peer, True)
        await cb.answer("Добавлен в доверенные ✅")
    else:
        st.set_trust(cb.from_user.id, peer, False)
        await cb.answer("Убран из доверенных")
    try:
        await cb.message.edit_reply_markup(reply_markup=peer_kb(peer))
    except Exception:
        pass


# ═════════════════════════════════════════════════════════
#  СТИЛИ ТЕКСТА
# ═════════════════════════════════════════════════════════
KAOMOJI = ["(◕‿◕✿)", "(｡♥‿♥｡)", "(*≧ω≦*)", "ʕ•ᴥ•ʔ", "(っ◔◡◔)っ ♡", "(˘⌣˘)♡",
           "ヽ(=^･ω･^=)丿", "(´｡• ᵕ •｡`)", "(⁄ ⁄•⁄ω⁄•⁄ ⁄)", "(◍•ᴗ•◍)♡", "(*ฅ́˘ฅ̀*)♡"]
NYA = ["ня", "ня~", "мур", "мурр~", "уву", "мяу", "нявушки"]


def kawaii(t):
    t = t.replace("!", "! ♡").replace("?", "? >_<")
    t = t.replace("л", "ль").replace("р", "рь")
    t = re.sub(r"\bда\b", "дя", t, flags=re.I)
    return f"{t} {random.choice(NYA)}~ {random.choice(KAOMOJI)}"


def tsundere(t):
    return (random.choice(["Н-не то чтобы я специально, но ", "Х-хмф! ", "Б-бака! "])
            + t.lower()
            + random.choice([" ...и вообще, мне всё равно! (>﹏<)", " ...б-бака.",
                             " ...не благодари, ясно?! (๑•̀ㅁ•́๑)"]))


def yandere(t):
    return t + random.choice([" Ты ведь только мой, да?~ 🔪♡",
                              " Я никому тебя не отдам... никогда~ (◡‿◡✿)"])


def leet(t):
    return t.translate(str.maketrans({"a": "4", "e": "3", "i": "1", "o": "0", "s": "5",
                                      "t": "7", "а": "@", "е": "3", "о": "0", "и": "1"}))


MODES = {"kawaii": kawaii, "tsundere": tsundere, "yandere": yandere, "leet": leet}
RU = "йцукенгшщзхъфывапролджэячсмитьбю."
EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,./"


def switch_layout(t):
    a, b = dict(zip(RU, EN)), dict(zip(EN, RU))
    tbl = a if sum(c.lower() in a for c in t) >= sum(c.lower() in b for c in t) else b
    return "".join(tbl.get(c, tbl.get(c.lower(), c)) for c in t)


# ═════════════════════════════════════════════════════════
#  ПОДКЛЮЧЕНИЕ
# ═════════════════════════════════════════════════════════
@dp.business_connection()
async def on_connect(bc: BusinessConnection):
    uid = bc.user.id
    user(uid, bc.user)
    if bc.is_enabled:
        st.set_connection(uid, bc.id, bc.user_chat_id)
        miss = []
        r = bc.rights
        if r:
            if not r.can_reply:
                miss.append("отвечать")
            if not r.can_read_messages:
                miss.append("читать сообщения")
            if not (r.can_delete_sent_messages or r.can_delete_outgoing_messages):
                miss.append("удалять отправленные (для модов)")
            if not r.can_delete_all_messages:
                miss.append("удалять любые (для автоудаления скама)")
        warn = ("\n\n⚠️ <b>Не выданы права:</b> " + ", ".join(miss) +
                "\nДобавь их там же, где подключал — иначе часть функций молчит.") if miss else ""
        await bot.send_message(
            bc.user_chat_id,
            "✅ <b>Подключено!</b>\n\n"
            "Уже работает:\n"
            "🗑 сохранение удалённых сообщений\n"
            "📦 архив медиа\n"
            "🛡 антискам на первых сообщениях\n\n"
            "Команды пишешь <b>сам в любом чате</b> с точкой: <code>.help</code>\n"
            "Настройки — /start" + warn,
            reply_markup=main_kb(uid))
        for a in ADMINS:                       # пинг владельцу бота
            if a != uid:
                try:
                    await bot.send_message(
                        a, f"🔌 Новое подключение: <b>{bc.user.full_name}</b> "
                           f"(@{bc.user.username or '—'}, <code>{uid}</code>)")
                except Exception:
                    pass
    else:
        st.drop_connection(uid)


# ═════════════════════════════════════════════════════════
#  АНТИ-ДЕЛИТ
# ═════════════════════════════════════════════════════════
cache: "OrderedDict[tuple, dict]" = OrderedDict()


def cache_put(m: Message):
    cache[(m.chat.id, m.message_id)] = {
        "text": m.text or m.caption or "",
        "from": m.from_user.full_name if m.from_user else "?",
        "from_id": m.from_user.id if m.from_user else 0,
        "chat": m.chat.id, "mid": m.message_id,
        "media": bool(m.photo or m.video or m.voice or m.video_note
                      or m.document or m.sticker or m.animation),
        "uid": (media_of(m)[3] if media_of(m) else None),
    }
    while len(cache) > CACHE_LIMIT:
        cache.popitem(last=False)


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
                # копия обычно уже в архиве — тогда оригинал не нужен
                if info.get("uid") and st.has_media(owner_id, info["uid"]):
                    head += "\n📦 <i>копия сохранена в архиве выше</i>"
                else:
                    try:
                        await bot.forward_message(c["chat"], info["chat"], info["mid"])
                    except Exception:
                        pass
            await bot.send_message(c["chat"], head + (
                f"\n\n💬 {info['text']}" if info["text"] else "\n\n📎 (медиа)"))
        except Exception as ex:
            logging.warning("antidelete: %s", ex)



# ═════════════════════════════════════════════════════════
#  📦 АРХИВ МЕДИА
# ═════════════════════════════════════════════════════════
#  Копия каждого медиа пересылается тебе в чат с ботом. Почему туда,
#  а не на диск: на Render диск эфемерный (стирается при деплое), а в
#  Telegram копия лежит вечно и открывается с любого устройства.
#
#  Ограничение Bot API: getFile отдаёт файлы до 20 МБ. Крупные видео
#  скачать нельзя — для них сохраняем только file_id (по нему можно
#  переслать, пока оригинал жив) и помечаем в архиве.
MAX_DL = 20 * 1024 * 1024
KINDS = ("photo", "video", "voice", "video_note", "animation", "document", "audio", "sticker")


def media_of(m: Message):
    """→ (тип, объект, file_id, file_uid, размер) или None."""
    for k in KINDS:
        obj = getattr(m, k, None)
        if not obj:
            continue
        if k == "photo":
            obj = obj[-1]                    # самый крупный размер
        return (k, obj, obj.file_id, obj.file_unique_id, getattr(obj, "file_size", 0) or 0)
    return None


async def archive_media(m: Message, owner_id: int, forced=False) -> str | None:
    """Сохранить медиа в архив владельца. Возвращает текст статуса или None."""
    u = user(owner_id)
    if not forced and not u.get("save_media", True):
        return None
    info = media_of(m)
    if not info:
        return "тут нет медиа" if forced else None
    kind, obj, file_id, file_uid, size = info

    own = bool(m.from_user and m.from_user.id == owner_id)
    if own and not forced and not u.get("save_own", False):
        return None
    if not forced and st.has_media(owner_id, file_uid):
        return None                          # уже в архиве

    c = conn_of(owner_id)
    if not c:
        return None
    who = ((m.from_user.full_name if m.from_user else "") or "").strip()
    if not who:
        who = getattr(m.chat, "full_name", None) or getattr(m.chat, "title", None) \
            or f"id {m.chat.id}"
    cap = (f"📦 <b>{kind}</b> · {who}\n"
           f"💬 чат <code>{m.chat.id}</code> · {now_local():%d.%m %H:%M}")
    if m.caption:
        cap += f"\n\n{m.caption[:300]}"

    saved_id, note = 0, ""
    try:
        if size and size > MAX_DL:
            # большой файл — копируем ссылкой на оригинал
            msg = await bot.copy_message(c["chat"], m.chat.id, m.message_id,
                                         caption=cap[:1000])
            saved_id, note = msg.message_id, "copied (>20MB)"
        else:
            data = (await bot.download(file_id)).read()
            size = size or len(data)          # у фото file_size бывает пустой
            fname = getattr(obj, "file_name", None) or f"{kind}_{file_uid}"
            file = BufferedInputFile(data, fname)
            sender = {"photo": bot.send_photo, "video": bot.send_video,
                      "voice": bot.send_voice, "video_note": bot.send_video_note,
                      "animation": bot.send_animation, "audio": bot.send_audio,
                      "sticker": bot.send_sticker}.get(kind, bot.send_document)
            kw = {"caption": cap[:1000]} if kind not in ("video_note", "sticker") else {}
            msg = await sender(c["chat"], file, **kw)
            if kind in ("video_note", "sticker"):
                await bot.send_message(c["chat"], cap)
            saved_id, note = msg.message_id, "downloaded"
    except Exception as ex:
        logging.warning("archive %s: %s", kind, ex)
        try:      # не смогли скачать — хотя бы копию оригинала
            msg = await bot.copy_message(c["chat"], m.chat.id, m.message_id)
            saved_id, note = msg.message_id, f"copy fallback ({ex.__class__.__name__})"
        except Exception:
            note = f"fail: {ex.__class__.__name__}"

    st.log_media(owner_id, m.chat.id, m.from_user.id if m.from_user else 0,
                 kind, file_id, file_uid, size, saved_id, note)
    return f"📦 {kind} в архиве ({note})" if saved_id else f"⚠️ не вышло: {note}"


# ═════════════════════════════════════════════════════════
#  РОУТЕР
# ═════════════════════════════════════════════════════════
@dp.business_message()
async def on_business_message(m: Message):
    owner_id, _ = owner_by_cid(m.business_connection_id)
    if owner_id is None or m.sender_business_bot is not None:
        return
    cache_put(m)

    if not (m.from_user and m.from_user.id == owner_id):
        await guard_incoming(m, owner_id)        # ← входящее: проверяем
        asyncio.create_task(archive_media(m, owner_id))   # и архивируем медиа
        return

    u = user(owner_id, m.from_user)
    if u["banned"]:
        return
    # владелец сам написал в диалог — считаем его знакомым
    if not st.is_known(owner_id, m.chat.id):
        st.add_known(owner_id, m.chat.id)

    if media_of(m):
        asyncio.create_task(archive_media(m, owner_id))

    text = m.text or ""
    if text.startswith(PREFIX):
        u["cmds"] += 1
        save()
        return await handle_cmd(m, owner_id, text[len(PREFIX):])

    mode = u["pairs"].get(str(m.chat.id)) or u["mode"]
    if mode and mode in MODES and text.strip():
        await replace_with(m, MODES[mode](text))


async def drop(m: Message) -> bool:
    try:
        await bot.delete_business_messages(
            business_connection_id=m.business_connection_id, message_ids=[m.message_id])
        return True
    except Exception as ex:
        logging.warning("delete: %s", ex)
        return False


async def replace_with(m: Message, new_text: str):
    if not await drop(m):
        return
    try:
        await bot.send_message(m.chat.id, new_text,
                               business_connection_id=m.business_connection_id)
    except Exception as ex:
        logging.warning("send: %s", ex)


# ═════════════════════════════════════════════════════════
#  МЕДИА
# ═════════════════════════════════════════════════════════
async def dl(file_id) -> bytes:
    return (await bot.download(file_id)).read()


def ff(args, data, in_ext, out_ext) -> bytes:
    src, dst = tempfile.mktemp(suffix=in_ext), tempfile.mktemp(suffix=out_ext)
    try:
        open(src, "wb").write(data)
        subprocess.run(["ffmpeg", "-y", "-i", src] + args + [dst],
                       check=True, capture_output=True, timeout=120)
        return open(dst, "rb").read()
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
    parts, x = [], 0
    while x < W and len(parts) < 10:
        b = io.BytesIO()
        img.crop((x, 0, min(x + step, W), H)).save(b, "JPEG", quality=92)
        parts.append(b.getvalue())
        x += step
    return parts


# ═════════════════════════════════════════════════════════
#  КОМАНДЫ
# ═════════════════════════════════════════════════════════
CMD_HELP = """✨ <b>Команды</b> (префикс <code>.</code>)

<b>Защита</b>
<code>.scam</code> — вкл/выкл антискам
<code>.filter</code> — вкл/выкл фильтр первых сообщений
<code>.trust</code> — доверять этому собеседнику (ответом или в его чате)
<code>.check</code> — проверить сообщение вручную (ответом)
<code>.word +слово</code> / <code>.word -слово</code> — стоп-слова
<code>/log</code> — журнал срабатываний

<b>Моды</b>
<code>.mode kawaii|tsundere|yandere|leet|off</code>
<code>.kawaii</code> <code>.tsundere</code> <code>.yandere</code> <code>.leet</code>
<code>.pair kawaii</code> · <code>.unpair</code> · <code>.pairs</code>

<b>Архив медиа</b> 📦
<code>.save</code> — сохранить медиа (ответом)
<code>.savemedia</code> — авто-архив вкл/выкл
<code>.media</code> — что в архиве

<b>Медиа</b> (ответом)
<code>.gif</code> <code>.fv</code> <code>.lq</code> <code>.story</code> <code>.nk</code>

<b>Прочее</b>
<code>/invite</code> — позвать друга · <code>/setup</code> — инструкция
<code>.type</code> <code>.sw</code> <code>.flip</code> <code>.dice</code> <code>.love</code> <code>.ad</code> <code>.me</code>
"""


async def handle_cmd(m: Message, uid: int, raw: str):
    parts = raw.split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    u = user(uid)
    cid = m.business_connection_id
    peer = m.chat.id
    rep = m.reply_to_message

    async def out(t):
        await replace_with(m, t)

    async def note(t, **kw):
        await drop(m)
        await dm(uid, t, **kw)

    # ── защита ──
    if name == "scam":
        u["antiscam"]["enabled"] = not u["antiscam"]["enabled"]
        save()
        return await note(f"🛡 Антискам: {'вкл ✅' if u['antiscam']['enabled'] else 'выкл ❌'}")

    if name == "filter":
        u["filter"]["enabled"] = not u["filter"]["enabled"]
        save()
        return await note(f"🔪 Фильтр: {'вкл ✅' if u['filter']['enabled'] else 'выкл ❌'}\n"
                          f"Удаление: {'да' if u['filter']['delete'] else 'нет (только уведомления)'}")

    if name == "trust":
        target = rep.from_user.id if (rep and rep.from_user) else peer
        st.set_trust(uid, target, True)
        return await note(f"✅ <code>{target}</code> в доверенных — проверки для него отключены.")

    if name == "check":
        if not rep:
            return await note("↩️ Ответь на сообщение, которое проверить.")
        v = analyze(rep, rep.chat.id not in u["known"])
        verdict = ("🚨 похоже на развод" if v["score"] >= SCAM_THRESHOLD
                   else "🟡 есть признаки" if v["score"] >= 3 else "🟢 чисто")
        return await note(f"🔎 <b>Проверка</b>: {verdict}\n"
                          f"Баллы: <b>{v['score']}</b> (порог {SCAM_THRESHOLD})\n"
                          f"Признаки: {', '.join(v['reasons']) or '—'}")

    if name == "word":
        a = args.strip()
        if not a:
            lst = ", ".join(u["filter"]["words"]) or "пусто"
            return await note(f"📝 Стоп-слова ({len(u['filter']['words'])}): {lst}\n"
                              f"Фильтр слов: {'вкл' if u['filter']['words_on'] else 'выкл'}")
        if a.startswith("+"):
            w = a[1:].strip().lower()
            if w and w not in u["filter"]["words"]:
                u["filter"]["words"].append(w)
                u["filter"]["words_on"] = True
                save()
            return await note(f"➕ Добавлено: «{w}»")
        if a.startswith("-"):
            w = a[1:].strip().lower()
            if w in u["filter"]["words"]:
                u["filter"]["words"].remove(w)
                save()
            return await note(f"➖ Удалено: «{w}»")
        return await note("Использование: <code>.word +слово</code> / <code>.word -слово</code>")

    # ── инфо ──
    if name in ("help", "h"):
        return await note(CMD_HELP)
    if name == "me":
        return await note(
            f"👤 <b>{u['name']}</b>\n"
            f"🛡 Антискам: {'вкл' if u['antiscam']['enabled'] else 'выкл'}\n"
            f"🔪 Фильтр: {'вкл' if u['filter']['enabled'] else 'выкл'} · поймано: <b>{u['caught']}</b>\n"
            f"🗑 Анти-делит: {'вкл' if u['antidelete'] else 'выкл'}\n"
            f"🌸 Мод: <b>{u['mode'] or 'выкл'}</b> · 🤝 пар: {len(u['pairs'])}\n"
            f"✅ Доверенных: {st.counts(uid)[1]} · 💬 диалогов: {st.counts(uid)[0]}")

    # ── моды ──
    if name == "mode":
        a = args.strip().lower()
        if a in ("off", "", "none"):
            u["mode"] = None
            save()
            return await note("🔕 Мод выключен.")
        if a not in MODES:
            return await note("⚠️ Моды: " + ", ".join(MODES))
        u["mode"] = a
        save()
        return await note(f"✅ Мод: <b>{a}</b> ♡")

    if name in MODES:
        return await out(MODES[name](args or (rep.text if rep and rep.text else " ")))
    if name == "sw":
        return await out(switch_layout(args or (rep.text if rep else "")))
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

    # ── архив ──
    if name == "save":
        target = rep or m
        await drop(m)
        res = await archive_media(target, uid, forced=True)
        return await dm(uid, res or "⚠️ нечего сохранять")

    if name == "savemedia":
        u["save_media"] = not u["save_media"]
        save(uid)
        return await note(f"📦 Архив медиа: {'вкл ✅' if u['save_media'] else 'выкл ❌'}")

    if name == "media":
        def plural(n, forms=("файл", "файла", "файлов")):
            n10, n100 = n % 10, n % 100
            if n10 == 1 and n100 != 11:
                return forms[0]
            if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
                return forms[1]
            return forms[2]

        n, sz = st.media_stats(uid) if not asyncio.iscoroutinefunction(st.media_stats) \
            else await st.media_stats(uid)
        rows = st.recent_media(uid, 5) if not asyncio.iscoroutinefunction(st.recent_media) \
            else await st.recent_media(uid, 5)
        lines = [f"• {r['kind']} от <code>{r['sender']}</code> · "
                 f"{ts_local(r['ts']):%d.%m %H:%M}" for r in rows]
        vol = (f"{sz/1048576:.1f} МБ" if sz >= 1048576
               else f"{sz/1024:.0f} КБ" if sz else "размер неизвестен")
        return await note(f"📦 <b>Архив</b>: {n} {plural(n)}, {vol}\n\n"
                          + ("\n".join(lines) or "пусто"))

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
                gif = ff(["-vf", "fps=15,scale=320:-1:flags=lanczos"],
                         await dl(src.file_id), ".mp4", ".gif")
                await bot.send_animation(m.chat.id, BufferedInputFile(gif, "a.gif"),
                                         business_connection_id=cid)
            elif name == "fv":
                if not rep.voice:
                    return await dm(uid, "⚠️ Нужно голосовое.")
                ogg = ff(["-af", "volume=8dB,acompressor", "-c:a", "libopus"],
                         await dl(rep.voice.file_id), ".ogg", ".ogg")
                await bot.send_voice(m.chat.id, BufferedInputFile(ogg, "v.ogg"),
                                     business_connection_id=cid)
            elif name == "lq":
                if not rep.photo:
                    return await dm(uid, "⚠️ Нужно фото.")
                await bot.send_photo(m.chat.id,
                                     BufferedInputFile(deepfry(await dl(rep.photo[-1].file_id)), "s.jpg"),
                                     caption="🐺 зашакалено", business_connection_id=cid)
            elif name == "story":
                if not rep.photo:
                    return await dm(uid, "⚠️ Нужно фото.")
                parts = to_stories(await dl(rep.photo[-1].file_id))
                media = [InputMediaPhoto(media=BufferedInputFile(p, f"s{i}.jpg"))
                         for i, p in enumerate(parts)]
                if len(media) == 1:
                    await bot.send_photo(m.chat.id, media[0].media, business_connection_id=cid)
                else:
                    await bot.send_media_group(m.chat.id, media, business_connection_id=cid)
        except subprocess.CalledProcessError as ex:
            await dm(uid, f"⚠️ ffmpeg: {ex.stderr[-300:].decode(errors='ignore')}")
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
        if not conn_of(peer):
            return await note("😕 У собеседника не подключён бот.")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Принять", callback_data=f"pair:ok:{uid}:{mode}"),
            InlineKeyboardButton(text="🚫 Отклонить", callback_data=f"pair:no:{uid}:{mode}")]])
        await drop(m)
        await dm(peer, f"🤝 <b>{u['name']}</b> предлагает совместный мод <b>{mode}</b>.",
                 reply_markup=kb)
        return await dm(uid, f"📨 Приглашение отправлено ({mode}).")

    if name == "unpair":
        had = u["pairs"].pop(str(peer), None)
        if user(peer)["pairs"].pop(str(uid), None):
            await dm(peer, "👋 Собеседник выключил совместный мод.")
        save()
        return await note("👋 Выключено." if had else "🤷 Пары не было.")

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
#  ОНБОРДИНГ ДЛЯ НОВЫХ ЛЮДЕЙ
# ═════════════════════════════════════════════════════════
#  ⚠️ ГЛАВНОЕ: в @BotFather включи Business Mode, иначе бота
#     физически НЕ БУДЕТ в списке чат-ботов у пользователя:
#       /mybots → выбрать бота → Bot Settings → Business Mode → Enable
#
#  Premium для подключения НЕ нужен: большинство бизнес-функций
#  требуют подписку, но подключение чат-бота доступно всем.
SETUP_STEPS = """🚀 <b>Как подключить бота к себе</b>

<b>1.</b> Настройки Telegram → <b>Telegram для бизнеса</b>
   <i>(если раздела нет — обнови приложение)</i>

<b>2.</b> Пункт <b>Чат-боты</b>

<b>3.</b> Вставь туда: {username}

<b>4.</b> Выдай права — все пять:
   ✅ Читать сообщения
   ✅ Отвечать на сообщения
   ✅ Удалять отправленные сообщения
   ✅ Удалять любые сообщения
   ✅ (остальные по желанию)

<b>5.</b> Выбери чаты, где бот работает
   <i>«Все чаты» — или только нужные</i>

Premium для этого <b>не нужен</b>.

⚠️ Бизнес-боты работают <b>только в диалогах с людьми</b> —
не в группах, не в Избранном, не в чатах с ботами.
Это ограничение Telegram, обойти нельзя.

Как закончишь — жми «Проверить» ниже 👇"""


def setup_kb(username) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить подключение", callback_data="m:check")],
        [InlineKeyboardButton(text="❓ Не получается", callback_data="m:trouble")],
    ])


TROUBLE = """🔧 <b>Если не выходит</b>

<b>Бота нет в списке чат-ботов</b>
У владельца бота не включён Business Mode в @BotFather.

<b>Нет раздела «Telegram для бизнеса»</b>
Обнови приложение. На очень старых версиях раздела нет.

<b>Подключил, но ничего не происходит</b>
Проверь, что в настройках выбраны чаты («Все чаты» проще всего)
и что выданы права на чтение и ответы.

<b>Моды не работают</b>
Не выдано право «удалять отправленные сообщения» — бот не может
подменить твоё сообщение на обработанное.

<b>Скам не удаляется, только уведомления</b>
Не выдано право «удалять любые сообщения».

<b>Не работает в группе / Избранном</b>
Так и должно быть: бизнес-боты живут только в диалогах
с другими людьми. Ни в группах, ни в Избранном, ни в чатах
с ботами они не работают — это ограничение Telegram.

<b>.fv не отправляет голосовое</b>
У тебя в Конфиденциальности запрещены голосовые в ЛС —
тогда и бот их слать не может. Настройки → Конфиденциальность
→ Голосовые сообщения → «Разрешать всегда» → добавь бота."""


# ═════════════════════════════════════════════════════════
#  МЕНЮ
# ═════════════════════════════════════════════════════════
def dot(x):
    return "🟢" if x else "🔴"


def main_kb(uid) -> InlineKeyboardMarkup:
    u = user(uid)
    rows = [
        [InlineKeyboardButton(text="🛡 Антискам", callback_data="m:scam")],
        [InlineKeyboardButton(text="🔪 Фильтр сообщений", callback_data="m:filter")],
        [InlineKeyboardButton(text=f"{dot(u['antidelete'])} Анти-делит", callback_data="m:ad")],
        [InlineKeyboardButton(text=f"{dot(u['save_media'])} Архив медиа", callback_data="m:sm")],
        [InlineKeyboardButton(text=f"{dot(u['save_own'])} Архивировать свои медиа",
                              callback_data="m:so")],
        [InlineKeyboardButton(text=f"🌸 Мод: {u['mode'] or 'выкл'}", callback_data="m:mode")],
        [InlineKeyboardButton(text="❓ Команды", callback_data="m:help")],
    ]
    if uid in ADMINS:
        rows.append([InlineKeyboardButton(text="🛠 Админка", callback_data="adm:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def scam_kb(uid) -> InlineKeyboardMarkup:
    a = user(uid)["antiscam"]
    t = [("enabled", "Антискам"), ("new_dialog", "Уведомления о новых диалогах"),
         ("unknown_bot", "Уведомления о незнакомых ботах"),
         ("scam", "Уведомления о скамерах"),
         ("exec_files", "Уведомления об исполняемых файлах")]
    rows = [[InlineKeyboardButton(text=f"{dot(a[k])} {lbl}", callback_data=f"tg:antiscam:{k}")]
            for k, lbl in t]
    rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="m:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def filter_kb(uid) -> InlineKeyboardMarkup:
    f = user(uid)["filter"]
    t = [("enabled", "Включено"), ("delete", "Удалять сообщение"),
         ("numbers", "Номера"), ("links", "Ссылки"), ("buttons", "Кнопки"),
         ("words_on", "Определённые слова")]
    rows = [[InlineKeyboardButton(text=f"{dot(f[k])} {lbl}", callback_data=f"tg:filter:{k}")]
            for k, lbl in t]
    rows.append([InlineKeyboardButton(text=f"📝 Список слов ({len(f['words'])})",
                                      callback_data="m:words")])
    rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="m:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def start(m: Message):
    user(m.from_user.id, m.from_user)
    ok = conn_of(m.from_user.id) is not None
    st = "✅ подключён" if ok else ("⚠️ не подключён\nНастройки → Telegram для бизнеса → Чат-боты")
    await m.answer(f"♡ <b>Karzen Bot</b>\n\nСтатус: {st}", reply_markup=main_kb(m.from_user.id))


@dp.callback_query(F.data.startswith("tg:"))
async def toggle(cb: CallbackQuery):
    _, section, key = cb.data.split(":")
    u = user(cb.from_user.id)
    u[section][key] = not u[section][key]
    save()
    kb = scam_kb(cb.from_user.id) if section == "antiscam" else filter_kb(cb.from_user.id)
    await cb.message.edit_reply_markup(reply_markup=kb)
    await cb.answer()


@dp.callback_query(F.data.startswith("m:"))
async def menu(cb: CallbackQuery):
    what = cb.data.split(":")[1]
    uid = cb.from_user.id
    u = user(uid, cb.from_user)
    back = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="‹ Назад", callback_data="m:root")]])
    if what == "check":
        me = await bot.get_me()
        if conn_of(uid):
            r = None
            await cb.message.edit_text(
                "✅ <b>Подключено!</b>\n\nБот теперь работает в выбранных чатах.\n"
                "Ниже — настройки, всё уже включено по умолчанию.",
                reply_markup=main_kb(uid))
            await cb.answer("Есть контакт!")
        else:
            await cb.answer("Пока не вижу подключения — проверь шаг 3", show_alert=True)
        return
    if what == "trouble":
        await cb.message.edit_text(TROUBLE, reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔄 Проверить",
                                                   callback_data="m:check")]]))
        await cb.answer()
        return
    if what == "scam":
        await cb.message.edit_text(
            "🛡 <b>Антискам</b>\n\nПроверяет первые сообщения от новых собеседников: "
            "предложения работы, крипта, фишинг, просьбы денег, ссылки, кнопки, "
            "исполняемые файлы. При срабатывании — уведомление тебе в этот чат.\n\n"
            f"Поймано всего: <b>{u['caught']}</b>", reply_markup=scam_kb(uid))
    elif what == "filter":
        await cb.message.edit_text(
            "🔪 <b>Фильтр сообщений</b>\n\nУдаляет первые сообщения от тех, с кем у тебя "
            "не было диалога, если они похожи на спам или развод. Текст удалённого "
            "придёт тебе сюда.\n\n⚠️ Нужны права «удалять любые сообщения».",
            reply_markup=filter_kb(uid))
    elif what == "words":
        lst = ", ".join(u["filter"]["words"]) or "пусто"
        await cb.message.edit_text(
            f"📝 <b>Стоп-слова</b> ({len(u['filter']['words'])})\n\n{lst}\n\n"
            f"Добавить: <code>.word +слово</code>\nУбрать: <code>.word -слово</code>",
            reply_markup=back)
    elif what in ("ad", "sm", "so"):
        key = {"ad": "antidelete", "sm": "save_media", "so": "save_own"}[what]
        u[key] = not u[key]
        save(uid)
        await cb.message.edit_reply_markup(reply_markup=main_kb(uid))
    elif what == "mode":
        rows = [[InlineKeyboardButton(text=x, callback_data=f"sm:{x}")] for x in MODES]
        rows += [[InlineKeyboardButton(text="🔕 Выключить", callback_data="sm:off")],
                 [InlineKeyboardButton(text="‹ Назад", callback_data="m:root")]]
        await cb.message.edit_text("🌸 Выбери стиль:",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif what == "help":
        await cb.message.edit_text(CMD_HELP, reply_markup=back)
    else:
        await cb.message.edit_text("♡ <b>Karzen Bot</b>", reply_markup=main_kb(uid))
    await cb.answer()


@dp.callback_query(F.data.startswith("sm:"))
async def setmode(cb: CallbackQuery):
    x = cb.data.split(":")[1]
    user(cb.from_user.id)["mode"] = None if x == "off" else x
    save()
    await cb.message.edit_text("♡ <b>Karzen Bot</b>", reply_markup=main_kb(cb.from_user.id))
    await cb.answer("Сохранено")


# ═════════════════════════════════════════════════════════
#  АДМИНКА (модерация)
# ═════════════════════════════════════════════════════════
def is_admin(uid):
    return uid in ADMINS


def stats_text() -> str:
    d = st.stats()
    return (f"📊 <b>Статистика</b>\n\n"
            f"👥 Всего: <b>{d['total']}</b>\n"
            f"🔌 Подключено: <b>{d['connected']}</b>\n"
            f"🆕 За неделю: <b>{d['week']}</b>\n"
            f"🔥 Актив 24ч: <b>{d['day']}</b>\n"
            f"⛔ Забанено: <b>{d['banned']}</b>\n"
            f"🔪 Поймано спама: <b>{d['caught']}</b> (за сутки: {d['catches_day']})\n"
            f"⌨️ Команд: <b>{d['cmds']}</b>")


def users_page(page=0, per=8):
    rows, total = st.page(page * per, per)
    pages = max(1, (total + per - 1) // per)
    page = max(0, min(page, pages - 1))
    if page * per != (page * per):
        rows, total = st.page(page * per, per)
    lines = []
    for r in rows:
        badge = "⛔" if r["banned"] else ("🔌" if conn_of(r["uid"]) else "▫️")
        un = f"@{r['username']}" if r["username"] else "—"
        lines.append(f"{badge} <code>{r['uid']}</code> · {(r['name'] or '?')[:18]} · {un} "
                     f"· 🔪{r['caught']}")
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="‹", callback_data=f"adm:users:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="adm:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="›", callback_data=f"adm:users:{page+1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        nav, [InlineKeyboardButton(text="‹ Админка", callback_data="adm:root")]])
    return (f"👥 <b>Пользователи</b>\n\n{chr(10).join(lines) or 'пусто'}\n\n"
            f"<i>/u id · /ban id · /unban id · /bc текст</i>"), kb


ADMIN_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
    [InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:users:0")],
    [InlineKeyboardButton(text="‹ Назад", callback_data="m:root")],
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
    elif p[1] != "noop":
        await cb.message.edit_text("🛠 <b>Админка</b>", reply_markup=ADMIN_KB)
    await cb.answer()


@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    if is_admin(m.from_user.id):
        await m.answer(stats_text(), reply_markup=ADMIN_KB)


@dp.message(Command("u"))
async def cmd_u(m: Message):
    if not is_admin(m.from_user.id):
        return
    a = m.text.split()
    if len(a) < 2:
        return await m.answer("<code>/u user_id</code>")
    try:
        u = user(int(a[1]))
    except ValueError:
        return await m.answer("Нужен числовой id.")
    await m.answer(
        f"👤 <b>{u['name']}</b> (@{u['username'] or '—'})\n🆔 <code>{a[1]}</code>\n"
        f"🔌 Подключён: {'да' if conn_of(a[1]) else 'нет'}\n"
        f"⛔ Бан: {'да' if u['banned'] else 'нет'}\n"
        f"🛡 Антискам: {'вкл' if u['antiscam']['enabled'] else 'выкл'} · "
        f"🔪 Фильтр: {'вкл' if u['filter']['enabled'] else 'выкл'} · поймано {u['caught']}\n"
        f"💬 Диалогов: {st.counts(int(a[1]))[0]} · ✅ доверенных: {st.counts(int(a[1]))[1]}\n"
        f"⌨️ Команд: {u['cmds']}\n"
        f"📅 С {ts_local(u['first_seen']):%d.%m.%Y} · "
        f"был {ts_local(u['last_seen']):%d.%m %H:%M}")


@dp.message(Command("find"))
async def cmd_find(m: Message):
    if not is_admin(m.from_user.id):
        return
    a = m.text.split(maxsplit=1)
    if len(a) < 2:
        return await m.answer("<code>/find имя|@юзернейм|id</code>")
    rows = st.find(a[1])
    if not rows:
        return await m.answer("Ничего не найдено.")
    await m.answer("🔎 <b>Найдено:</b>\n" + "\n".join(
        f"• <code>{r['uid']}</code> · {(r['name'] or '?')[:20]} · "
        f"@{r['username'] or '—'} · 🔪{r['caught']}" for r in rows))


@dp.message(Command("log"))
async def cmd_log(m: Message):
    """Последние срабатывания защиты — свои или чужие (для админа)."""
    target = m.from_user.id
    a = m.text.split()
    if len(a) > 1 and is_admin(m.from_user.id):
        target = int(a[1])
    rows = st.recent_catches(target, 10)
    if not rows:
        return await m.answer("📭 Пока ничего не поймано.")
    out = []
    for r in rows:
        when = ts_local(r["ts"]).strftime("%d.%m %H:%M")
        mark = "🗑" if r["action"] == "deleted" else "⚠️"
        out.append(f"{mark} <b>{when}</b> · <code>{r['peer']}</code> · "
                   f"{r['reason']} [{r['score']}]\n<i>{(r['text'] or '')[:90]}</i>")
    await m.answer("🔪 <b>Последние срабатывания:</b>\n\n" + "\n\n".join(out))


@dp.message(Command("ban"))
async def cmd_ban(m: Message):
    if not is_admin(m.from_user.id):
        return
    a = m.text.split()
    if len(a) < 2:
        return await m.answer("<code>/ban user_id</code>")
    user(int(a[1]))["banned"] = True
    save(int(a[1]))
    st.flush()
    await m.answer(f"⛔ Забанен <code>{a[1]}</code>")


@dp.message(Command("unban"))
async def cmd_unban(m: Message):
    if not is_admin(m.from_user.id):
        return
    a = m.text.split()
    if len(a) < 2:
        return await m.answer("<code>/unban user_id</code>")
    user(int(a[1]))["banned"] = False
    save(int(a[1]))
    st.flush()
    await m.answer(f"✅ Разбанен <code>{a[1]}</code>")


@dp.message(Command("bc"))
async def cmd_bc(m: Message):
    if not is_admin(m.from_user.id):
        return
    t = m.text.split(maxsplit=1)
    if len(t) < 2:
        return await m.answer("<code>/bc текст</code>")
    ok = fail = 0
    uids = st.all_uids()
    status = await m.answer(f"📢 Рассылка на {len(uids)}...")
    for i, uid in enumerate(uids):
        try:
            await bot.send_message(uid, t[1])
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(1 if i % 25 == 0 else 0.05)
    await status.edit_text(f"📢 Готово: ✅ {ok} · ❌ {fail}")


# ═════════════════════════════════════════════════════════
async def on_startup():
    """Вешаем вебхук. drop_pending_updates выкидывает очередь, накопившуюся
    пока инстансы конфликтовали — иначе бот сразу захлебнётся старьём."""
    url = WEBHOOK_BASE.rstrip("/") + WEBHOOK_PATH
    await bot.set_webhook(
        url=url,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
        max_connections=40,
        allowed_updates=ALLOWED_UPDATES)
    info = await bot.get_webhook_info()
    logging.info("webhook: %s (ожидает: %s)", info.url, info.pending_update_count)


async def health(request):
    """Render сканирует порт и пингует сервис — отвечаем 200."""
    return web.json_response({"ok": True, "users": st.stats()["total"]})


ALLOWED_UPDATES = ["message", "callback_query", "business_connection",
                   "business_message", "edited_business_message",
                   "deleted_business_messages"]


async def run_webhook():
    await storage_start()
    asyncio.create_task(st.flush_loop(5))
    dp.startup.register(on_startup)
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot,
                         secret_token=WEBHOOK_SECRET).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)     # ← порт, который ищет Render
    await site.start()
    me = await bot.get_me()
    print(f"♡ @{me.username} на вебхуках, порт {PORT}. Юзеров: {st.stats()['total']}")
    await asyncio.Event().wait()                     # держим процесс


async def run_polling():
    await storage_start()
    asyncio.create_task(st.flush_loop(5))
    # если раньше стоял вебхук — снимаем, иначе Telegram не отдаст апдейты
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    print(f"♡ @{me.username} на поллинге. Юзеров: {st.stats()['total']}")
    await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)


async def main():
    try:
        if WEBHOOK_BASE:
            await run_webhook()
        else:
            await run_polling()
    finally:
        await storage_stop()      # дописать хвост перед выходом


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
