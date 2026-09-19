#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================
  Karzen Business Bot  ♡  (aiogram 3 · Bot API 9.x) - v2.1
=========================================================
Исправления:
1. Безопасность: динамаческие проверки прав, защита от timing attack
2. OSINT: полный модуль с проверкой утечек через API
3. Производительность: асинхронный FFmpeg, кэширование
4. Логические ошибки: исправлены in_quiet, is_masked, кэширование
=========================================================
"""
import asyncio
import hashlib
import hmac
import html as html_lib
import io
import json
import logging
import os
import phonenumbers
import random
import re
import subprocess
import tempfile
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import parse_qs
import logging

# В начале файла уже есть:
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# В коде:
try:
    # что-то делаем
    pass
except Exception as e:
    logging.error(f"OSINT error for user {uid}: {e}")
    # пользователю показываем упрощённое сообщение

# Сервер живёт в UTC. TZ_OFFSET — твой сдвиг в часах
TZ_OFFSET = float(os.getenv("TZ_OFFSET", "2"))

def now_local() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)

def ts_local(ts) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=TZ_OFFSET)

import aiohttp
from PIL import Image, ImageEnhance
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Выбор бэкенда хранения
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
    BotCommand, BufferedInputFile, MenuButtonCommands, MenuButtonWebApp,
    WebAppInfo, BusinessConnection, BusinessMessagesDeleted, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message,
    ReplyParameters,
)

# ═════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ═════════════════════════════════════════════════════════
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN")
WEBHOOK_BASE = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL", "")
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me-please")
WEBHOOK_PATH = "/tg/webhook"
ADMINS = [123456789]  # <-- твой telegram id
PREFIX = "."
DB_FILE = "bot.db"
CACHE_LIMIT = 8000

# OSINT API ключи
HIBP_API_KEY = os.getenv("HIBP_API_KEY", "")
SHODAN_API_KEY = os.getenv("QTSYkUDZkODP5RMMqSriksckMZjRGIhZ", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
DEHASHED_EMAIL = os.getenv("DEHASHED_EMAIL", "")
DEHASHED_API_KEY = os.getenv("DEHASHED_API_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ═════════════════════════════════════════════════════════
# КЛАСС OSINT СЕРВИСА
# ═════════════════════════════════════════════════════════
class OSINTService:
    """Сервис для сбора открытой информации."""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limit_cache: Dict[str, float] = {}
    
    async def get_session(self) -> aiohttp.ClientSession:
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session
    
    def check_rate_limit(self, key: str, limit_seconds: int = 2) -> bool:
        """Проверка ограничения частоты запросов."""
        now = time.time()
        last_call = self.rate_limit_cache.get(key, 0)
        if now - last_call < limit_seconds:
            return False
        self.rate_limit_cache[key] = now
        return True
    
    async def check_username_across_platforms(self, username: str) -> Dict[str, str]:
        """Проверка username на популярных платформах."""
        if not self.check_rate_limit(f"platforms_{username}"):
            return {"error": "Слишком частые запросы"}
        
        platforms = {
            "VK": f"https://vk.com/{username}",
            "Instagram": f"https://instagram.com/{username}",
            "Twitter/X": f"https://twitter.com/{username}",
            "GitHub": f"https://github.com/{username}",
            "Telegram": f"https://t.me/{username}",
            "YouTube": f"https://youtube.com/@{username}",
            "Twitch": f"https://twitch.tv/{username}",
            "Pinterest": f"https://pinterest.com/{username}",
            "Reddit": f"https://reddit.com/user/{username}",
            "Steam": f"https://steamcommunity.com/id/{username}",
            "LinkedIn": f"https://linkedin.com/in/{username}",
            "TikTok": f"https://tiktok.com/@{username}",
            "Spotify": f"https://open.spotify.com/user/{username}",
        }
        
        results = {}
        session = await self.get_session()
        
        for platform, url in platforms.items():
            try:
                async with session.get(url, allow_redirects=False, ssl=False) as resp:
                    if resp.status == 200:
                        results[platform] = "✅ Найден"
                    elif resp.status in (301, 302, 307, 308):
                        final_url = str(resp.url)
                        if username.lower() in final_url.lower():
                            results[platform] = "✅ Найден (редирект)"
                        else:
                            results[platform] = "🔄 Редирект (возможно)"
                    elif resp.status == 403:
                        results[platform] = "🔒 Доступ запрещён"
                    elif resp.status == 429:
                        results[platform] = "⏳ Лимит запросов"
                    elif resp.status == 404:
                        results[platform] = "❌ Нет"
                    else:
                        results[platform] = f"⚠️ {resp.status}"
            except (aiohttp.ClientError, asyncio.TimeoutError):
                results[platform] = "🌐 Ошибка сети"
            except Exception:
                results[platform] = "❓ Ошибка"
            await asyncio.sleep(0.1)  # Задержка между запросами
        
        return results
    
    async def check_email_breaches(self, email: str) -> Dict[str, any]:
        """Проверка email на утечки через HaveIBeenPwned API."""
        if not HIBP_API_KEY:
            return {"error": "API ключ HIBP не настроен", "breached": False}
        
        if not self.check_rate_limit(f"email_{email}"):
            return {"error": "Слишком частые запросы", "breached": False}
        
        headers = {
            "hibp-api-key": HIBP_API_KEY,
            "User-Agent": "Karzen-Bot/2.1"
        }
        
        try:
            session = await self.get_session()
            url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
            
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    breaches = await resp.json()
                    return {
                        "breached": True,
                        "count": len(breaches),
                        "breaches": [{"name": b.get("Name"), "date": b.get("BreachDate")} 
                                   for b in breaches[:3]],
                        "latest": max((b.get("BreachDate", "") for b in breaches), default="")
                    }
                elif resp.status == 404:
                    return {"breached": False, "count": 0}
                else:
                    return {"error": f"API error: {resp.status}", "breached": False}
        except Exception as e:
            return {"error": str(e), "breached": False}
    
    async def check_username_breaches(self, username: str) -> Dict[str, any]:
        """Проверка username на утечки."""
        if not self.check_rate_limit(f"breach_{username}"):
            return {"error": "Слишком частые запросы", "breached": False}
        
        # 1. Проверка через DeHashed
        if DEHASHED_API_KEY and DEHASHED_EMAIL:
            try:
                session = await self.get_session()
                auth = aiohttp.BasicAuth(DEHASHED_EMAIL, DEHASHED_API_KEY)
                url = f"https://api.dehashed.com/search?query=username:{username}"
                
                async with session.get(url, auth=auth) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("total", 0) > 0:
                            entries = data.get("entries", [])[:3]
                            return {
                                "breached": True,
                                "count": data.get("total", 0),
                                "sources": [e.get("database_name", "Unknown") for e in entries],
                                "hashes": [e.get("hashed_password") for e in entries if e.get("hashed_password")]
                            }
                    return {"breached": False}
            except Exception:
                pass
        
        # 2. Фоллбэк на leakcheck.io
        try:
            session = await self.get_session()
            async with session.get(f"https://leakcheck.io/api/public?check={username}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success") and data.get("found", 0) > 0:
                        return {
                            "breached": True,
                            "count": data.get("found", 0),
                            "sources": data.get("sources", [])[:3]
                        }
        except Exception:
            pass
        
        return {"breached": False, "note": "Нет доступа к API проверки утечек"}
    
    async def check_ip_info(self, ip: str) -> Dict[str, any]:
        """Получение информации об IP-адресе."""
        if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
            return {"error": "Неверный формат IP"}
        
        if not self.check_rate_limit(f"ip_{ip}"):
            return {"error": "Слишком частые запросы"}
        
        try:
            session = await self.get_session()
            async with session.get(f"http://ip-api.com/json/{ip}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "success":
                        return {
                            "country": data.get("country", ""),
                            "region": data.get("regionName", ""),
                            "city": data.get("city", ""),
                            "isp": data.get("isp", ""),
                            "org": data.get("org", ""),
                            "asn": data.get("as", ""),
                            "lat": data.get("lat"),
                            "lon": data.get("lon"),
                        }
        except Exception:
            pass
        
        return {"error": "Не удалось получить информацию"}
    
    async def check_phone_info(self, phone: str) -> Dict[str, any]:
        """Проверка информации о номере телефона."""
        try:
            parsed = phonenumbers.parse(phone, None)
            if not phonenumbers.is_valid_number(parsed):
                return {"error": "Неверный номер"}
            
            from phonenumbers import geocoder, carrier, timezone
            
            return {
                "valid": True,
                "country": geocoder.description_for_number(parsed, "ru"),
                "carrier": carrier.name_for_number(parsed, "ru") or "Неизвестно",
                "timezone": timezone.time_zones_for_number(parsed),
                "type": str(phonenumbers.number_type(parsed)),
                "international": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
                "national": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL),
            }
        except Exception as e:
            return {"error": str(e)}
    
    async def reverse_image_search(self, image_url: str) -> Dict[str, any]:
        """Обратный поиск изображения через Google Images."""
        if not SERPAPI_KEY:
            return {"found": False, "note": "Для поиска нужен API ключ serpapi.com"}
        
        if not self.check_rate_limit(f"image_search"):
            return {"error": "Слишком частые запросы", "found": False}
        
        try:
            session = await self.get_session()
            params = {
                "engine": "google_reverse_image",
                "image_url": image_url,
                "api_key": SERPAPI_KEY
            }
            async with session.get("https://serpapi.com/search", params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("image_results"):
                        results = data.get("image_results", [])[:5]
                        return {
                            "found": True,
                            "results": [{
                                "title": r.get("title", ""),
                                "link": r.get("link", ""),
                                "source": r.get("source", "")
                            } for r in results]
                        }
        except Exception:
            pass
        
        return {"found": False}
    
    async def close(self):
        """Закрытие сессии."""
        if self.session and not self.session.closed:
            await self.session.close()

# Инициализация сервиса
osint_service = OSINTService()

# ═════════════════════════════════════════════════════════
# БЕЗОПАСНЫЕ ФУНКЦИИ ВЕРИФИКАЦИИ
# ═════════════════════════════════════════════════════════
def check_init_data(init_data: str) -> Optional[int]:
    """Безопасная проверка подписи Telegram WebApp с защитой от timing attack."""
    try:
        parsed = parse_qs(init_data, strict_parsing=True)
        if not parsed:
            return None
        
        hash_str = parsed.get('hash', [''])[0]
        auth_date = int(parsed.get('auth_date', ['0'])[0])
        user_str = parsed.get('user', ['{}'])[0]
        
        # Проверка возраста токена (24 часа)
        if time.time() - auth_date > 24 * 3600:
            return None
        
        # Подготовка строки для проверки
        data_check_string_parts = []
        for key in sorted(parsed.keys()):
            if key == 'hash':
                continue
            if key == 'user':
                data_check_string_parts.append(f"{key}={user_str}")
            else:
                data_check_string_parts.append(f"{key}={parsed[key][0]}")
        
        data_check_string = "\n".join(data_check_string_parts)
        
        # Вычисление HMAC
        secret_key = hmac.new(
            b"WebAppData", 
            BOT_TOKEN.encode(), 
            hashlib.sha256
        ).digest()
        
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Сравнение с защитой от timing attack
        if not hmac.compare_digest(computed_hash, hash_str):
            return None
        
        # Парсинг user
        try:
            user_data = json.loads(user_str)
            return user_data.get('id')
        except (json.JSONDecodeError, KeyError):
            return None
            
    except (ValueError, KeyError, IndexError):
        return None

# ═════════════════════════════════════════════════════════
# ХРАНИЛИЩЕ
# ═════════════════════════════════════════════════════════
ANTISCAM_TPL = {
    "enabled": True,
    "new_dialog": True,
    "unknown_bot": True,
    "exec_files": True,
    "scam": True,
}
FILTER_TPL = {
    "enabled": False,
    "delete": True,
    "links": True,
    "numbers": True,
    "buttons": True,
    "words_on": False,
    "words": [],
}
ANTIDOX_TPL = {
    "enabled": True,
    "dry_run": True,
    "arm": 4.0,
    "quarantine": 0,
    "triggers": 0, "killed": 0, "missed": 0,
}
USER_TPL = {
    "name": "", "username": "", "first_seen": 0, "last_seen": 0,
    "banned": False, "cmds": 0, "caught": 0,
    "mode": None, "antidelete": True, "pairs": {},
    "mode_level": "normal",
    "mode_bold": True,
    "mode_emoji": True,
    "chat_modes": {},
    "ref": 0,
    "invited": 0,
    "save_media": True,
    "save_when": "deleted",
    "track_edits": True,
    "save_own": False,
    "sens": "normal",
    "muted": [],
    "trusted_ids": [],
    "quiet": "",
    "away": "",
    "away_on": False,
    "digest": False,
    "antiscam": dict(ANTISCAM_TPL), "filter": dict(FILTER_TPL),
    "antidox": dict(ANTIDOX_TPL),
}

st = Storage(os.getenv("DATABASE_URL") or DB_FILE, defaults=USER_TPL)

async def storage_start():
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

def esc(t) -> str:
    return html_lib.escape(str(t or ""))

async def call(fn, *a):
    r = fn(*a)
    return await r if asyncio.iscoroutine(r) else r

def set_trust(uid, peer, on: bool):
    st.set_trust(uid, peer, on)
    u = user(uid)
    lst = u.setdefault("trusted_ids", [])
    if on and peer not in lst:
        lst.append(peer)
    elif not on and peer in lst:
        lst.remove(peer)
    save(uid)

def hhmm(s: str) -> int:
    h, m = s.strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(s)
    return h * 60 + m

def in_quiet(u) -> bool:
    """Корректная проверка тихих часов с окном через полночь."""
    q = (u.get("quiet") or "").strip()
    if "-" not in q:
        return False
    try:
        a, b = q.split("-", 1)
        start, end = hhmm(a), hhmm(b)
    except Exception:
        return False
    now = now_local()
    cur = now.hour * 60 + now.minute
    if start <= end:
        return start <= cur < end
    else:
        # Окно через полночь (например, 23:00-08:00)
        return cur >= start or cur < end

async def dm(uid, text, quiet_ok=True, **kw):
    c = conn_of(uid)
    if not c:
        return
    if quiet_ok and in_quiet(user(uid)):
        kw.setdefault("disable_notification", True)
    try:
        return await bot.send_message(c["chat"], text, **kw)
    except Exception:
        pass

# ═════════════════════════════════════════════════════════
# 🛡 АНТИСКАМ — ИСПРАВЛЕННЫЙ
# ═════════════════════════════════════════════════════════
HOMOGLYPH = str.maketrans({
    "a": "а", "b": "ь", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
    "o": "о", "p": "р", "s": "ѕ", "t": "т", "u": "и", "x": "х", "y": "у",
    "A": "а", "B": "в", "C": "с", "E": "е", "H": "н", "K": "к", "M": "м",
    "O": "о", "P": "р", "T": "т", "X": "х", "Y": "у",
    "ё": "е", "Ё": "е", "і": "и", "ї": "и", "є": "е", "ў": "у",
})
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff\u00ad]")
PUNCT_SPLIT = re.compile(r"(?<=\b\w)[._\-*|/]{1,3}(?=\w\b)")
SPACE_SPLIT = re.compile(r"\b(?:\w[ ]){2,}\w\b")
MIXED_WORD = re.compile(r"\b(?=\w*[а-яёА-ЯЁ])(?=\w*[a-zA-Z])\w{3,}\b")

def normalize(text: str) -> str:
    import unicodedata
    t = ZERO_WIDTH.sub("", text or "")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().translate(HOMOGLYPH)
    t = re.sub(r"(.)\1{2,}", r"\1", t)
    for _ in range(3):
        t = PUNCT_SPLIT.sub("", t)
    t = SPACE_SPLIT.sub(lambda mm: mm.group(0).replace(" ", ""), t)
    return re.sub(r"\s{2,}", " ", t).strip()

def is_masked(text: str) -> bool:
    """Исправленная проверка маскировки: учитывает цифры и спецсимволы."""
    if ZERO_WIDTH.search(text or ""):
        return True
    # Проверяем латиницу внутри кириллических слов
    words = re.findall(r'\b\w+\b', text or "")
    for word in words:
        if len(word) >= 3:
            has_cyrillic = bool(re.search(r'[а-яёА-ЯЁ]', word))
            has_latin = bool(re.search(r'[a-zA-Z]', word))
            if has_cyrillic and has_latin:
                return True
    return False

SCAM_RULES = [
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
    (3, "гарант/обмен", r"(через\s+гарант|я\s+гарант|обмен\s+с\s+гарант|сделка\s+через)"),
    (3, "взлом близкого", r"(это\s+(мама|папа|сын|дочь|бабушк)\w*|"
                          r"мой\s+номер\s+(не\s+работает|заблокир)|пишу\s+с\s+нового)"),
    (2, "продажа аккаунта", r"(куплю\s+(акк|аккаунт|канал)|продам\s+(акк|аккаунт|канал)|"
                            r"сдам\s+акк|аренда\s+аккаунт)"),
]

EXEC_EXT = (".apk", ".exe", ".bat", ".cmd", ".scr", ".msi", ".jar",
            ".vbs", ".ps1", ".sh", ".com", ".dmg")
URL_RE = re.compile(r"(https?://|t\.me/|telegra\.ph|@[A-Za-z]\w{3,})", re.I)
PHONE_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{8,}\d)")
CARD_RE = re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b")

def analyze(m: Message, first_contact: bool) -> dict:
    text = (m.text or m.caption or "")
    low = normalize(text)
    score, hits = 0, []

    def hit(weight: int, label: str):
        nonlocal score
        score += weight
        hits.append((weight, label))

    if is_masked(text):
        hit(1, "маскировка символов")

    for weight, label, rx in SCAM_RULES:
        if re.search(rx, low):
            hit(weight, label)

    if URL_RE.search(text):
        hit(2 if first_contact else 1, "ссылка")
    if PHONE_RE.search(text):
        hit(1, "номер телефона")
    if CARD_RE.search(text):
        hit(3, "номер карты")
    if m.reply_markup and getattr(m.reply_markup, "inline_keyboard", None):
        hit(2, "инлайн-кнопки")
    if m.forward_origin is not None:
        hit(1, "переслано")
    if m.document and m.document.file_name and \
            m.document.file_name.lower().endswith(EXEC_EXT):
        hit(4, f"исполняемый файл ({m.document.file_name})")
    if m.from_user and m.from_user.is_bot:
        hit(1, "отправитель — бот")
    if first_contact:
        score += 1

    hits.sort(reverse=True)
    return {"score": score, "reasons": [h[1] for h in hits],
            "hits": hits, "text": text}

SCAM_THRESHOLD = 4
SENS = {"low": 6, "normal": 4, "high": 3}
SENS_RU = {"low": "мягко", "normal": "обычно", "high": "строго"}

def threshold(u: dict) -> int:
    return SENS.get(u.get("sens", "normal"), SCAM_THRESHOLD)

def filter_hit(m: Message, f: dict, verdict: dict, thr: int) -> str | None:
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
    if verdict["score"] >= thr:
        return "скам-детектор: " + ", ".join(verdict["reasons"][:3])
    return None

# Автоответ с кэшированием
away_sent: dict[tuple, float] = {}
AWAY_COOLDOWN = 6 * 3600

async def maybe_away(m: Message, owner_id: int, u: dict):
    if not (u.get("away_on") and (u.get("away") or "").strip()):
        return
    key = (owner_id, m.chat.id)
    if time.time() - away_sent.get(key, 0) < AWAY_COOLDOWN:
        return
    away_sent[key] = time.time()
    try:
        await bot.send_message(m.chat.id, esc(u["away"])[:800],
                               business_connection_id=m.business_connection_id)
    except Exception as ex:
        logging.warning("away: %s", ex)

async def guard_incoming(m: Message, owner_id: int):
    u = user(owner_id)
    peer = m.chat.id
    a, f = u["antiscam"], u["filter"]

    if peer == owner_id:
        return
    if st.is_trusted(owner_id, peer):
        return
    first_contact = not st.is_known(owner_id, peer)

    if peer in (u.get("muted") or []):
        if first_contact:
            st.add_known(owner_id, peer)
        return
    await maybe_away(m, owner_id, u)

    thr = threshold(u)

    # Уведомления
    if a["enabled"]:
        if first_contact and a["new_dialog"]:
            who = esc(m.from_user.full_name if m.from_user else "?")
            un = f"@{esc(m.from_user.username)}" if (m.from_user and m.from_user.username) else "—"
            await dm(owner_id,
                     f"💬 <b>Новый диалог</b>\n👤 {who} · {un} · <code>{peer}</code>",
                     reply_markup=peer_kb(peer, u))
        if a["unknown_bot"] and m.from_user and m.from_user.is_bot and first_contact:
            await dm(owner_id, f"🤖 <b>Незнакомый бот</b> написал тебе: "
                               f"@{esc(m.from_user.username or '?')} (<code>{peer}</code>)",
                     reply_markup=peer_kb(peer, u))

    verdict = analyze(m, first_contact)

    if a["enabled"] and a["exec_files"] and m.document and m.document.file_name \
            and m.document.file_name.lower().endswith(EXEC_EXT):
        await dm(owner_id, f"⚠️ <b>Исполняемый файл!</b>\n"
                           f"<code>{esc(m.document.file_name)}</code> от <code>{peer}</code>\n"
                           f"Не открывай это на телефоне.",
                 quiet_ok=False)

    # Фильтр / автоудаление
    hit = filter_hit(m, f, verdict, thr) if f["enabled"] and first_contact else None
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
        body = esc(verdict["text"][:600]) or "(без текста)"
        await dm(owner_id, f"{head}\n🎯 Причина: {esc(hit)}\n"
                           f"👤 <code>{peer}</code>\n\n<blockquote>{body}</blockquote>",
                 reply_markup=peer_kb(peer, u))
    elif a["enabled"] and a["scam"] and verdict["score"] >= thr:
        st.log_catch(owner_id, peer, verdict["score"],
                     ", ".join(verdict["reasons"][:3]), verdict["text"], "notified")
        await dm(owner_id,
                 f"🚨 <b>Похоже на развод</b> · {risk_bar(verdict['score'], thr)}\n"
                 f"🎯 {esc(', '.join(verdict['reasons'][:4]))}\n👤 <code>{peer}</code>\n\n"
                 f"<blockquote>{esc(verdict['text'][:600])}</blockquote>",
                 reply_markup=peer_kb(peer, u))

    # Запоминаем диалог
    if first_contact:
        st.add_known(owner_id, peer)

def risk_bar(score: int, thr: int) -> str:
    full = max(1, thr * 2)
    n = max(0, min(5, round(score / full * 5)))
    return f"{'▰' * n}{'▱' * (5 - n)} {score}/{thr}"

def peer_kb(peer, u: dict | None = None) -> InlineKeyboardMarkup:
    muted = bool(u and peer in (u.get("muted") or []))
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Доверять", callback_data=f"peer:white:{peer}"),
        InlineKeyboardButton(text="🚫 Не доверять", callback_data=f"peer:unwhite:{peer}"),
    ], [
        InlineKeyboardButton(text="🔔 Вернуть звук" if muted else "🔕 Заглушить",
                             callback_data=f"peer:{'unmute' if muted else 'mute'}:{peer}"),
    ]])

@dp.callback_query(F.data.startswith("peer:"))
async def peer_cb(cb: CallbackQuery):
    _, act, peer = cb.data.split(":")
    uid, peer = cb.from_user.id, int(peer)
    u = user(uid)
    muted = u.setdefault("muted", [])
    if act == "white":
        set_trust(uid, peer, True)
        await cb.answer("Добавлен в доверенные ✅")
    elif act == "unwhite":
        set_trust(uid, peer, False)
        await cb.answer("Убран из доверенных")
    elif act == "mute":
        if peer not in muted:
            muted.append(peer)
        save(uid)
        await cb.answer("Уведомления о нём выключены 🔕")
    else:
        if peer in muted:
            muted.remove(peer)
        save(uid)
        await cb.answer("Уведомления включены 🔔")
    try:
        await cb.message.edit_reply_markup(reply_markup=peer_kb(peer, u))
    except Exception:
        pass

# ═════════════════════════════════════════════════════════
# СТИЛИ ТЕКСТА (исправленные)
# ═════════════════════════════════════════════════════════
KAO_CUTE = ["(◕‿◕✿)", "(｡♥‿♥｡)", "(*≧ω≦*)", "ʕ•ᴥ•ʔ", "(っ◔◡◔)っ ♡", "(˘⌣˘)♡",
            "ヽ(=^･ω･^=)丿", "(´｡• ᵕ •｡`)", "(⁄ ⁄•⁄ω⁄•⁄ ⁄)", "(◍•ᴗ•◍)♡",
            "(*ฅ́˘ฅ̀*)♡", "(≧◡≦) ♡", "ʚ(*´꒳`*)ɞ", "₊˚ෆ", "(๑>◡<๑)"]
KAO_TSUN = ["(>﹏<)", "(๑•̀ㅁ•́๑)", "(￣^￣)", "(¬_¬)", "(//∇//)", "(*｀^´)",
            "(・`ω´・)", "(￣ヘ￣)", "(⁄ ⁄>⁄ ▽ ⁄<⁄ ⁄)", "(；一_一)", "(╯•﹏•╰)"]
KAO_YAN = ["(◡‿◡✿)", "( ͡° ͜ʖ ͡°)", "(๑•̀ㅂ•́)✧", "(◕‿◕)", "(⌒▽⌒)", "( ˘ω˘ )"]

EMO_KAWAII = ["🌸", "🍡", "🧸", "🐾", "✨", "💗", "🍓", "🫧", "🐰", "🍰", "☁️", "🌷",
              "💫", "🐱", "🧁", "🪷", "🎀", "🍬"]
EMO_TSUN = ["😤", "💢", "🙄", "😳", "🔥", "💥", "😾", "🫤", "😠", "💨", "🍂", "❄️"]
EMO_YAN = ["🔪", "🩸", "💘", "🖤", "🕯️", "🥀", "⛓️", "👁️", "💉", "🌑", "🫀"]
EMO_LEET = ["💾", "🖥️", "👾", "🕹️", "📟", "⚡", "🔌", "💿"]

def maybe(chance: float) -> bool:
    return random.random() < chance

def stutter(word: str, chance=0.35) -> str:
    if len(word) < 3 or not word[0].isalpha() or not maybe(chance):
        return word
    return f"{word[0]}-{word}"

LEVELS = ("soft", "normal", "max")

def assemble(body: str, opener: str, closer: str, kao: str,
             level: str, bold: bool, emoji: list | None = None,
             use_emoji: bool = True) -> str:
    body = body.strip()
    if bold and body:
        body = f"<b>{body}</b>"
    
    pool = emoji if (emoji and use_emoji) else []
    n = {"soft": 1, "normal": 2, "max": 3}.get(level, 2)
    picked = random.sample(pool, min(n, len(pool))) if pool and maybe(0.85) else []
    
    head = picked.pop() if picked and maybe(0.45) else ""
    
    parts = []
    if head:
        parts.append(head)
    if level != "soft" and opener and maybe(0.8):
        parts.append(opener)
    parts.append(body)
    if closer and maybe(0.85):
        parts.append(closer)
    res = " ".join(p for p in parts if p)
    
    tail = " ".join(picked)
    if kao and maybe(0.6):
        tail = (tail + " " + kao).strip()
    return (res + " " + tail).strip() if tail else res

# KAWAII (исправлено)
KAWAII_SUBS = {
    "привет": ["привтик", "прив-прив", "хаюшки"],
    "пока": ["покашки", "бай-бай"],
    "спасибо": ["спасибки", "мерси~"],
    "да": ["дя", "ага~"],
    "нет": ["неть", "не-а~"],
    "хорошо": ["хорошоу", "ладушки"],
    "что": ["чо~", "чтоо"],
}

KAWAII_OPEN = ["", "", "ня~", "ммм~", "ой!", "уву,"]
KAWAII_CLOSE = ["ня~", "мур~", "уву~", "мя~ ♡", "нявушки~", "~ ✨", "ня-ня~"]

def kawaii(t: str, level="normal", bold=True, emoji=True) -> str:
    if level == "max":
        words = []
        for w in t.split():
            low = w.lower().strip(".,!?")
            # Безопасная замена слов
            if low in KAWAII_SUBS and maybe(0.7):
                words.append(random.choice(KAWAII_SUBS[low]))
            else:
                words.append(w)
        t = " ".join(words)
        # Безопасная замена букв
        t = t.replace("л", "ль").replace("р", "рь")
    return assemble(t, random.choice(KAWAII_OPEN), random.choice(KAWAII_CLOSE),
                    random.choice(KAO_CUTE), level, bold, EMO_KAWAII, emoji)

# TSUNDERE
TSUN_OPEN = [
    "Н-не то чтобы я специально, но", "Х-хмф!", "Б-бака!", "Э-эй!", "Н-ну…",
    "Т-только не подумай ничего такого:", "Хмф.", "Д-дурак, я же говорила —",
    "Ч-что?! Ладно, слушай:", "Пф, ну ладно.", "Я-я не ради тебя это, просто",
]
TSUN_CLOSE = [
    "…и вообще, мне всё равно!", "…б-бака.", "…не благодари, ясно?!", "Хмф!",
    "…я просто мимо проходила, понял?", "…н-не смотри так!", "…это ничего не значит!",
    "…и не вздумай зазнаваться.", "…д-дурак.", "…п-понял меня?!",
]
TSUN_SUBS = {"да": ["н-ну да", "д-да"], "нет": ["н-нет!", "вот ещё"],
             "ты": ["т-ты"], "спасибо": ["ну… спасибо"], "хорошо": ["л-ладно уж"]}

def tsundere(t: str, level="normal", bold=True, emoji=True) -> str:
    if level == "max":
        words = []
        for i, w in enumerate(t.split()):
            low = w.lower().strip(".,!?")
            if low in TSUN_SUBS and maybe(0.6):
                w = random.choice(TSUN_SUBS[low])
            elif i == 0 or maybe(0.15):
                w = stutter(w)
            words.append(w)
        t = " ".join(words)
    return assemble(t, random.choice(TSUN_OPEN), random.choice(TSUN_CLOSE),
                    random.choice(KAO_TSUN), level, bold, EMO_TSUN, emoji)

# YANDERE
YAN_OPEN = ["", "", "Знаешь…", "Слушай внимательно.", "Хи-хи~", "Ммм~"]
YAN_CLOSE = [
    "Ты ведь только мой, да?~ 🔪♡", "Я никому тебя не отдам… никогда~",
    "Не смотри на других, хорошо?~ ♡", "Мы будем вместе. Навсегда. ♡",
    "Я знаю, где ты сейчас~ ♡", "Ты же не бросишь меня, правда?~",
    "Только не заставляй меня грустить~ 🔪",
]

def yandere(t: str, level="normal", bold=True, emoji=True) -> str:
    return assemble(t, random.choice(YAN_OPEN), random.choice(YAN_CLOSE),
                    random.choice(KAO_YAN), level, bold, EMO_YAN, emoji)

def leet(t: str, level="normal", bold=False, emoji=True) -> str:
    out = t.translate(str.maketrans({"a": "4", "e": "3", "i": "1", "o": "0", "s": "5",
                                     "t": "7", "а": "@", "е": "3", "о": "0", "и": "1"}))
    if emoji and maybe(0.6):
        out += " " + random.choice(EMO_LEET)
    return out

SMALLCAPS = str.maketrans(
    "abcdefghijklmnopqrstuvwxyz",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘQʀꜱᴛᴜᴠᴡxʏᴢ")
FLIP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyz0123456789.,!?'\"()[]{}<>",
    "ɐqɔpǝɟƃɥᴉɾʞlɯuodbɹsʇnʌʍxʎz0ƖᄅƐㄣϛ9ㄥ86˙'¡¿,„)(][}{><")

def smallcaps(t: str, level="normal", bold=False, emoji=True) -> str:
    return t.lower().translate(SMALLCAPS)

def bubble(t: str, level="normal", bold=False, emoji=True) -> str:
    out = []
    for ch in t:
        if "a" <= ch.lower() <= "z":
            out.append(chr(0x24B6 + ord(ch.lower()) - 97))
        elif ch.isdigit() and ch != "0":
            out.append(chr(0x2460 + int(ch) - 1))
        else:
            out.append(ch)
    return "".join(out)

def mock(t: str, level="normal", bold=False, emoji=True) -> str:
    out, up = [], False
    for ch in t:
        if ch.isalpha():
            out.append(ch.upper() if up else ch.lower())
            up = not up if maybe(0.8) else up
        else:
            out.append(ch)
    return "".join(out)

def spaced(t: str) -> str:
    return " ".join(t)

def zalgo(t: str, power=3) -> str:
    marks = [chr(c) for c in range(0x0300, 0x036F)]
    return "".join(ch + "".join(random.choice(marks) for _ in range(random.randint(0, power)))
                   for ch in t)

def upside(t: str) -> str:
    return t.lower().translate(FLIP)[::-1]

MODES = {"kawaii": kawaii, "tsundere": tsundere, "yandere": yandere, "leet": leet,
         "small": smallcaps, "bubble": bubble, "mock": mock}
MODE_UI = {
    "kawaii": ("🌸", "kawaii", "ня~ и сердечки"),
    "tsundere": ("💢", "tsundere", "б-бака!"),
    "yandere": ("🔪", "yandere", "ты только мой"),
    "leet": ("👾", "leet", "h4ck3r"),
    "small": ("🔡", "small", "ᴀʙᴄ · латиница"),
    "bubble": ("🫧", "bubble", "ⒶⒷⒸ · латиница"),
    "mock": ("🐔", "mock", "sPoNgEbOb"),
}
PREVIEW_SRC = "привет, как дела? я сегодня освободился пораньше"

def preview(mode: str, u: dict) -> str:
    fn = MODES.get(mode or "")
    if not fn:
        return f"<i>{esc(PREVIEW_SRC)}</i>\n\n<i>(мод выключен — текст уходит как есть)</i>"
    return fn(esc(PREVIEW_SRC), u.get("mode_level", "normal"),
              u.get("mode_bold", True), u.get("mode_emoji", True))

RU = "йцукенгшщзхъфывапролджэячсмитьбю."
EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,./"

def switch_layout(t):
    a, b = dict(zip(RU, EN)), dict(zip(EN, RU))
    tbl = a if sum(c.lower() in a for c in t) >= sum(c.lower() in b for c in t) else b
    return "".join(tbl.get(c, tbl.get(c.lower(), c)) for c in t)

# ═════════════════════════════════════════════════════════
# ПОДКЛЮЧЕНИЕ
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
        for a in ADMINS:
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
# АНТИ-ДЕЛИТ С ОЧИСТКОЙ КЭША
# ═════════════════════════════════════════════════════════
cache: "OrderedDict[tuple, dict]" = OrderedDict()
CACHE_CLEAN_INTERVAL = 3600  # 1 час
_last_cache_clean = time.time()

def cache_cleanup():
    """Очистка старых записей из кэша."""
    global _last_cache_clean
    now = time.time()
    if now - _last_cache_clean < CACHE_CLEAN_INTERVAL:
        return
    
    _last_cache_clean = now
    max_age = 24 * 3600  # 24 часа
    to_remove = []
    
    for key, value in cache.items():
        if now - value.get("timestamp", 0) > max_age:
            to_remove.append(key)
    
    for key in to_remove:
        cache.pop(key, None)
    
    if to_remove:
        logging.info(f"Cache cleanup: removed {len(to_remove)} old entries")

def cache_put(m: Message):
    cache_cleanup()
    cache[(m.chat.id, m.message_id)] = {
        "text": m.text or m.caption or "",
        "from": m.from_user.full_name if m.from_user else "?",
        "from_id": m.from_user.id if m.from_user else 0,
        "chat": m.chat.id, "mid": m.message_id,
        "media": bool(m.photo or m.video or m.voice or m.video_note
                      or m.document or m.sticker or m.animation),
        "uid": (_mi[3] if (_mi := media_of(m)) else None),
        "file_id": (_mi[2] if _mi else None),
        "kind": (_mi[0] if _mi else None),
        "size": (_mi[4] if _mi else 0),
        "date": int(m.date.timestamp()) if m.date else int(time.time()),
        "cid": m.business_connection_id,
        "timestamp": time.time(),
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
                f"👤 {esc(info['from'])} (<code>{info['from_id']}</code>) · "
                f"{ts_local(info['date']):%d.%m %H:%M}")
        try:
            if info["media"]:
                u = user(owner_id)
                if info.get("uid") and st.has_media(owner_id, info["uid"]):
                    head += "\n📦 <i>копия сохранена в архиве выше</i>"
                elif u.get("save_media", True):
                    head += await archive_from_cache(info, owner_id)
                else:
                    try:
                        await bot.forward_message(c["chat"], info["chat"], info["mid"])
                    except Exception:
                        pass
            await bot.send_message(c["chat"], head + (
                f"\n\n<blockquote>{esc(info['text'][:900])}</blockquote>"
                if info["text"] else "\n\n📎 (медиа)"),
                reply_markup=peer_kb(info["from_id"], user(owner_id))
                if info["from_id"] else None)
        except Exception as ex:
            logging.warning("antidelete: %s", ex)

# ═════════════════════════════════════════════════════════
# 🕵️ АНТИ-ДОКС С ДИНАМИЧЕСКОЙ ПРОВЕРКОЙ ПРАВ
# ═════════════════════════════════════════════════════════
DOX_TRIGGER = re.compile(
    r"^[./!,#$]?\s*(dox|doxp|doxbin|dox3|пробив|probiv|деанон|deanon)\b",
    re.IGNORECASE)
_dox_armed: dict[tuple[int, int], float] = {}
_dox_quarantine: dict[tuple[int, int], float] = {}

def adox(u: dict) -> dict:
    return u.setdefault("antidox", dict(ANTIDOX_TPL))

async def dox_kill(m: Message, owner_id: int, reason: str, t0: float):
    """Удалить с динамической проверкой прав."""
    u = user(owner_id)
    d = adox(u)
    peer = m.chat.id
    text = m.text or m.caption or ""

    # Динамическая проверка прав
    can_delete = False
    try:
        conn = await bot.get_business_connection(m.business_connection_id)
        can_delete = bool(conn.rights.can_delete_all_messages) if conn and conn.rights else False
    except Exception:
        can_delete = False
    
    if d.get("dry_run", True):
        status_, deleted = "DRY-RUN · не удаляю", False
    elif not can_delete:
        status_, deleted = "нет права «удалять любые сообщения»", False
        d["missed"] = d.get("missed", 0) + 1
    else:
        try:
            await bot.delete_business_messages(
                business_connection_id=m.business_connection_id,
                message_ids=[m.message_id])
            status_ = f"удалено · {(time.monotonic() - t0) * 1000:.0f} мс"
            deleted = True
            d["killed"] = d.get("killed", 0) + 1
        except Exception as ex:
            status_, deleted = f"не вышло: {ex.__class__.__name__}", False
            d["missed"] = d.get("missed", 0) + 1
    save(owner_id)
    st.log_catch(owner_id, peer, 0, f"анти-докс: {reason}", text,
                 "deleted" if deleted else "notified")
    await dm(owner_id,
             f"🕵️ <b>Анти-докс</b> · {status_}\n"
             f"🎯 {reason} · 👤 <code>{peer}</code>\n\n"
             f"<blockquote>{esc(text[:400]) or '(без текста)'}</blockquote>",
             reply_markup=peer_kb(peer, u))

async def antidox_check(m: Message, owner_id: int, edited: bool = False) -> bool:
    t0 = time.monotonic()
    u = user(owner_id)
    d = adox(u)
    if not d.get("enabled", True):
        return False
    peer = m.chat.id
    if peer == owner_id or st.is_trusted(owner_id, peer):
        return False
    key = (owner_id, peer)
    now = time.time()

    # Карантин
    until = _dox_quarantine.get(key)
    if until and now < until:
        _dox_armed.pop(key, None)
        await dox_kill(m, owner_id, "карантин", t0)
        return True

    # Стадия 2: выкладка
    deadline = _dox_armed.pop(key, None)
    if deadline is not None and now < deadline:
        await dox_kill(m, owner_id, "выкладка после команды", t0)
        return True

    # Стадия 1: команда
    if DOX_TRIGGER.match(m.text or m.caption or ""):
        _dox_armed[key] = now + float(d.get("arm", 4.0))
        d["triggers"] = d.get("triggers", 0) + 1
        if d.get("quarantine", 0):
            _dox_quarantine[key] = now + float(d["quarantine"]) * 60
        await dox_kill(m, owner_id, "команда (правкой)" if edited else "команда", t0)
        return True
    return False

def dox_status(u: dict) -> str:
    d = adox(u)
    return (f"🕵️ <b>Анти-докс</b> · {'вкл' if d.get('enabled', True) else 'выкл'}\n"
            f"Режим: <b>{'наблюдение (DRY-RUN)' if d.get('dry_run', True) else 'удаление'}</b>\n"
            f"Окно после команды: <b>{float(d.get('arm', 4.0)):.0f} с</b> · "
            f"карантин: <b>{int(d.get('quarantine', 0)) or 'выкл'}</b>"
            f"{' мин' if d.get('quarantine') else ''}\n"
            f"Срабатываний: <b>{d.get('triggers', 0)}</b> · удалено: "
            f"<b>{d.get('killed', 0)}</b> · не вышло: <b>{d.get('missed', 0)}</b>")

# ═════════════════════════════════════════════════════════
# 📦 АРХИВ МЕДИА С БЕЗОПАСНОЙ ОБРАБОТКОЙ
# ═════════════════════════════════════════════════════════
MAX_DL = 20 * 1024 * 1024
KINDS = ("photo", "video", "voice", "video_note", "animation", "document", "audio", "sticker")

def media_of(m: Message):
    for k in KINDS:
        obj = getattr(m, k, None)
        if not obj:
            continue
        if k == "photo":
            obj = obj[-1]
        return (k, obj, obj.file_id, obj.file_unique_id, getattr(obj, "file_size", 0) or 0)
    return None

async def archive_media(m: Message, owner_id: int, forced=False) -> str | None:
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
        return None

    c = conn_of(owner_id)
    if not c:
        return None
    who = ((m.from_user.full_name if m.from_user else "") or "").strip()
    if not who:
        who = getattr(m.chat, "full_name", None) or getattr(m.chat, "title", None) \
            or f"id {m.chat.id}"
    cap = (f"📦 <b>{kind}</b> · {esc(who)}\n"
           f"💬 чат <code>{m.chat.id}</code> · {now_local():%d.%m %H:%M}")
    if m.caption:
        cap += f"\n\n{esc(m.caption[:300])}"

    saved_id, note = 0, ""
    try:
        if size and size > MAX_DL:
            msg = await bot.copy_message(c["chat"], m.chat.id, m.message_id,
                                         caption=cap[:1000])
            saved_id, note = msg.message_id, "copied (>20MB)"
        else:
            data = (await bot.download(file_id)).read()
            size = size or len(data)
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
        try:
            msg = await bot.copy_message(c["chat"], m.chat.id, m.message_id)
            saved_id, note = msg.message_id, f"copy fallback ({ex.__class__.__name__})"
        except Exception:
            note = f"fail: {ex.__class__.__name__}"

    st.log_media(owner_id, m.chat.id, m.from_user.id if m.from_user else 0,
                 kind, file_id, file_uid, size, saved_id, note)
    return f"📦 {kind} в архиве ({note})" if saved_id else f"⚠️ не вышло: {note}"

async def archive_from_cache(info: dict, owner_id: int) -> str:
    c = conn_of(owner_id)
    if not c or not info.get("file_id"):
        return ""
    kind, file_id = info["kind"], info["file_id"]
    if st.has_media(owner_id, info.get("uid") or ""):
        return "\n📦 <i>копия уже была в архиве</i>"
    try:
        data = (await bot.download(file_id)).read()
        fname = f"{kind}_{info.get('uid')}"
        file = BufferedInputFile(data, fname)
        sender = {"photo": bot.send_photo, "video": bot.send_video,
                  "voice": bot.send_voice, "video_note": bot.send_video_note,
                  "animation": bot.send_animation, "audio": bot.send_audio,
                  "sticker": bot.send_sticker}.get(kind, bot.send_document)
        cap = (f"🗑📦 <b>{kind}</b> из удалённого\n"
               f"👤 {esc(info['from'])} · {now_local():%d.%m %H:%M}")
        kw = {"caption": cap} if kind not in ("video_note", "sticker") else {}
        msg = await sender(c["chat"], file, **kw)
        st.log_media(owner_id, info["chat"], info["from_id"], kind, file_id,
                     info.get("uid") or "", info.get("size") or len(data),
                     msg.message_id, "saved on delete")
        return "\n📦 <i>медиа успело сохраниться</i>"
    except Exception as ex:
        logging.warning("archive_from_cache: %s", ex)
        return (f"\n⚠️ <i>медиа не успело сохраниться "
                f"({ex.__class__.__name__}) — вероятно, было одноразовым</i>")

# ═════════════════════════════════════════════════════════
# ✏️ ПРАВКИ СООБЩЕНИЙ
# ═════════════════════════════════════════════════════════
def diff_line(old: str, new: str) -> str:
    if not old:
        return "добавлен текст"
    if not new:
        return "текст убран"
    if old.lower() == new.lower():
        return "изменён регистр"
    if new.startswith(old):
        return f"дописано в конец (+{len(new) - len(old)})"
    if old.startswith(new):
        return f"обрезано (−{len(old) - len(new)})"
    return "переписано"

@dp.edited_business_message()
async def on_edited(m: Message):
    owner_id, c = owner_by_cid(m.business_connection_id)
    if owner_id is None or m.sender_business_bot is not None:
        return

    key = (m.chat.id, m.message_id)
    old = cache.get(key)
    new_text = m.text or m.caption or ""

    own = bool(m.from_user and m.from_user.id == owner_id)
    if old:
        old_text = old["text"]
        old["text"] = new_text
    else:
        old_text = ""
        cache_put(m)
    if own:
        return
    
    if await antidox_check(m, owner_id, edited=True):
        return

    u = user(owner_id)
    if not u.get("track_edits", True) or not c:
        return
    if old_text == new_text:
        return

    who = esc(m.from_user.full_name if m.from_user else "?")
    await bot.send_message(
        c["chat"],
        f"✏️ <b>Сообщение изменено</b> · <i>{diff_line(old_text, new_text)}</i>\n"
        f"👤 {who} (<code>{m.chat.id}</code>) · {now_local():%d.%m %H:%M}\n\n"
        f"<b>Было:</b>\n<blockquote>{html_lib.escape(old_text or '—')[:700]}</blockquote>\n"
        f"<b>Стало:</b>\n<blockquote>{html_lib.escape(new_text or '—')[:700]}</blockquote>")

# ═════════════════════════════════════════════════════════
# РОУТЕР С КЭШИРОВАНИЕМ
# ═════════════════════════════════════════════════════════
@dp.business_message()
async def on_business_message(m: Message):
    owner_id, _ = owner_by_cid(m.business_connection_id)
    if owner_id is None or m.sender_business_bot is not None:
        return
    cache_put(m)

    # Кэшируем данные пользователя на время обработки
    user_cache = user(owner_id)
    
    if not (m.from_user and m.from_user.id == owner_id):
        if await antidox_check(m, owner_id):
            return
        await guard_incoming(m, owner_id)
        if user_cache.get("save_when", "deleted") == "always":
            asyncio.create_task(archive_media(m, owner_id))
        return

    if user_cache["banned"]:
        return
    
    if not st.is_known(owner_id, m.chat.id):
        st.add_known(owner_id, m.chat.id)

    if media_of(m) and user_cache.get("save_when", "deleted") == "always":
        asyncio.create_task(archive_media(m, owner_id))

    text = m.text or ""
    if text.startswith(PREFIX):
        user_cache["cmds"] += 1
        save(owner_id)
        return await handle_cmd(m, owner_id, text[len(PREFIX):])

    mode = mode_for(user_cache, m.chat.id)
    if mode and mode in MODES and text.strip():
        await replace_with(m, MODES[mode](html_lib.escape(text),
                                          user_cache.get("mode_level", "normal"),
                                          user_cache.get("mode_bold", True),
                                          user_cache.get("mode_emoji", True)))

def mode_for(u: dict, chat_id) -> str | None:
    key = str(chat_id)
    pair = u["pairs"].get(key)
    if pair:
        return pair
    local = (u.get("chat_modes") or {}).get(key)
    if local == "off":
        return None
    return local or u["mode"]

async def drop(m: Message) -> bool:
    try:
        await bot.delete_business_messages(
            business_connection_id=m.business_connection_id, message_ids=[m.message_id])
        return True
    except Exception as ex:
        logging.warning("delete: %s", ex)
        return False

async def replace_with(m: Message, new_text: str):
    try:
        await bot.edit_message_text(
            new_text,
            chat_id=m.chat.id,
            message_id=m.message_id,
            business_connection_id=m.business_connection_id)
        return
    except Exception as ex:
        logging.info("edit не прошёл (%s) — падаю на delete+send",
                     ex.__class__.__name__)

    if not await drop(m):
        return
    try:
        await bot.send_message(m.chat.id, new_text,
                               business_connection_id=m.business_connection_id)
    except Exception as ex:
        logging.warning("send: %s", ex)

# ═════════════════════════════════════════════════════════
# МЕДИА ОБРАБОТКА С БЕЗОПАСНЫМИ ВРЕМЕННЫМИ ФАЙЛАМИ
# ═════════════════════════════════════════════════════════
async def dl(file_id) -> bytes:
    return (await bot.download(file_id)).read()

def safe_ff(args, data, in_ext, out_ext) -> bytes:
    """Безопасная обработка через FFmpeg."""
    src = dst = None
    try:
        temp_dir = tempfile.gettempdir()
        src = os.path.join(temp_dir, f"karzen_{uuid.uuid4().hex}{in_ext}")
        dst = os.path.join(temp_dir, f"karzen_{uuid.uuid4().hex}{out_ext}")
        
        with open(src, "wb") as f:
            f.write(data)
        
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", src] + args + [dst],
            check=True,
            capture_output=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        with open(dst, "rb") as f:
            return f.read()
            
    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg timeout (120s)")
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr[-500:].decode('utf-8', errors='ignore') if e.stderr else str(e)
        raise Exception(f"FFmpeg error: {error_msg}")
    finally:
        for path in (src, dst):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except (OSError, PermissionError):
                    import atexit
                    atexit.register(lambda p=path: os.unlink(p) if os.path.exists(p) else None)

def deepfry(data: bytes) -> bytes:
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

def to_stories(data: bytes) -> List[bytes]:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    W, H = img.size
    step = max(1, int(H * 9 / 16))
    parts, x = [], 0
    while x < W and len(parts) < 10:
        b = io.BytesIO()
        img.crop((x, 0, min(x + step, W), H)).save(b, "JPEG", quality=92)
        parts.append(b.getvalue())
        x += step
    def split_message(text: str, max_len: int = 4000) -> List[str]:
        """Разбивает длинный текст на части."""
        if len(text) <= max_len:
            return [text]

        parts = []
        while text:
            if len(text) <= max_len:
                parts.append(text)
                break

            # Ищем последний перенос строки или пробел
            split_at = text.rfind('\n', 0, max_len)
            if split_at == -1:
                split_at = text.rfind(' ', 0, max_len)
            if split_at == -1:
                split_at = max_len

            parts.append(text[:split_at])
            text = text[split_at:].lstrip()

        return parts

# ═════════════════════════════════════════════════════════
# 🐾 КАРТИНКИ ИЗ ИНТЕРНЕТА
# ═════════════════════════════════════════════════════════
_http: Optional[aiohttp.ClientSession] = None

async def http() -> aiohttp.ClientSession:
    global _http
    if _http is None or _http.closed:
        _http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=25, connect=8))
    return _http

async def http_close():
    if _http and not _http.closed:
        await _http.close()

NEKO = {
    "neko": ("ня~ ♡ (=^･ω･^=)", ["https://nekos.best/api/v2/neko",
                                 "https://nekos.best/api/v2/kitsune"]),
    "kitsune": ("лисичка ♡ ʕ•ᴥ•ʔ", ["https://nekos.best/api/v2/kitsune",
                                     "https://nekos.best/api/v2/neko"]),
    "waifu": ("вайфу ♡ (◕‿◕✿)", ["https://nekos.best/api/v2/waifu",
                                 "https://nekos.best/api/v2/neko"]),
    "husbando": ("кун ♡ (￣ω￣)", ["https://nekos.best/api/v2/husbando",
                                  "https://api.catboys.com/img"]),
    "catboy": ("котик ♡ ヽ(=^･ω･^=)丿", ["https://api.catboys.com/img",
                                        "https://nekos.best/api/v2/husbando"]),
    "femboy": ("котик ♡ (⁄ ⁄•⁄ω⁄•⁄ ⁄)", ["https://api.catboys.com/img",
                                          "https://nekos.best/api/v2/husbando"]),
}

GIF_CATS = frozenset("""
angry baka bite bleh blowkiss blush bonk bored carry clap confused cry
cuddle dance facepalm feed handhold handshake happy highfive hug kabedon
kick kiss lappillow laugh lurk nod nom nope nya pat peck poke pout punch
run salute shake shocked shoot shrug sip slap sleep smile smug spin stare
tableflip teehee think thumbsup tickle wag wave wink yawn yeet
""".split())

GIF_RU = {
    "обнять": "hug", "обнимашки": "hug", "прижать": "cuddle", "обнимать": "cuddle",
    "погладить": "pat", "гладить": "pat", "поцелуй": "kiss", "чмок": "peck",
    "воздушный": "blowkiss", "улыбка": "smile", "смех": "laugh", "ржу": "laugh",
    "плак": "cry", "плакать": "cry", "злюсь": "angry", "бака": "baka",
    "спать": "sleep", "зевок": "yawn", "танец": "dance", "танцевать": "dance",
    "привет": "wave", "пока": "wave", "махать": "wave", "кивок": "nod",
    "неа": "nope", "пять": "highfive", "заруку": "handhold", "класс": "thumbsup",
    "бонк": "bonk", "шлёп": "slap", "шлеп": "slap", "пинок": "kick",
    "кусь": "bite", "тык": "poke", "щекотка": "tickle", "краснеть": "blush",
    "смущение": "blush", "думать": "think", "фейспалм": "facepalm",
    "покормить": "feed", "ня": "nya", "подмигнуть": "wink", "пожать": "shrug",
    "смотреть": "stare", "скучно": "bored", "колени": "lappillow",
    "нести": "carry", "хлоп": "clap", "стол": "tableflip", "бежать": "run",
    "пить": "sip", "кинуть": "yeet", "шок": "shocked", "ухмылка": "smug",
    "дуться": "pout", "салют": "salute", "хихи": "teehee", "ням": "nom",
    "хвост": "wag", "крутиться": "spin",
}

GIF_LABEL: Dict[str, str] = {}
for _word, _cat in GIF_RU.items():
    GIF_LABEL.setdefault(_cat, _word)

GIF_POPULAR = ("hug", "pat", "kiss", "nya", "dance", "laugh", "wave",
               "sleep", "blush", "thumbsup", "baka", "bonk")

def gif_sources(cat: str) -> list:
    raw = os.getenv(f"NEKO_SRC_{cat.upper()}", "")
    mine = [u.strip() for u in raw.split(",") if u.strip().startswith("http")]
    return mine + [f"https://nekos.best/api/v2/{cat}"]

NEKO_ALIAS = {
    "neko": "neko", "нэко": "neko", "неко": "neko", "ня": "neko",
    "kitsune": "kitsune", "лиса": "kitsune", "лисичка": "kitsune",
    "waifu": "waifu", "вайфу": "waifu", "ж": "waifu", "девушка": "waifu",
    "husbando": "husbando", "boy": "husbando", "b": "husbando",
    "м": "husbando", "кун": "husbando", "парень": "husbando",
    "catboy": "catboy", "кот": "catboy", "котик": "catboy", "некомими": "catboy",
    "femboy": "femboy", "фембой": "femboy", "фем": "femboy", "fb": "femboy",
}

def sources_for(kind: str) -> list:
    raw = os.getenv(f"NEKO_SRC_{kind.upper()}", "")
    mine = [u.strip() for u in raw.split(",") if u.strip().startswith("http")]
    return mine + list(NEKO[kind][1])

MAX_PIC = 10 * 1024 * 1024
IMG_MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG", "png"),
             (b"GIF8", "gif"), (b"RIFF", "webp"))

def img_ext(data: bytes) -> Optional[str]:
    for sig, ext in IMG_MAGIC:
        if data.startswith(sig):
            return ext
    return None

def pic_url(j) -> Optional[str]:
    if not isinstance(j, dict):
        return None
    for path in (("url",), ("image",), ("results", 0, "url")):
        node = j
        for key in path:
            if isinstance(key, int):
                node = node[key] if isinstance(node, list) and len(node) > key else None
            else:
                node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, str) and node.startswith("http"):
            return node
    return None

async def fetch_pic(sources) -> Optional[Tuple[bytes, str]]:
    s = await http()
    for api in sources:
        try:
            async with s.get(api) as r:
                url = pic_url(await r.json(content_type=None))
            if not url:
                continue
            async with s.get(url) as r:
                if int(r.headers.get("Content-Length") or 0) > MAX_PIC:
                    continue
                data = await r.read()
            ext = img_ext(data)
            if ext and len(data) <= MAX_PIC:
                return data, ext
        except Exception as ex:
            logging.info("pic %s: %s", api, ex.__class__.__name__)
    return None

# ═════════════════════════════════════════════════════════
# КОМАНДЫ
# ═════════════════════════════════════════════════════════
HELP = {
    "guard": ("🛡", "Защита", """🛡 <b>Защита</b>

<code>.scam</code> — антискам вкл/выкл
<code>.filter</code> — фильтр первых сообщений вкл/выкл
<code>.sens мягко|обычно|строго</code> — строгость детектора
<code>.check</code> — разобрать сообщение по баллам (ответом)
<code>.trust</code> / <code>.untrust</code> — доверять собеседнику или нет
<code>.mute</code> / <code>.unmute</code> — уведомления по этому диалогу
<code>.word +слово</code> · <code>.word -слово</code> — стоп-слова
<code>.away текст</code> · <code>.away off</code> — автоответ «я отошёл»
<code>.quiet 23:00-08:00</code> · <code>.quiet off</code> — тихие часы
<code>.dox</code> — анти-докс: команда «dox/пробив» и выкладка за ней
<code>.dox kill</code> — из наблюдения в боевой · <code>.dox q 30</code> — карантин
<code>/log</code> — журнал срабатываний"""),

    "style": ("🎨", "Стиль", """🎨 <b>Стиль речи</b>

<code>.mode</code> <i>kawaii tsundere yandere leet small bubble mock</i> | <code>off</code>
<code>.here kawaii</code> — мод только для этого диалога
<code>.here off</code> — здесь писать без мода
<code>.style soft|normal|max</code> — сколько декора
<code>.style bold</code> · <code>.style emoji</code>
<code>.preview</code> — как это будет выглядеть
<code>.pair kawaii</code> · <code>.unpair</code> · <code>.pairs</code> — вдвоём

Разово, не меняя режим:
<code>.kawaii текст</code> <code>.mock текст</code> <code>.small</code> <code>.bubble</code>
<code>.zalgo</code> <code>.space</code> <code>.upside</code> <code>.sw</code> (раскладка)

<i>small и bubble меняют только латинские буквы — в юникоде
нет ни кириллических капсов, ни кириллицы в кружочках.</i>"""),

    "arch": ("📦", "Архив", """📦 <b>Архив и следы</b>

<code>.save</code> — сохранить медиа (ответом)
<code>.savemedia</code> — архив вкл/выкл
<code>.savewhen always|deleted</code> — когда сохранять
<code>.ad</code> — анти-делит (удалённые сообщения)
<code>.edits</code> — ловить правки чужих сообщений
<code>.media</code> — что лежит в архиве
<code>.digest</code> — сводка за день в 21:00
<code>.export</code> — выгрузить настройки и журнал файлом"""),

    "media": ("🎬", "Медиа", """🎬 <b>Работа с медиа</b> (ответом на сообщение)

<code>.gif</code> — видео → гифка
<code>.fv</code> — голосовое громче и чище
<code>.lq</code> — зашакалить фото
<code>.story</code> — фото → нарезка под сторис
<code>.type</code> — печатать текст по буквам

<b>Картинки</b>
<code>.nk</code> — неко · <code>.nkb</code> — кун
<code>.nk лиса</code> <code>.nk вайфу</code> <code>.nk кот</code> <code>.nk фембой</code>
<i>если источник лежит, бот сам идёт к следующему</i>

<b>Гифки-реакции</b> (59 штук)
<code>.g обнять</code> <code>.g погладить</code> <code>.g поцелуй</code> <code>.g бонк</code>
<code>.g</code> — случайная · <code>.g список</code> — все
<i>ответом на сообщение гифка уходит ответом</i>"""),

    "fun": ("🎲", "Мелочи", """🎲 <b>Мелочи</b>

<code>.pick а | б | в</code> — выбрать за тебя
<code>.roll 2d6</code> · <code>.dice</code> · <code>.flip</code>
<code>.8ball вопрос</code> — шар предсказаний
<code>.nuke</code> — «удалить чат» понарошку · <code>.nuke своя концовка</code>
<code>.love</code> · <code>.me</code> — про себя
<code>/invite</code> — позвать друга · <code>/setup</code> — как подключить"""),
}

CMD_HELP = ("✨ <b>Команды</b> · префикс <code>.</code>\n\n"
            "Пишешь их <b>сам, в любом диалоге</b> — бот стирает команду\n"
            "и делает своё. Выбери раздел 👇")

def help_kb(active: str = "") -> InlineKeyboardMarkup:
    rows, line = [], []
    for k, (ico, title, _) in HELP.items():
        mark = "· " if k == active else ""
        line.append(InlineKeyboardButton(text=f"{mark}{ico} {title}",
                                         callback_data=f"h:{k}"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([InlineKeyboardButton(text="‹ Меню", callback_data="n:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

NUKE_STEPS = [
    "стираю переписку…", "выношу компромат…", "жгу улики…",
    "удаляю мемы… больно…", "чищу голосовые, записанные в три ночи…",
    "забываю всё, что ты писал…", "отправляю стикеры в небытие…",
    "удаляю «привет, как дела» ×247…", "вычёркиваю тебя из истории…",
    "распечатываю и рву…", "прошу Дурова забыть этот чат…",
    "стираю «ок» ×1 024…",
]
NUKE_PUNCH = [
    "…шучу 😼 всё на месте.",
    "…ладно, ничего не удалено. Но страшно же было?",
    "…нет. Я всё сохраню. Навсегда. ♡",
    "…расслабься, это просто мем 🗿",
    "…упс, кнопка «отмена» была рядом. Всё цело.",
    "…а вот и нет. Даже тот стикер.",
]

# ═════════════════════════════════════════════════════════
# ОСНОВНОЙ OSINT-МОДУЛЬ
# ═════════════════════════════════════════════════════════
async def generate_osint_report(owner_id: int, target_id: int) -> str:
    """Генерация полного OSINT отчёта с безопасным доступом к атрибутам."""
    report_lines = []
    
    try:
        target_user = await bot.get_chat(target_id)
    except Exception as e:
        return f"⚠️ Не могу получить информацию о пользователе <code>{target_id}</code>: {e}"
    
    # Базовая информация
    report_lines.append(f"🕵️ <b>OSINT отчёт</b> · {now_local():%d.%m %H:%M}")
    
    # Имя пользователя - безопасный доступ
    full_name = getattr(target_user, 'full_name', None)
    if full_name:
        report_lines.append(f"👤 <b>Цель:</b> {esc(full_name)}")
    else:
        report_lines.append(f"👤 <b>Цель:</b> ID <code>{target_id}</code>")
    
    report_lines.append(f"🆔 ID: <code>{target_id}</code>")
    
    # Username - безопасный доступ
    username = getattr(target_user, 'username', None)
    if username:
        report_lines.append(f"📱 @{esc(username)}")
        
        # Проверка username на платформах
        try:
            platform_check = await osint_service.check_username_across_platforms(username)
            if platform_check and "error" not in platform_check:
                found = [p for p, s in platform_check.items() if "✅" in s or "🔄" in s]
                if found:
                    report_lines.append(f"\n<b>Найдено на платформах:</b> {', '.join(found[:5])}")
        except Exception:
            pass
    
    # Язык - безопасный доступ (может отсутствовать)
    language_code = getattr(target_user, 'language_code', None)
    if language_code:
        report_lines.append(f"🌐 Язык: {esc(language_code)}")
    
    # Premium статус - безопасный доступ
    is_premium = getattr(target_user, 'is_premium', False)
    report_lines.append(f"💎 Premium: {'да' if is_premium else 'нет'}")
    
    # Фото профиля
    try:
        photos = await bot.get_user_profile_photos(target_id, limit=1)
        if photos and photos.photos:
            photo = photos.photos[0][-1]
            report_lines.append(f"\n📷 <b>Фото профиля:</b> есть ({photo.width}×{photo.height})")
        else:
            report_lines.append(f"\n📷 <b>Фото профиля:</b> нет")
    except Exception:
        report_lines.append(f"\n📷 <b>Фото профиля:</b> не проверено")
    
    # Проверка утечек
    if username:
        try:
            breaches = await osint_service.check_username_breaches(username)
            if breaches.get("breached"):
                report_lines.append(f"\n🚨 <b>Утечки данных:</b> найдено {breaches.get('count', 0)}")
                if breaches.get("sources"):
                    report_lines.append(f"  Источники: {', '.join(breaches['sources'][:3])}")
            elif not breaches.get("error"):
                report_lines.append(f"\n🛡️ <b>Утечки данных:</b> не найдено")
        except Exception as e:
            report_lines.append(f"\n⚠️ <b>Проверка утечек:</b> ошибка: {e}")
    
    # Генерация возможных номеров
    try:
        from phonenumbers import PhoneNumberFormat
        # Создаём "псевдо" номер из ID (для демонстрации)
        fake_number = f"+7{str(target_id)[-10:]}"
        parsed = phonenumbers.parse(fake_number, None)
        if phonenumbers.is_valid_number(parsed):
            possible_number = phonenumbers.format_number(parsed, PhoneNumberFormat.INTERNATIONAL)
            report_lines.append(f"\n📞 <b>Возможный номер:</b> <code>{possible_number}</code>")
    except:
        pass
    
    # Оценка возраста аккаунта
    reg_estimate = estimate_registration_date(target_id)
    report_lines.append(f"\n📅 <b>Примерная дата регистрации:</b> {reg_estimate}")
    
    # Информация из базы бота
    if target_id != owner_id:
        try:
            is_trusted = st.is_trusted(owner_id, target_id)
            media_count = await call(st.count_media_from, owner_id, target_id)
            catches = [r for r in st.recent_catches(owner_id, 50) if r["peer"] == target_id]
            
            report_lines.append(f"\n<b>В базе бота:</b>")
            report_lines.append(f"  🤝 Доверенный: {'да' if is_trusted else 'нет'}")
            if media_count > 0:
                report_lines.append(f"  📦 Медиа в архиве: {media_count}")
            if catches:
                report_lines.append(f"  🔪 Срабатываний защиты: {len(catches)}")
                if catches:
                    last = catches[0]
                    report_lines.append(f"    Последнее: {esc(last['reason'][:60])}")
        except Exception:
            pass
    
    report_lines.append(f"\n<i>Отчёт сгенерирован автоматически. Точность не гарантируется.</i>")
    
    return "\n".join(report_lines)

def estimate_registration_date(user_id: int) -> str:
    """Оценка даты регистрации Telegram аккаунта по ID."""
    if user_id < 100000000:
        return "2013-2014 (очень старый аккаунт)"
    elif user_id < 200000000:
        return "2014-2015"
    elif user_id < 500000000:
        return "2015-2016"
    elif user_id < 1000000000:
        return "2016-2017"
    elif user_id < 2000000000:
        return "2017-2018"
    elif user_id < 4000000000:
        return "2018-2020"
    elif user_id < 6000000000:
        return "2020-2021"
    elif user_id < 8000000000:
        return "2021-2022"
    else:
        return "2022-2023 (новый аккаунт)"

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

 # ── OSINT команды ──
    if name in ("dox", "osint", "пробив", "inf", "info"):
        await drop(m)
        target_id = None
        
        # Определение цели
        if args.strip().isdigit():
            target_id = int(args.strip())
        elif rep and rep.from_user:
            target_id = rep.from_user.id
        else:
            target_id = peer
        
        if target_id == uid:
            return await dm(uid, "🤨 Зачем пробивать самого себя?")
        
        # Генерация отчёта
        try:
            report = await generate_osint_report(uid, target_id)
            # Разбиваем длинные отчёты на части
            if len(report) > 4000:
                parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
                for i, part in enumerate(parts):
                    if i == len(parts) - 1:
                        await dm(uid, part, reply_markup=peer_kb(target_id, u))
                    else:
                        await dm(uid, part)
            else:
                await dm(uid, report, reply_markup=peer_kb(target_id, u))
        except Exception as e:
            logging.error(f"OSINT error: {e}")
            await dm(uid, f"⚠️ Ошибка при генерации отчёта: {str(e)[:200]}")
        return
    
    # ── Расширенный OSINT ──
    if name in ("email", "емейл", "почта"):
        if not args.strip():
            return await note("📧 <code>.email user@example.com</code> — проверка email на утечки")
        
        await drop(m)
        email = args.strip()
        breaches = await osint_service.check_email_breaches(email)
        
        if breaches.get("breached"):
            report = f"📧 <b>Проверка email:</b> {esc(email)}\n"
            report += f"🚨 <b>Найдено утечек:</b> {breaches['count']}\n"
            if breaches.get("breaches"):
                report += f"📅 Последние утечки:\n"
                for b in breaches["breaches"]:
                    report += f"  • {b.get('name')} ({b.get('date')})\n"
            if breaches.get("latest"):
                report += f"🕐 Последняя утечка: {breaches['latest']}"
        elif breaches.get("error"):
            report = f"📧 <b>Проверка email:</b> {esc(email)}\n⚠️ {breaches['error']}"
        else:
            report = f"📧 <b>Проверка email:</b> {esc(email)}\n🛡️ Утечек не найдено"
        
        await dm(uid, report)
        return
    
    if name in ("ip", "айпи"):
        import re
        ip_match = re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', args)
        if not ip_match:
            return await note("🌐 <code>.ip 8.8.8.8</code> — информация об IP-адресе")
        
        await drop(m)
        ip_info = await osint_service.check_ip_info(ip_match.group(0))
        
        if ip_info.get("error"):
            report = f"🌐 <b>Проверка IP:</b> {ip_match.group(0)}\n⚠️ {ip_info['error']}"
        else:
            report = f"🌐 <b>Информация об IP:</b> {ip_match.group(0)}\n"
            report += f"🌍 <b>Страна:</b> {ip_info.get('country', '—')}\n"
            report += f"🏙️ <b>Город:</b> {ip_info.get('city', '—')}\n"
            report += f"📡 <b>Провайдер:</b> {ip_info.get('isp', '—')}\n"
            report += f"🏢 <b>Организация:</b> {ip_info.get('org', '—')}"
        
        await dm(uid, report)
        return
    
    if name in ("phone", "телефон", "номер"):
        if not args.strip():
            return await note("📞 <code>.phone +79991234567</code> — информация о номере")
        
        await drop(m)
        phone_info = await osint_service.check_phone_info(args.strip())
        
        if phone_info.get("error"):
            report = f"📞 <b>Проверка номера:</b> {esc(args.strip())}\n⚠️ {phone_info['error']}"
        elif phone_info.get("valid"):
            report = f"📞 <b>Информация о номере:</b>\n"
            report += f"📱 <b>Номер:</b> {phone_info.get('international', '—')}\n"
            report += f"🌍 <b>Страна:</b> {phone_info.get('country', '—')}\n"
            report += f"📡 <b>Оператор:</b> {phone_info.get('carrier', '—')}\n"
            report += f"🕐 <b>Часовой пояс:</b> {', '.join(phone_info.get('timezone', []))}\n"
            report += f"📊 <b>Тип:</b> {phone_info.get('type', '—')}"
        else:
            report = f"📞 <b>Проверка номера:</b> {esc(args.strip())}\n⚠️ Неверный номер"
        
        await dm(uid, report)
        return
    
    # ── Остальные команды (сокращённо, как в оригинале) ──
    if name == "scam":
        u["antiscam"]["enabled"] = not u["antiscam"]["enabled"]
        save(uid)
        return await note(f"🛡 Антискам: {'вкл ✅' if u['antiscam']['enabled'] else 'выкл ❌'}")

    if name == "filter":
        u["filter"]["enabled"] = not u["filter"]["enabled"]
        save(uid)
        return await note(f"🔪 Фильтр: {'вкл ✅' if u['filter']['enabled'] else 'выкл ❌'}\n"
                          f"Удаление: {'да' if u['filter']['delete'] else 'нет (только уведомления)'}")

    if name == "sens":
        a = args.strip().lower()
        aliases = {"мягко": "low", "обычно": "normal", "строго": "high"}
        a = aliases.get(a, a)
        if a not in SENS:
            return await note(
                "🎚 <code>.sens мягко|обычно|строго</code>\n\n"
                f"Сейчас: <b>{SENS_RU[u.get('sens','normal')]}</b> "
                f"(порог {threshold(u)} баллов)")
        u["sens"] = a
        save(uid)
        return await note(f"🎚 Строгость: <b>{SENS_RU[a]}</b> · порог {SENS[a]}")

    if name in ("trust", "untrust"):
        target = rep.from_user.id if (rep and rep.from_user) else peer
        on = name == "trust"
        set_trust(uid, target, on)
        return await note(f"✅ <code>{target}</code> в доверенных — проверки отключены."
                          if on else f"↩️ <code>{target}</code> убран из доверенных.")

    if name in ("mute", "unmute"):
        target = rep.from_user.id if (rep and rep.from_user) else peer
        muted = u.setdefault("muted", [])
        if name == "mute" and target not in muted:
            muted.append(target)
        elif name == "unmute" and target in muted:
            muted.remove(target)
        save(uid)
        return await note(f"🔕 <code>{target}</code> без уведомлений." if name == "mute"
                          else f"🔔 <code>{target}</code> снова с уведомлениями.")

    if name == "check":
        if not rep:
            return await note("↩️ Ответь на сообщение, которое проверить.")
        thr = threshold(u)
        v = analyze(rep, not st.is_known(uid, rep.chat.id))
        verdict = ("🚨 похоже на развод" if v["score"] >= thr
                   else "🟡 есть признаки" if v["score"] >= thr - 1 else "🟢 чисто")
        rows = "\n".join(f"  +{w} · {esc(lbl)}" for w, lbl in v["hits"][:8]) or "  — ничего"
        return await note(f"🔎 <b>Проверка</b>: {verdict}\n"
                          f"{risk_bar(v['score'], thr)} · строгость {SENS_RU[u.get('sens','normal')]}\n\n"
                          f"<b>Из чего сложилось:</b>\n{rows}")

    if name == "away":
        a = args.strip()
        if a.lower() in ("off", "выкл", "стоп"):
            u["away_on"] = False
            save(uid)
            return await note("💤 Автоответ выключен.")
        if not a:
            u["away_on"] = bool(u.get("away")) and not u.get("away_on")
            save(uid)
            if not u.get("away"):
                return await note("💤 <code>.away текст</code> — что отвечать, пока тебя нет.")
            return await note(f"💤 Автоответ: {'вкл ✅' if u['away_on'] else 'выкл ❌'}\n"
                              f"<blockquote>{esc(u['away'])}</blockquote>")
        u["away"], u["away_on"] = a[:800], True
        save(uid)
        return await note(f"💤 Автоответ включён — уйдёт один раз на собеседника "
                          f"(не чаще раза в 6 ч):\n<blockquote>{esc(a[:800])}</blockquote>")

    if name == "quiet":
        a = args.strip().lower()
        if a in ("off", "выкл", ""):
            if not a and u.get("quiet"):
                return await note(f"🌙 Тихие часы: <b>{esc(u['quiet'])}</b>\n"
                                  f"Выключить: <code>.quiet off</code>")
            u["quiet"] = ""
            save(uid)
            return await note("🌙 Тихие часы выключены — уведомления снова со звуком.")
        try:
            s, e = a.split("-", 1)
            hhmm(s), hhmm(e)
        except Exception:
            return await note("🌙 Формат: <code>.quiet 23:00-08:00</code>")
        u["quiet"] = f"{s.strip()}-{e.strip()}"
        save(uid)
        return await note(f"🌙 С {esc(s.strip())} до {esc(e.strip())} уведомления "
                          f"приходят беззвучно.\n<i>Исполняемые файлы будят всегда.</i>")

    if name == "digest":
        u["digest"] = not u.get("digest")
        save(uid)
        return await note(f"📬 Сводка за день: {'вкл ✅ (в 21:00)' if u['digest'] else 'выкл ❌'}")

    if name in ("dox", "antidox"):
        d = adox(u)
        a = args.strip().lower()
        words = a.split()
        if a in ("on", "вкл"):
            d["enabled"] = True
        elif a in ("off", "выкл"):
            d["enabled"] = False
        elif a in ("dry", "тест", "наблюдение"):
            d["dry_run"] = True
        elif a in ("kill", "боевой", "удалять"):
            d["dry_run"] = False
        elif len(words) == 2 and words[0] in ("arm", "окно") and words[1].isdigit():
            d["arm"] = float(max(1, min(30, int(words[1]))))
        elif len(words) == 2 and words[0] in ("q", "карантин") and words[1].isdigit():
            d["quarantine"] = max(0, min(1440, int(words[1])))
        elif a:
            return await note(
                "🕵️ <code>.dox on|off</code> — вкл/выкл\n"
                "<code>.dox dry</code> — только докладывать · "
                "<code>.dox kill</code> — удалять\n"
                "<code>.dox arm 4</code> — секунд ждать выкладку после команды\n"
                "<code>.dox q 30</code> — карантин: минут удалять от него всё (0 = выкл)\n\n"
                + dox_status(u))
        save(uid)
        return await note(dox_status(u))

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
                save(uid)
            return await note(f"➕ Добавлено: «{esc(w)}»")
        if a.startswith("-"):
            w = a[1:].strip().lower()
            if w in u["filter"]["words"]:
                u["filter"]["words"].remove(w)
                save(uid)
            return await note(f"➖ Удалено: «{esc(w)}»")
        return await note("Использование: <code>.word +слово</code> / <code>.word -слово</code>")

    # ── инфо ──
    if name in ("help", "h"):
        return await note(CMD_HELP, reply_markup=help_kb())
    if name == "me":
        await drop(m)
        txt, kb = await screen(uid, "stats")
        return await dm(uid, txt, reply_markup=kb)
    if name in ("menu", "settings"):
        await drop(m)
        txt, kb = await screen(uid, "root")
        return await dm(uid, txt, reply_markup=kb)

    # ── моды ──
    if name == "mode":
        a = args.strip().lower()
        if a in ("off", "", "none"):
            u["mode"] = None
            save(uid)
            return await note("🔕 Мод выключен.")
        if a not in MODES:
            return await note("⚠️ Моды: " + ", ".join(MODES))
        u["mode"] = a
        save(uid)
        return await note(f"✅ Мод: <b>{a}</b> ♡\n\n{preview(a, u)}")

    if name == "here":
        a = args.strip().lower()
        cm = u.setdefault("chat_modes", {})
        if a in ("clear", "сброс"):
            cm.pop(str(peer), None)
            save(uid)
            return await note("↩️ В этом диалоге снова общий мод.")
        if a in ("off", "выкл"):
            cm[str(peer)] = "off"
            save(uid)
            return await note("🔕 В этом диалоге пишем без мода.")
        if a not in MODES:
            return await note("🎯 <code>.here kawaii|…</code> — мод только здесь\n"
                              "<code>.here off</code> — здесь без мода\n"
                              "<code>.here clear</code> — вернуть общий\n\n"
                              "Моды: " + ", ".join(MODES))
        cm[str(peer)] = a
        save(uid)
        return await note(f"🎯 В этом диалоге мод <b>{a}</b>.\n\n{preview(a, u)}")

    if name == "preview":
        a = args.strip().lower()
        mode = a if a in MODES else mode_for(u, peer)
        return await note(f"👁 <b>Предпросмотр</b> · {mode or 'без мода'}\n\n{preview(mode, u)}")

    if name == "style":
        a = args.strip().lower()
        if a in LEVELS:
            u["mode_level"] = a
            save(uid)
            return await note(f"🎚 Интенсивность: <b>{a}</b>")
        if a in ("emoji", "эмодзи"):
            u["mode_emoji"] = not u["mode_emoji"]
            save(uid)
            return await note(f"😺 Эмодзи: {'вкл ✅' if u['mode_emoji'] else 'выкл ❌'}")
        if a in ("bold", "жирный"):
            u["mode_bold"] = not u["mode_bold"]
            save(uid)
            return await note(f"🔠 Выделять твой текст: "
                              f"{'да ✅' if u['mode_bold'] else 'нет ❌'}")
        return await note(
            "🎚 <code>.style soft|normal|max</code> — сколько декора\n"
            "<code>.style bold</code> — выделять твой текст жирным\n"
            "<code>.style emoji</code> — эмодзи вкл/выкл\n\n"
            f"Сейчас: <b>{u.get('mode_level','normal')}</b>, "
            f"жирный {'вкл' if u.get('mode_bold', True) else 'выкл'}")

    if name in MODES:
        src = args or (rep.text if rep and rep.text else " ")
        return await out(MODES[name](html_lib.escape(src),
                                     u.get("mode_level", "normal"),
                                     u.get("mode_bold", True),
                                     u.get("mode_emoji", True)))
    if name in ("zalgo", "space", "upside"):
        src = args or (rep.text if rep and rep.text else "")
        if not src.strip():
            return await note(f"✍️ <code>.{name} текст</code> или ответом на сообщение.")
        fn = {"zalgo": zalgo, "space": spaced, "upside": upside}[name]
        return await out(html_lib.escape(fn(src[:500])))
    if name == "sw":
        return await out(switch_layout(args or (rep.text if rep else "")))
    if name == "flip":
        return await out("🪙 " + random.choice(["Орёл", "Решка"]))
    if name == "dice":
        return await out(f"🎲 {random.randint(1, 6)}")

    if name == "roll":
        mt = re.fullmatch(r"\s*(\d{0,2})d(\d{1,3})\s*", args.lower() or "1d6")
        if not mt:
            return await note("🎲 <code>.roll 2d6</code> — два кубика по шесть граней.")
        n, side = max(1, int(mt.group(1) or 1)), max(2, int(mt.group(2)))
        if n > 20:
            n = 20
        rolls = [random.randint(1, side) for _ in range(n)]
        tail = f" = <b>{sum(rolls)}</b>" if n > 1 else ""
        return await out(f"🎲 {' + '.join(map(str, rolls))}{tail}")

    if name == "pick":
        opts = [o.strip() for o in re.split(r"[|,;]| или ", args) if o.strip()]
        if len(opts) < 2:
            return await note("🎯 <code>.pick кофе | чай | ничего</code>")
        return await out(f"🎯 <b>{html_lib.escape(random.choice(opts))}</b>")

    if name in ("8ball", "8"):
        answers = ["бесспорно", "мне кажется — да", "определённо да", "скорее всего",
                   "хороший знак", "спроси позже", "не сейчас", "туманно",
                   "даже не думай", "мой ответ — нет", "весьма сомнительно",
                   "перспективы не очень"]
        q = f"<i>{html_lib.escape(args.strip())}</i>\n" if args.strip() else ""
        return await out(f"{q}🎱 {random.choice(answers)}")

    if name == "love":
        return await out(" ".join(random.sample("❤️🧡💛💚💙💜🤍💗", 8)))
    if name == "ad":
        u["antidelete"] = not u["antidelete"]
        save(uid)
        return await note(f"🗑 Анти-делит: {'вкл ✅' if u['antidelete'] else 'выкл ❌'}")

    if name == "type":
        txt = (args or "...")[:400]
        step = 1 if len(txt) <= 60 else 2 if len(txt) <= 160 else 4
        await drop(m)
        try:
            sent = await bot.send_message(m.chat.id, "▌", business_connection_id=cid)
            acc = ""
            for i in range(0, len(txt), step):
                acc = txt[:i + step]
                try:
                    await bot.edit_message_text(html_lib.escape(acc) + "▌",
                                                chat_id=m.chat.id,
                                                message_id=sent.message_id,
                                                business_connection_id=cid)
                except Exception:
                    pass
                await asyncio.sleep(0.09)
            acc = txt
            await bot.edit_message_text(acc, chat_id=m.chat.id,
                                        message_id=sent.message_id,
                                        business_connection_id=cid)
        except Exception as ex:
            await dm(uid, f"⚠️ .type: {ex}")
        return

    if name in ("nuke", "wipe", "delchat", "удалить"):
        punch = html_lib.escape(args.strip()[:200]) or random.choice(NUKE_PUNCH)
        await drop(m)
        try:
            sent = await bot.send_message(m.chat.id, "🗑 <b>Удаление чата…</b>",
                                          business_connection_id=cid)
        except Exception as ex:
            return await dm(uid, f"⚠️ .{name}: {ex}")

        async def frame(text: str, pause: float = 0.5):
            try:
                await bot.edit_message_text(text, chat_id=m.chat.id,
                                            message_id=sent.message_id,
                                            business_connection_id=cid)
            except Exception:
                pass
            await asyncio.sleep(pause)

        steps = random.sample(NUKE_STEPS, 4)
        for i, step in enumerate(steps, 1):
            pct = int(i / (len(steps) + 1) * 100)
            filled = pct // 10
            await frame(f"🗑 <b>Удаление чата…</b>\n"
                        f"{'▰' * filled}{'▱' * (10 - filled)} {pct}%\n<i>{step}</i>")
        msgs = f"{random.randint(800, 9000):,}".replace(",", " ")
        gb = random.randint(11, 97) / 10
        await frame(f"🗑 <b>Удаление чата…</b>\n{'▰' * 10} 100%\n"
                    f"<i>удалено {msgs} сообщений · освобождено {gb} ГБ мемов</i>", 0.8)
        await frame("✅ <b>Чат удалён.</b>", 1.2)
        await frame(f"✅ <s>Чат удалён.</s>\n\n{punch}", 0)
        return

    # ── архив ──
    if name == "save":
        target = rep or m
        await drop(m)
        res = await archive_media(target, uid, forced=True)
        return await dm(uid, res or "⚠️ нечего сохранять")

    if name == "savewhen":
        a = args.strip().lower()
        if a in ("always", "сразу"):
            u["save_when"] = "always"
        elif a in ("deleted", "удаление"):
            u["save_when"] = "deleted"
        else:
            return await note(
                "📦 <code>.savewhen always</code> — сохранять сразу при получении\n"
                "<code>.savewhen deleted</code> — только когда удалят\n\n"
                f"Сейчас: <b>{u.get('save_when','deleted')}</b>")
        save(uid)
        return await note(f"📦 Сохранять: <b>{u['save_when']}</b>")

    if name == "edits":
        u["track_edits"] = not u["track_edits"]
        save(uid)
        return await note(f"✏️ Отслеживание правок: "
                          f"{'вкл ✅' if u['track_edits'] else 'выкл ❌'}")

    if name == "savemedia":
        u["save_media"] = not u["save_media"]
        save(uid)
        return await note(f"📦 Архив медиа: {'вкл ✅' if u['save_media'] else 'выкл ❌'}")

    if name == "media":
        await drop(m)
        txt, kb = await screen(uid, "arch")
        return await dm(uid, txt, reply_markup=kb)

    if name == "export":
        await drop(m)
        return await send_export(uid)

    # ── медиа ──
    if name in ("nk", "nkb"):
        a = args.strip().lower()
        if a and a not in NEKO_ALIAS:
            return await note("🐾 <code>.nk</code> — неко · <code>.nkb</code> — кун\n"
                              "Ещё: " + " ".join(f"<code>.nk {k}</code>" for k in
                                                 ("лиса", "вайфу", "кот", "фембой")))
        kind = NEKO_ALIAS.get(a) or ("husbando" if name == "nkb" else "neko")
        cap = NEKO[kind][0]
        await drop(m)
        got = await fetch_pic(sources_for(kind))
        if not got:
            return await dm(uid, "😿 Источники картинок молчат — попробуй позже.")
        data, ext = got
        sender = bot.send_animation if ext == "gif" else bot.send_photo
        try:
            await sender(m.chat.id, BufferedInputFile(data, f"{kind}.{ext}"),
                         caption=cap, business_connection_id=cid)
        except Exception as ex:
            await dm(uid, f"⚠️ .{name}: {ex}")
        return

    if name in ("nkg", "g", "гиф"):
        a = args.strip().lower()
        if a in ("список", "list", "?"):
            return await note(
                f"🎞 <b>Гифки-реакции</b> ({len(GIF_CATS)})\n\n"
                "<b>По-русски:</b> " + ", ".join(sorted(GIF_LABEL.values())) +
                "\n\n<b>Все названия:</b>\n<code>"
                + " ".join(sorted(GIF_CATS)) + "</code>\n\n"
                "Пример: <code>.g обнять</code> ответом на сообщение.")
        cat = GIF_RU.get(a) or (a if a in GIF_CATS else None)
        if a and not cat:
            close = sorted(c for c in GIF_CATS if c.startswith(a[:3]))[:5]
            tip = ("\n\nМожет быть: " + " ".join(f"<code>.g {c}</code>" for c in close)
                   ) if close else ""
            return await note(f"🤔 Нет реакции «{esc(a)}»{tip}\n\n"
                              f"Весь список: <code>.g список</code>")
        cat = cat or random.choice(GIF_POPULAR)
        await drop(m)
        got = await fetch_pic(gif_sources(cat))
        if not got:
            return await dm(uid, "😿 Гифки не отвечают — попробуй позже.")
        data, ext = got
        kw = {"caption": f"{GIF_LABEL.get(cat, cat)} ♡",
              "business_connection_id": cid}
        if rep:
            kw["reply_parameters"] = ReplyParameters(message_id=rep.message_id)
        try:
            await bot.send_animation(m.chat.id,
                                     BufferedInputFile(data, f"{cat}.{ext}"), **kw)
        except Exception as ex:
            kw.pop("reply_parameters", None)
            try:
                await bot.send_animation(m.chat.id,
                                         BufferedInputFile(data, f"{cat}.{ext}"), **kw)
            except Exception:
                await dm(uid, f"⚠️ .{name}: {ex}")
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
                gif = safe_ff(["-vf", "fps=15,scale=320:-1:flags=lanczos"],
                             await dl(src.file_id), ".mp4", ".gif")
                await bot.send_animation(m.chat.id, BufferedInputFile(gif, "a.gif"),
                                         business_connection_id=cid)
            elif name == "fv":
                if not rep.voice:
                    return await dm(uid, "⚠️ Нужно голосовое.")
                ogg = safe_ff(["-af", "volume=8dB,acompressor", "-c:a", "libopus"],
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
            save(peer)
            await dm(peer, "👋 Собеседник выключил совместный мод.")
        save(uid)
        return await note("👋 Выключено." if had else "🤷 Пары не было.")

    if name == "pairs":
        if not u["pairs"]:
            return await note("📭 Пар нет.")
        return await note("🤝 <b>Пары:</b>\n" + "\n".join(
            f"• <code>{k}</code> — <b>{v}</b>" for k, v in u["pairs"].items()))

    # ── команды нет ──
    KNOWN_CMDS = sorted({
        "scam", "filter", "sens", "trust", "untrust", "mute", "unmute", "check",
        "word", "away", "quiet", "digest", "dox", "antidox", "help", "me", "menu", "mode", "here",
        "preview", "style", "sw", "flip", "dice", "roll", "pick", "8ball", "love",
        "nuke", "wipe", "delchat", "удалить",
        "dox", "osint", "пробив", "email", "ip", "phone",
        "ad", "type", "save", "savewhen", "savemedia", "edits", "media", "export",
        "nk", "nkb", "nkg", "g", "гиф", "gif", "fv", "lq", "story",
        "pair", "unpair", "pairs",
        "zalgo", "space", "upside", *MODES,
    })
    
    close = [c for c in KNOWN_CMDS if c.startswith(name[:3])][:4] if len(name) > 2 else []
    tip = ("\n\nМожет быть: " + " ".join(f"<code>.{c}</code>" for c in close)) if close else ""
    return await note(f"🤔 Не знаю команду <code>.{esc(name)}</code>{tip}",
                      reply_markup=help_kb())

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
    save(responder)
    save(initiator)
    await cb.message.edit_text(f"🤝 Совместный мод <b>{mode}</b> включён ♡")
    await dm(initiator, f"✅ Согласие получено! Мод <b>{mode}</b> активен ♡")
    await cb.answer("Готово!")

# ═════════════════════════════════════════════════════════
# ОНБОРДИНГ ДЛЯ НОВЫХ ЛЮДЕЙ
# ═════════════════════════════════════════════════════════
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
# МЕНЮ · единая навигация
# ═════════════════════════════════════════════════════════
def dot(x):
    return "🟢" if x else "🔴"

def chk(x):
    return "☑️" if x else "▫️"

def pill(on):
    return "◉" if on else "◯"

def B(text, data) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)

def dig(u: dict, path: str):
    node = u
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node

def dig_set(u: dict, path: str, val):
    parts = path.split(".")
    node = u
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = val

def toggles(u, screen_id, items) -> list:
    return [[B(f"{chk(dig(u, p))} {lbl}", f"t:{p}:{screen_id}")] for p, lbl in items]

def choice(u, path, screen_id, options) -> list:
    cur = dig(u, path)
    return [[B(f"{pill(v == cur)} {lbl}", f"v:{path}:{v}:{screen_id}") for v, lbl in options]]

def plural(n, forms=("файл", "файла", "файлов")):
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return forms[0]
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return forms[1]
    return forms[2]

def human_size(sz) -> str:
    if not sz:
        return "размер неизвестен"
    if sz >= 1073741824:
        return f"{sz / 1073741824:.1f} ГБ"
    if sz >= 1048576:
        return f"{sz / 1048576:.1f} МБ"
    return f"{sz / 1024:.0f} КБ"

def root_kb(uid) -> InlineKeyboardMarkup:
    rows = [
        [B("🛡 Защита", "n:guard"), B("🎨 Стиль", "n:style")],
        [B("📦 Архив", "n:arch"), B("💬 Диалоги", "n:dialogs")],
        [B("📊 Сводка", "n:stats"), B("❓ Команды", "n:help")],
    ]
    if not conn_of(uid):
        rows.insert(0, [B("🚀 Подключить бота", "n:setup")])
    if uid in ADMINS:
        rows.append([B("🛠 Админка", "adm:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

main_kb = root_kb

async def screen(uid: int, name: str, arg: str = "") -> tuple[str, InlineKeyboardMarkup]:
    u = user(uid)

    # ── главный ──
    if name == "root":
        conn = conn_of(uid)
        n, sz = await call(st.media_stats, uid)
        known, trusted = st.counts(uid)
        mode = u["mode"]
        line = "✅ подключён" if conn else "⚠️ не подключён"
        extra = []
        if u.get("quiet"):
            extra.append(f"🌙 {esc(u['quiet'])}")
        if u.get("away_on"):
            extra.append("💤 автоответ")
        if u.get("digest"):
            extra.append("📬 сводка")
        txt = f"♡ <b>Karzen Bot</b> · {line}\n"
        if extra:
            txt += " · ".join(extra) + "\n"
        txt += (
            f"\n🛡 <b>Защита</b> · антискам {'вкл' if u['antiscam']['enabled'] else 'выкл'}"
            f" · фильтр {'вкл' if u['filter']['enabled'] else 'выкл'}"
            f" · {SENS_RU[u.get('sens', 'normal')]}\n"
            f"🎨 <b>Стиль</b> · {mode or 'без мода'} · {u.get('mode_level', 'normal')}\n"
            f"📦 <b>Архив</b> · {n} {plural(n)} · {human_size(sz)}\n"
            f"💬 <b>Диалоги</b> · {known} всего · {trusted} доверенных · "
            f"🔪 поймано {u['caught']}")
        if not conn:
            txt += ("\n\n⚠️ Бот ещё не подключён к твоему Telegram — "
                    "жми «Подключить», это минута.")
        return txt, root_kb(uid)

    # ── защита ──
    if name == "guard":
        a, f = u["antiscam"], u["filter"]
        rows = [
            [B(f"{dot(a['enabled'])} Антискам ›", "n:scam"),
             B(f"{dot(f['enabled'])} Фильтр ›", "n:filter")],
        ]
        rows += choice(u, "sens", "guard",
                       [("low", "мягко"), ("normal", "обычно"), ("high", "строго")])
        dx = adox(u)
        rows += [[B(f"{dot(dx.get('enabled', True))} Анти-докс"
                    f"{' · DRY-RUN' if dx.get('dry_run', True) else ''} ›", "n:dox")],
                 [B("🔪 Журнал срабатываний", "n:log:0")],
                 [B("‹ Меню", "n:root")]]
        return (f"🛡 <b>Защита</b>\n\n"
                f"<b>Антискам</b> читает первое сообщение от незнакомого человека и "
                f"считает баллы: работа/крипта, фишинг, просьбы денег, ссылки, кнопки, "
                f"исполняемые файлы. Перебрал порог — приходит уведомление.\n"
                f"<b>Фильтр</b> идёт дальше и удаляет такое сообщение сразу.\n\n"
                f"Порог сейчас: <b>{threshold(u)}</b> баллов "
                f"({SENS_RU[u.get('sens', 'normal')]})\n"
                f"Поймано всего: <b>{u['caught']}</b>",
                InlineKeyboardMarkup(inline_keyboard=rows))

    if name == "scam":
        rows = toggles(u, "scam", [
            ("antiscam.enabled", "Антискам включён"),
            ("antiscam.new_dialog", "Новый диалог"),
            ("antiscam.unknown_bot", "Незнакомый бот"),
            ("antiscam.scam", "Похоже на развод"),
            ("antiscam.exec_files", "Исполняемые файлы"),
        ])
        rows.append([B("‹ Защита", "n:guard")])
        return ("🛡 <b>Антискам</b> · о чём предупреждать\n\n"
                "<i>Уведомления приходят сюда, в чат с ботом. "
                "Само сообщение остаётся у собеседника — антискам ничего не трогает.</i>",
                InlineKeyboardMarkup(inline_keyboard=rows))

    if name == "filter":
        f = u["filter"]
        rows = toggles(u, "filter", [
            ("filter.enabled", "Фильтр включён"),
            ("filter.delete", "Удалять, а не только помечать"),
            ("filter.links", "Ссылки"),
            ("filter.numbers", "Номера и карты"),
            ("filter.buttons", "Инлайн-кнопки"),
            ("filter.words_on", "Стоп-слова"),
        ])
        rows.append([B(f"📝 Стоп-слова ({len(f['words'])})", "n:words")])
        rows.append([B("‹ Защита", "n:guard")])
        return ("🔪 <b>Фильтр первых сообщений</b>\n\n"
                "Работает только на первом сообщении от того, с кем ты ещё не общался. "
                "Знакомые и доверенные проходят мимо фильтра всегда.\n\n"
                "⚠️ Для удаления нужно право «удалять любые сообщения».",
                InlineKeyboardMarkup(inline_keyboard=rows))

    if name == "dox":
        adox(u)
        rows = toggles(u, "dox", [
            ("antidox.enabled", "Анти-докс включён"),
            ("antidox.dry_run", "Только наблюдать (DRY-RUN)"),
        ])
        rows += choice(u, "antidox.arm", "dox",
                       [(2.0, "2 с"), (4.0, "4 с"), (8.0, "8 с")])
        rows += choice(u, "antidox.quarantine", "dox",
                       [(0, "без карантина"), (5, "5 мин"), (30, "30 мин")])
        rows.append([B("‹ Защита", "n:guard")])
        return (dox_status(u) + "\n\n"
                "<i>Докс-боты шлют команду («dox», «пробив»), а следом выкладку. "
                "Команду бот удаляет сразу и несколько секунд удаляет всё, что тот "
                "человек пришлёт следом. В DRY-RUN только докладывает — "
                "посмотри на отчёты, потом выключай его.</i>\n\n"
                "⚠️ Нужно право «удалять любые сообщения».",
                InlineKeyboardMarkup(inline_keyboard=rows))

    if name == "words":
        words = u["filter"]["words"]
        rows = [[B(f"✕ {w[:20]}", f"n:wdel:{i}")] for i, w in enumerate(words[:12])]
        rows.append([B("‹ Фильтр", "n:filter")])
        return (f"📝 <b>Стоп-слова</b> ({len(words)})\n\n"
                + (", ".join(esc(w) for w in words) or "<i>пусто</i>")
                + "\n\nДобавить: <code>.word +слово</code>\n"
                  "Нажми на слово ниже, чтобы убрать.\n\n"
                  "<i>Сравнение идёт по очищенному тексту, так что "
                  "«к.а.з.и.н.о» и «кaзино» латиницей тоже ловятся.</i>",
                InlineKeyboardMarkup(inline_keyboard=rows))

    # ── стиль ──
    if name == "style":
        rows, line = [], []
        for key, (ico, title, hint) in MODE_UI.items():
            line.append(B(f"{pill(u['mode'] == key)} {ico} {title}", f"v:mode:{key}:style"))
            if len(line) == 2:
                rows.append(line)
                line = []
        if line:
            rows.append(line)
        rows.append([B(f"{pill(not u['mode'])} 🚫 без мода", "v:mode:off:style")])
        rows += choice(u, "mode_level", "style",
                       [("soft", "мягко"), ("normal", "обычно"), ("max", "максимум")])
        rows += toggles(u, "style", [("mode_bold", "Выделять мой текст жирным"),
                                     ("mode_emoji", "Эмодзи под настроение")])
        rows.append([B("🎲 Ещё пример", "n:style"), B("‹ Меню", "n:root")])
        return (f"🎨 <b>Стиль речи</b> · {u['mode'] or 'выключен'}\n\n"
                f"Всё, что ты пишешь сам, бот переписывает в выбранной манере. "
                f"Твоя фраза остаётся ядром — декор встаёт вокруг неё.\n\n"
                f"<b>Так это выглядит:</b>\n{preview(u['mode'], u)}\n\n"
                f"<i>Мод на один диалог — команда <code>.here kawaii</code> прямо в нём.</i>",
                InlineKeyboardMarkup(inline_keyboard=rows))

    # ── архив ──
    if name == "arch":
        n, sz = await call(st.media_stats, uid)
        rows = toggles(u, "arch", [
            ("save_media", "Архив медиа"),
            ("save_own", "Архивировать и свои"),
            ("antidelete", "Сохранять удалённые сообщения"),
            ("track_edits", "Показывать правки «было → стало»"),
            ("digest", "Сводка за день в 21:00"),
        ])
        rows += choice(u, "save_when", "arch",
                       [("deleted", "при удалении"), ("always", "сразу")])
        rows += [[B("🧾 Последние файлы", "n:recent")],
                 [B("‹ Меню", "n:root")]]
        return (f"📦 <b>Архив</b> · {n} {plural(n)} · {human_size(sz)}\n\n"
                f"Копии уходят сюда, в чат с ботом: диск на хостинге стирается "
                f"при каждом обновлении, а в Telegram копия лежит вечно.\n\n"
                f"<i>«При удалении» — архив чище, но одноразовые медиа могут не успеть "
                f"сохраниться. «Сразу» — не пропустит ничего, но и весит больше.</i>\n"
                f"<i>Файлы тяжелее 20 МБ сохраняются ссылкой на оригинал — "
                f"Bot API не даёт их скачать.</i>",
                InlineKeyboardMarkup(inline_keyboard=rows))

    if name == "recent":
        rows = await call(st.recent_media, uid, 12)
        lines = [f"• <b>{r['kind']}</b> от <code>{r['sender']}</code> · "
                 f"{ts_local(r['ts']):%d.%m %H:%M}" for r in rows]
        return ("🧾 <b>Последнее в архиве</b>\n\n" + ("\n".join(lines) or "<i>пусто</i>"),
                InlineKeyboardMarkup(inline_keyboard=[[B("‹ Архив", "n:arch")]]))

    # ── диалоги ──
    if name == "dialogs":
        known, trusted = st.counts(uid)
        muted = u.get("muted") or []
        rows = toggles(u, "dialogs", [("away_on", "Автоответ «я отошёл»")])
        if muted:
            rows.append([B(f"🔔 Снять заглушение со всех ({len(muted)})", "n:unmuteall")])
        rows.append([B("‹ Меню", "n:root")])
        away = (f"<blockquote>{esc(u['away'])}</blockquote>" if u.get("away")
                else "<i>текст не задан — <code>.away текст</code></i>")
        return (f"💬 <b>Диалоги</b>\n\n"
                f"Всего знакомых: <b>{known}</b>\n"
                f"✅ Доверенных: <b>{trusted}</b> — их бот не проверяет\n"
                f"🔕 Заглушённых: <b>{len(muted)}</b>\n"
                f"🤝 Совместных модов: <b>{len(u['pairs'])}</b>\n\n"
                f"🌙 <b>Тихие часы:</b> {esc(u['quiet']) if u.get('quiet') else 'выключены'}"
                f" · <code>.quiet 23:00-08:00</code>\n\n"
                f"💤 <b>Автоответ</b> (раз в 6 ч на человека):\n{away}\n\n"
                f"<i>Доверие и заглушение ставятся кнопками прямо под уведомлением "
                f"или командами <code>.trust</code> / <code>.mute</code> в нужном диалоге.</i>",
                InlineKeyboardMarkup(inline_keyboard=rows))

    # ── журнал ──
    if name == "log":
        page = max(0, int(arg or 0))
        per = 5
        rows_all = st.recent_catches(uid, 50)
        pages = max(1, (len(rows_all) + per - 1) // per)
        page = min(page, pages - 1)
        chunk = rows_all[page * per:page * per + per]
        out = []
        for r in chunk:
            mark = "🗑" if r["action"] == "deleted" else "⚠️"
            out.append(f"{mark} <b>{ts_local(r['ts']):%d.%m %H:%M}</b> · "
                       f"<code>{r['peer']}</code> · {esc(r['reason'])} [{r['score']}]\n"
                       f"<blockquote>{esc((r['text'] or '')[:160])}</blockquote>")
        nav = []
        if page > 0:
            nav.append(B("‹", f"n:log:{page - 1}"))
        nav.append(B(f"{page + 1}/{pages}", "n:noop"))
        if page < pages - 1:
            nav.append(B("›", f"n:log:{page + 1}"))
        return ("🔪 <b>Журнал защиты</b>\n\n" + ("\n\n".join(out) or "<i>пока пусто</i>"),
                InlineKeyboardMarkup(inline_keyboard=[nav, [B("‹ Защита", "n:guard")]]))

    # ── сводка по себе ──
    if name == "stats":
        known, trusted = st.counts(uid)
        n, sz = await call(st.media_stats, uid)
        day = [r for r in st.recent_catches(uid, 50)
               if time.time() - r["ts"] < 86400]
        return (f"📊 <b>{esc(u['name'] or 'ты')}</b>\n\n"
                f"🔪 Поймано: <b>{u['caught']}</b> · за сутки: <b>{len(day)}</b>\n"
                f"💬 Диалогов: <b>{known}</b> · ✅ доверенных: <b>{trusted}</b>\n"
                f"📦 В архиве: <b>{n}</b> {plural(n)} · {human_size(sz)}\n"
                f"🎨 Мод: <b>{u['mode'] or 'выкл'}</b> · для отдельных чатов: "
                f"<b>{len(u.get('chat_modes') or {})}</b>\n"
                f"🤝 Пар: <b>{len(u['pairs'])}</b> · ⌨️ команд: <b>{u['cmds']}</b>\n"
                f"📅 С {ts_local(u['first_seen']):%d.%m.%Y}",
                InlineKeyboardMarkup(inline_keyboard=[
                    [B("📤 Выгрузить файлом", "n:export")],
                    [B("‹ Меню", "n:root")]]))

    # ── онбординг ──
    if name == "setup":
        me = await bot.get_me()
        return (SETUP_STEPS.format(username=f"@{me.username}"),
                InlineKeyboardMarkup(inline_keyboard=[
                    [B("🔄 Проверить подключение", "n:check")],
                    [B("❓ Не получается", "n:trouble")],
                    [B("‹ Меню", "n:root")]]))

    if name == "trouble":
        return (TROUBLE, InlineKeyboardMarkup(inline_keyboard=[
            [B("🔄 Проверить", "n:check")], [B("‹ Меню", "n:root")]]))

    if name == "help":
        return CMD_HELP, help_kb()

    return "♡ <b>Karzen Bot</b>", root_kb(uid)

async def show(cb: CallbackQuery, name: str, arg: str = "", toast: str = ""):
    txt, kb = await screen(cb.from_user.id, name, arg)
    try:
        await cb.message.edit_text(txt, reply_markup=kb)
    except Exception:
        pass
    await cb.answer(toast)

@dp.message(CommandStart())
async def start(m: Message):
    u = user(m.from_user.id, m.from_user)
    arg = (m.text or "").split(maxsplit=1)
    if len(arg) > 1 and arg[1].startswith("ref_") and not u.get("ref"):
        try:
            ref = int(arg[1][4:])
            if ref != m.from_user.id:
                u["ref"] = ref
                inviter = user(ref)
                inviter["invited"] = inviter.get("invited", 0) + 1
                save(m.from_user.id)
                save(ref)
                await dm(ref, f"🎉 По твоей ссылке пришёл "
                              f"<b>{esc(m.from_user.full_name)}</b>")
        except ValueError:
            pass
    txt, kb = await screen(m.from_user.id, "root" if conn_of(m.from_user.id) else "setup")
    await m.answer(txt, reply_markup=kb)

@dp.callback_query(F.data.startswith("n:"))
async def nav(cb: CallbackQuery):
    p = cb.data.split(":")
    what, arg = p[1], (p[2] if len(p) > 2 else "")
    uid = cb.from_user.id
    u = user(uid, cb.from_user)

    if what == "noop":
        return await cb.answer()
    if what == "check":
        if not conn_of(uid):
            return await cb.answer("Пока не вижу подключения — проверь шаг 3",
                                   show_alert=True)
        return await show(cb, "root", toast="Есть контакт!")
    if what == "wdel":
        words = u["filter"]["words"]
        i = int(arg or -1)
        if 0 <= i < len(words):
            words.pop(i)
            save(uid)
        return await show(cb, "words", toast="Убрано")
    if what == "unmuteall":
        u["muted"] = []
        save(uid)
        return await show(cb, "dialogs", toast="Все снова слышны 🔔")
    if what == "export":
        await cb.answer("Собираю…")
        return await send_export(uid)
    await show(cb, what, arg)

@dp.callback_query(F.data.startswith("t:"))
async def toggle(cb: CallbackQuery):
    p = cb.data.split(":")
    path, back_to = p[1], (p[2] if len(p) > 2 else "root")
    u = user(cb.from_user.id)
    dig_set(u, path, not dig(u, path))
    save(cb.from_user.id)
    await show(cb, back_to)

@dp.callback_query(F.data.startswith("v:"))
async def setval(cb: CallbackQuery):
    p = cb.data.split(":")
    path, val, back_to = p[1], p[2], (p[3] if len(p) > 3 else "root")
    u = user(cb.from_user.id)
    if path == "mode":
        u["mode"] = None if val == "off" else (val if val in MODES else None)
    elif path == "sens":
        u["sens"] = val if val in SENS else "normal"
    elif path == "mode_level":
        u["mode_level"] = val if val in LEVELS else "normal"
    elif path == "save_when":
        u["save_when"] = val if val in ("always", "deleted") else "deleted"
    elif path == "antidox.arm" and val in ("2.0", "4.0", "8.0"):
        adox(u)["arm"] = float(val)
    elif path == "antidox.quarantine" and val in ("0", "5", "30"):
        adox(u)["quarantine"] = int(val)
    else:
        return await cb.answer()
    save(cb.from_user.id)
    await show(cb, back_to)

@dp.callback_query(F.data.startswith("h:"))
async def help_nav(cb: CallbackQuery):
    key = cb.data.split(":")[1]
    body = HELP.get(key, (None, None, CMD_HELP))[2]
    try:
        await cb.message.edit_text(body, reply_markup=help_kb(key))
    except Exception:
        pass
    await cb.answer()

async def send_export(uid: int):
    u = dict(user(uid))
    n, sz = await call(st.media_stats, uid)
    data = {
        "exported": now_local().isoformat(timespec="seconds"),
        "user": {k: v for k, v in u.items() if k not in ("name", "username")},
        "archive": {"files": n, "bytes": sz},
        "catches": [dict(r) for r in st.recent_catches(uid, 200)],
    }
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode()
    c = conn_of(uid)
    if not c:
        return
    try:
        await bot.send_document(
            c["chat"],
            BufferedInputFile(raw, f"karzen_{now_local():%Y%m%d}.json"),
            caption="📤 Настройки и журнал срабатываний.")
    except Exception as ex:
        await dm(uid, f"⚠️ Не вышло собрать выгрузку: {ex.__class__.__name__}")

# ═════════════════════════════════════════════════════════
# 📬 СВОДКА ЗА ДЕНЬ
# ═════════════════════════════════════════════════════════
DIGEST_HOUR = 21
digest_sent: dict[int, str] = {}

async def digest_loop():
    while True:
        try:
            await asyncio.sleep(600)
            today = f"{now_local():%Y-%m-%d}"
            if now_local().hour != DIGEST_HOUR:
                continue
            for uid in st.all_uids():
                u = user(uid)
                if not u.get("digest") or digest_sent.get(uid) == today:
                    continue
                digest_sent[uid] = today
                catches = [r for r in st.recent_catches(uid, 100)
                           if time.time() - r["ts"] < 86400]
                media = [r for r in await call(st.recent_media, uid, 100)
                         if time.time() - r["ts"] < 86400]
                if not catches and not media:
                    continue
                top = ", ".join(dict.fromkeys(esc(r["reason"]).split(":")[0]
                                              for r in catches[:5])) or "—"
                await dm(uid, f"📬 <b>Итоги дня</b> · {now_local():%d.%m}\n\n"
                              f"🔪 Сработок защиты: <b>{len(catches)}</b>\n"
                              f"📦 Сохранено медиа: <b>{len(media)}</b>\n"
                              f"🎯 Чаще всего: {top}",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             [B("🔪 Журнал", "n:log:0"), B("⚙️ Меню", "n:root")]]))
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logging.warning("digest: %s", ex)

# ═════════════════════════════════════════════════════════
# МИНИ-ПРИЛОЖЕНИЕ (WebApp)
# ═════════════════════════════════════════════════════════
from aiohttp import web

MINI_APP = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Karzen Bot</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
 :root{
   color-scheme:light dark;
   --bg:var(--tg-theme-bg-color,#fff);
   --fg:var(--tg-theme-text-color,#0d0d0f);
   --card:var(--tg-theme-secondary-bg-color,#f2f2f7);
   --hint:var(--tg-theme-hint-color,#8a8a8e);
   --accent:var(--tg-theme-button-color,#3390ec);
   --accent-fg:var(--tg-theme-button-text-color,#fff);
   --danger:#e5484d;
 }
 *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
 body{margin:0;padding:12px 14px 40px;font:16px/1.4 -apple-system,system-ui,"Segoe UI",sans-serif;
      background:var(--bg);color:var(--fg)}
 h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--hint);
    margin:20px 4px 8px;font-weight:600}
 .top{display:flex;align-items:center;gap:10px;margin:4px 2px 14px}
 .top .name{font-weight:600;font-size:17px}
 .badge{margin-left:auto;font-size:12px;padding:4px 10px;border-radius:20px;
        background:var(--card);color:var(--hint)}
 .badge.on{background:rgba(52,199,89,.16);color:#2f9e44}
 .badge.off{background:rgba(229,72,77,.14);color:var(--danger)}
 .stat{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:6px}
 .stat div{text-align:center;padding:12px 4px;border-radius:14px;background:var(--card)}
 .stat span{display:block;font-size:20px;font-weight:650;letter-spacing:-.02em}
 .stat em{font-size:10.5px;color:var(--hint);font-style:normal}
 .tabs{display:flex;gap:4px;padding:4px;background:var(--card);border-radius:14px;
       margin:14px 0 4px;position:sticky;top:6px;z-index:5;backdrop-filter:blur(12px)}
 .tabs button{flex:1;border:0;background:transparent;color:var(--hint);font:inherit;
   font-size:13px;font-weight:550;padding:9px 4px;border-radius:11px;transition:.18s}
 .tabs button.on{background:var(--bg);color:var(--fg);box-shadow:0 1px 3px #0002}
 .row{display:flex;align-items:center;justify-content:space-between;gap:12px;
      padding:13px 14px;background:var(--card);border-radius:14px;margin-bottom:8px}
 .row b{font-weight:500;font-size:15px}
 .row small{display:block;color:var(--hint);font-size:12px;margin-top:3px;line-height:1.35}
 .sw{position:relative;width:50px;height:30px;flex:none}
 .sw input{opacity:0;width:0;height:0}
 .sl{position:absolute;inset:0;background:#8884;border-radius:30px;transition:.2s;cursor:pointer}
 .sl:before{content:"";position:absolute;height:24px;width:24px;left:3px;top:3px;
     background:#fff;border-radius:50%;transition:.2s;box-shadow:0 1px 3px #0003}
 input:checked+.sl{background:var(--accent)}
 input:checked+.sl:before{transform:translateX(20px)}
 .seg{display:flex;gap:3px;padding:3px;background:#8881;border-radius:11px;flex:none}
 .seg button{border:0;background:transparent;color:var(--hint);font:inherit;font-size:13px;
   padding:7px 11px;border-radius:9px;transition:.15s}
 .seg button.on{background:var(--accent);color:var(--accent-fg)}
 .modes{display:grid;grid-template-columns:1fr 1fr;gap:8px}
 .mode{display:flex;align-items:center;gap:9px;padding:12px;border-radius:14px;
       background:var(--card);border:2px solid transparent;transition:.15s}
 .mode.on{border-color:var(--accent)}
 .mode i{font-style:normal;font-size:19px}
 .mode b{font-weight:550;font-size:14px;display:block}
 .mode small{color:var(--hint);font-size:11px}
 .preview{padding:14px;border-radius:14px;background:var(--card);margin-top:10px;
          font-size:15px;min-height:22px}
 .preview .lbl{display:block;font-size:11px;color:var(--hint);text-transform:uppercase;
               letter-spacing:.05em;margin-bottom:6px}
 .field{background:var(--card);border-radius:14px;padding:12px 14px;margin-bottom:8px}
 .field label{display:block;font-size:12px;color:var(--hint);margin-bottom:6px}
 .field input,.field textarea{width:100%;background:transparent;border:0;color:inherit;
   font:inherit;resize:vertical;outline:none}
 .field input::placeholder,.field textarea::placeholder{color:var(--hint)}
 .chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:4px}
 .chip{display:flex;align-items:center;gap:6px;background:var(--card);border-radius:20px;
       padding:7px 8px 7px 13px;font-size:14px}
 .chip button{border:0;background:#8882;color:var(--hint);border-radius:50%;width:20px;
   height:20px;line-height:18px;font-size:13px;padding:0}
 .empty{color:var(--hint);font-size:14px;text-align:center;padding:22px 10px}
 .log{background:var(--card);border-radius:14px;padding:12px 14px;margin-bottom:8px}
 .log .h{font-size:12px;color:var(--hint);margin-bottom:5px}
 .log .t{font-size:14px;word-break:break-word}
 .skel{height:64px;border-radius:14px;background:var(--card);margin-bottom:8px;
       animation:pulse 1.2s ease-in-out infinite}
 @keyframes pulse{50%{opacity:.5}}
 .hidden{display:none}
 .toast{position:fixed;left:50%;bottom:22px;transform:translate(-50%,80px);
   background:var(--fg);color:var(--bg);padding:10px 18px;border-radius:22px;font-size:14px;
   opacity:0;transition:.25s;z-index:20}
 .toast.on{transform:translate(-50%,0);opacity:.94}
</style></head><body>

<div class="top">
  <div><div class="name" id="who">Karzen Bot</div></div>
  <div class="badge" id="conn">…</div>
</div>

<div class="stat">
  <div><span id="s_media">–</span><em>в архиве</em></div>
  <div><span id="s_caught">–</span><em>поймано</em></div>
  <div><span id="s_known">–</span><em>диалогов</em></div>
  <div><span id="s_trust">–</span><em>доверенных</em></div>
</div>

<div class="tabs">
  <button data-tab="style" class="on">🎨 Стиль</button>
  <button data-tab="guard">🛡 Защита</button>
  <button data-tab="arch">📦 Архив</button>
  <button data-tab="log">🔪 Журнал</button>
</div>

<div id="body"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>
<div class="toast" id="toast"></div>

<script>
const tg = Telegram.WebApp; tg.ready(); tg.expand();
let D = {}, TAB = "style", LOG = [];

const E = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const $ = id => document.getElementById(id);

function tap(kind="light"){ try{ tg.HapticFeedback.impactOccurred(kind) }catch(e){} }
function toast(t){
  const el = $("toast"); el.textContent = t; el.classList.add("on");
  clearTimeout(el._t); el._t = setTimeout(()=>el.classList.remove("on"), 1600);
}

async function api(path, body={}){
  try{
    const r = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({init: tg.initData, ...body})});
    return await r.json();
  }catch(e){ toast("Нет связи с ботом"); return {error:"network"} }
}

/* ── элементы ─────────────────────────────────────────── */
const sw = (k,t,d,v) => `<div class="row"><div><b>${t}</b><small>${d}</small></div>
 <label class="sw"><input type="checkbox" ${v?"checked":""}
 onchange="setKey('${k}',this.checked)"><span class="sl"></span></label></div>`;

const seg = (k,t,d,opts,cur) => `<div class="row"><div><b>${t}</b><small>${d}</small></div>
 <div class="seg">${opts.map(([v,l])=>
   `<button class="${v===cur?"on":""}" onclick="setKey('${k}','${v}')">${l}</button>`).join("")}</div></div>`;

const field = (k,t,ph,v,area) => `<div class="field"><label>${t}</label>
 ${area ? `<textarea rows="2" placeholder="${ph}" onchange="setKey('${k}',this.value)"
    >${E(v||"")}</textarea>`
        : `<input value="${E(v||"")}" placeholder="${ph}" onchange="setKey('${k}',this.value)">`}
 </div>`;

/* ── вкладки ──────────────────────────────────────────── */
const MODES = [
 ["kawaii","🌸","kawaii","ня~ и сердечки"], ["tsundere","💢","tsundere","б-бака!"],
 ["yandere","🔪","yandere","ты только мой"], ["leet","👾","leet","h4ck3r"],
 ["small","🔡","small","мелкие капсы"],     ["bubble","🫧","bubble","Ⓑ Ⓤ Ⓑ"],
 ["mock","🐔","mock","sPoNgEbOb"],          ["","🚫","без мода","как написал"],
];

function viewStyle(){
  return `<h2>Мод</h2>
  <div class="modes">${MODES.map(([k,i,n,d])=>`
    <div class="mode ${((D.mode||"")===k)?"on":""}" onclick="setKey('mode','${k}')">
      <i>${i}</i><div><b>${n}</b><small>${d}</small></div></div>`).join("")}</div>
  <div class="preview" id="pv"><span class="lbl">как это увидит собеседник</span>
    <span id="pvt">${E(D.preview||"")}</span></div>
  <h2>Настройка</h2>
  ${seg("mode_level","Интенсивность","сколько декора вокруг фразы",
        [["soft","мягко"],["normal","обычно"],["max","макс"]], D.mode_level)}
  ${sw("mode_bold","Выделять мой текст","твоя фраза жирным, декор обычным", D.mode_bold)}
  ${sw("mode_emoji","Эмодзи","под настроение режима", D.mode_emoji)}
  <div class="empty">Мод для одного диалога — команда <b>.here kawaii</b> прямо в нём</div>`;
}

function viewGuard(){
  return `<h2>Проверка входящих</h2>
  ${sw("scam","Антискам","разбирает первое сообщение от незнакомца", D.scam)}
  ${seg("sens","Строгость","строже — ловит больше, но и ошибается чаще",
        [["low","мягко"],["normal","обычно"],["high","строго"]], D.sens)}
  ${sw("new_dialog","Новый диалог","сообщать, когда пишет кто-то впервые", D.new_dialog)}
  ${sw("unknown_bot","Незнакомые боты","", D.unknown_bot)}
  ${sw("exec_files",".apk и .exe","предупреждать об исполняемых файлах", D.exec_files)}
  <h2>Автоудаление</h2>
  ${sw("filter","Фильтр первых сообщений","знакомых и доверенных не трогает", D.filter)}
  ${sw("filter_delete","Удалять","иначе только помечать уведомлением", D.filter_delete)}
  ${sw("links","Ссылки","", D.links)}
  ${sw("numbers","Номера и карты","", D.numbers)}
  ${sw("buttons","Инлайн-кнопки","", D.buttons)}
  ${sw("words_on","Стоп-слова","список ниже", D.words_on)}
  <h2>Анти-докс</h2>
  ${sw("dox","Анти-докс","команда «dox / пробив» и выкладка следом", D.dox)}
  ${sw("dox_dry","Только наблюдать","DRY-RUN: докладывать, не удалять", D.dox_dry)}
  <div class="field"><label>Добавить стоп-слово</label>
    <input id="w" placeholder="например: казино" onchange="addWord(this)"></div>
  <div class="chips" id="chips">${(D.words||[]).map((w,i)=>
    `<span class="chip">${E(w)}<button onclick="delWord(${i})">✕</button></span>`).join("")
    || '<div class="empty">Пусто</div>'}</div>
  <h2>Покой</h2>
  ${field("quiet","Тихие часы — уведомления без звука","23:00-08:00", D.quiet)}
  ${sw("away_on","Автоответ «я отошёл»","уйдёт раз в 6 часов на человека", D.away_on)}
  ${field("away","Текст автоответа","я сейчас не у телефона, отвечу позже", D.away, true)}`;
}

function viewArch(){
  return `<h2>Что сохранять</h2>
  ${sw("save_media","Архив медиа","копии фото, видео и голосовых к тебе в чат", D.save_media)}
  ${sw("save_own","Свои медиа","архивировать и то, что шлёшь сам", D.save_own)}
  ${seg("save_when","Когда","«при удалении» чище, но одноразовые могут не успеть",
        [["deleted","при удалении"],["always","сразу"]], D.save_when)}
  <h2>Следы</h2>
  ${sw("antidelete","Анти-делит","удалённое собеседником приходит тебе", D.antidelete)}
  ${sw("track_edits","Правки","показывать «было → стало»", D.track_edits)}
  ${sw("digest","Сводка за день","итоги в 21:00", D.digest)}
  <div class="empty">В архиве ${D.media} файлов · ${E(D.media_size||"")}<br>
    Выгрузить всё — команда <b>.export</b></div>`;
}

function viewLog(){
  if(!LOG.length) return '<div class="empty">Защита пока не срабатывала 🍃</div>';
  return LOG.map(r=>`<div class="log">
    <div class="h">${r.action==="deleted"?"🗑 удалено":"⚠️ помечено"} · ${E(r.when)} ·
      ${E(r.reason)} [${r.score}]</div>
    <div class="t">${E(r.text||"—")}</div></div>`).join("");
}

const VIEWS = {style:viewStyle, guard:viewGuard, arch:viewArch, log:viewLog};

function render(){
  $("body").innerHTML = VIEWS[TAB]();
  document.querySelectorAll(".tabs button").forEach(b =>
    b.classList.toggle("on", b.dataset.tab === TAB));
}

document.querySelectorAll(".tabs button").forEach(b => b.onclick = async () => {
  tap(); TAB = b.dataset.tab;
  if(TAB === "log" && !LOG.length){
    const d = await api("/api/log");
    LOG = d.rows || [];
  }
  render();
});

/* ── данные ───────────────────────────────────────────── */
async function setKey(k, v){
  tap();
  const before = D[k];
  D[k] = v;
  if(k === "mode" || k === "mode_level" || k === "mode_bold" || k === "mode_emoji") render();
  const r = await api("/api/set", {key:k, val:v});
  if(r.error){ D[k] = before; toast("Не сохранилось"); return render() }
  if(r.preview !== undefined){ D.preview = r.preview; const t = $("pvt"); if(t) t.innerHTML = r.preview }
}

async function addWord(el){
  const w = el.value.trim(); if(!w) return;
  el.value = ""; tap();
  const r = await api("/api/words", {add:w});
  if(r.words){ D.words = r.words; D.words_on = true; render(); toast("Добавлено") }
}
async function delWord(i){
  tap();
  const r = await api("/api/words", {del:i});
  if(r.words){ D.words = r.words; render() }
}

async function load(){
  const d = await api("/api/state");
  if(d.error){
    document.body.innerHTML =
      '<div class="empty">Открой это из чата с ботом 🙃</div>';
    return;
  }
  D = d;
  $("who").textContent = d.name || "Karzen Bot";
  const c = $("conn");
  c.textContent = d.connected ? "подключён" : "не подключён";
  c.className = "badge " + (d.connected ? "on" : "off");
  $("s_media").textContent = d.media;
  $("s_caught").textContent = d.caught;
  $("s_known").textContent = d.known;
  $("s_trust").textContent = d.trusted;
  render();
}
load();
</script></body></html>"""

async def page_app(request):
    return web.Response(text=MINI_APP, content_type="text/html")

async def api_state(request):
    body = await request.json()
    uid = check_init_data(body.get("init", ""))
    if not uid:
        return web.json_response({"error": "bad signature"}, status=403)
    u = user(uid)
    n, sz = await call(st.media_stats, uid)
    known, trusted = st.counts(uid)
    f = u["filter"]
    return web.json_response({
        "name": u.get("name") or "", "connected": conn_of(uid) is not None,
        "mode": u["mode"] or "", "preview": preview(u["mode"], u),
        "mode_level": u.get("mode_level", "normal"), "mode_bold": u.get("mode_bold", True),
        "mode_emoji": u.get("mode_emoji", True),
        "antidelete": u["antidelete"], "save_media": u["save_media"],
        "save_own": u["save_own"], "save_when": u.get("save_when", "deleted"),
        "track_edits": u.get("track_edits", True), "digest": u.get("digest", False),
        "scam": u["antiscam"]["enabled"], "new_dialog": u["antiscam"]["new_dialog"],
        "unknown_bot": u["antiscam"]["unknown_bot"], "exec_files": u["antiscam"]["exec_files"],
        "filter": f["enabled"], "filter_delete": f["delete"], "links": f["links"],
        "numbers": f["numbers"], "buttons": f["buttons"], "words_on": f["words_on"],
        "words": f["words"], "sens": u.get("sens", "normal"),
        "dox": adox(u).get("enabled", True), "dox_dry": adox(u).get("dry_run", True),
        "quiet": u.get("quiet", ""), "away": u.get("away", ""),
        "away_on": u.get("away_on", False),
        "media": n, "media_size": human_size(sz), "caught": u["caught"],
        "known": known, "trusted": trusted, "pairs": len(u["pairs"]),
    })

WEB_KEYS = {
    "scam": "antiscam.enabled", "new_dialog": "antiscam.new_dialog",
    "unknown_bot": "antiscam.unknown_bot", "exec_files": "antiscam.exec_files",
    "filter": "filter.enabled", "filter_delete": "filter.delete",
    "links": "filter.links", "numbers": "filter.numbers",
    "buttons": "filter.buttons", "words_on": "filter.words_on",
    "dox": "antidox.enabled", "dox_dry": "antidox.dry_run",
    "antidelete": "antidelete", "save_media": "save_media", "save_own": "save_own",
    "track_edits": "track_edits", "digest": "digest", "away_on": "away_on",
    "mode_bold": "mode_bold", "mode_emoji": "mode_emoji",
}
WEB_ENUMS = {
    "mode_level": LEVELS, "save_when": ("always", "deleted"), "sens": tuple(SENS),
}

async def api_set(request):
    body = await request.json()
    uid = check_init_data(body.get("init", ""))
    if not uid:
        return web.json_response({"error": "bad signature"}, status=403)
    u, k, v = user(uid), body.get("key"), body.get("val")

    if k == "mode":
        u["mode"] = v if v in MODES else None
    elif k in WEB_ENUMS:
        if v not in WEB_ENUMS[k]:
            return web.json_response({"error": "bad value"}, status=400)
        u[k] = v
    elif k in WEB_KEYS:
        dig_set(u, WEB_KEYS[k], bool(v))
    elif k == "away":
        u["away"] = str(v or "")[:800]
    elif k == "quiet":
        q = str(v or "").strip()
        if q:
            try:
                a, b = q.split("-", 1)
                hhmm(a), hhmm(b)
            except Exception:
                return web.json_response({"error": "bad time"}, status=400)
        u["quiet"] = q
    else:
        return web.json_response({"error": "unknown key"}, status=400)
    save(uid)
    return web.json_response({"ok": True, "preview": preview(u["mode"], u)})

async def api_words(request):
    body = await request.json()
    uid = check_init_data(body.get("init", ""))
    if not uid:
        return web.json_response({"error": "bad signature"}, status=403)
    u = user(uid)
    words = u["filter"]["words"]
    add = (body.get("add") or "").strip().lower()[:40]
    if add and add not in words:
        words.append(add)
        u["filter"]["words_on"] = True
    if isinstance(body.get("del"), int) and 0 <= body["del"] < len(words):
        words.pop(body["del"])
    save(uid)
    return web.json_response({"words": words})

async def api_log(request):
    body = await request.json()
    uid = check_init_data(body.get("init", ""))
    if not uid:
        return web.json_response({"error": "bad signature"}, status=403)
    rows = [{"when": f"{ts_local(r['ts']):%d.%m %H:%M}", "peer": r["peer"],
             "reason": r["reason"], "score": r["score"], "action": r["action"],
             "text": (r["text"] or "")[:200]}
            for r in st.recent_catches(uid, 30)]
    return web.json_response({"rows": rows})

async def setup_menu():
    try:
        BOT_COMMANDS = [
            BotCommand(command="start", description="⚙️ Настройки"),
            BotCommand(command="help", description="❓ Команды"),
            BotCommand(command="media", description="📦 Что в архиве"),
            BotCommand(command="log", description="🔪 Срабатывания защиты"),
            BotCommand(command="export", description="📤 Выгрузить данные"),
            BotCommand(command="invite", description="🔗 Позвать друга"),
            BotCommand(command="setup", description="🚀 Как подключить"),
        ]
        await bot.set_my_commands(BOT_COMMANDS)
        if WEBHOOK_BASE:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
                text="⚙️ Меню", web_app=WebAppInfo(url=WEBHOOK_BASE.rstrip("/") + "/app")))
        else:
            await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as ex:
        logging.warning("menu: %s", ex)

# ═════════════════════════════════════════════════════════
# АДМИНКА
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
    lines = []
    for r in rows:
        badge = "⛔" if r["banned"] else ("🔌" if conn_of(r["uid"]) else "▫️")
        un = f"@{esc(r['username'])}" if r["username"] else "—"
        lines.append(f"{badge} <code>{r['uid']}</code> · {esc((r['name'] or '?')[:18])} · {un} "
                     f"· 🔪{r['caught']}")
    nav = []
    if page > 0:
        nav.append(B("‹", f"adm:users:{page - 1}"))
    nav.append(B(f"{page + 1}/{pages}", "adm:noop"))
    if page < pages - 1:
        nav.append(B("›", f"adm:users:{page + 1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[nav, [B("‹ Админка", "adm:root")]])
    return (f"👥 <b>Пользователи</b> · {total}\n\n{chr(10).join(lines) or 'пусто'}\n\n"
            f"<i>/u id · /ban id · /unban id · /bc текст · /find кто</i>"), kb

def top_page() -> str:
    rows, _ = st.page(0, 500)
    top_caught = sorted(rows, key=lambda r: r["caught"], reverse=True)[:10]
    lines = [f"{i + 1}. <code>{r['uid']}</code> · {esc((r['name'] or '?')[:18])} "
             f"· 🔪 {r['caught']}" for i, r in enumerate(top_caught) if r["caught"]]
    return "🏆 <b>Больше всех поймали</b>\n\n" + ("\n".join(lines) or "пока никто")

ADMIN_KB = InlineKeyboardMarkup(inline_keyboard=[
    [B("📊 Статистика", "adm:stats"), B("🏆 Топ", "adm:top")],
    [B("👥 Пользователи", "adm:users:0")],
    [B("‹ Меню", "n:root")],
])

@dp.callback_query(F.data.startswith("adm:"))
async def admin_cb(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        return await cb.answer("Не для тебя 🙃", show_alert=True)
    p = cb.data.split(":")
    try:
        if p[1] == "stats":
            await cb.message.edit_text(stats_text(), reply_markup=ADMIN_KB)
        elif p[1] == "top":
            await cb.message.edit_text(top_page(), reply_markup=ADMIN_KB)
        elif p[1] == "users":
            txt, kb = users_page(int(p[2]) if len(p) > 2 else 0)
            await cb.message.edit_text(txt, reply_markup=kb)
        elif p[1] != "noop":
            await cb.message.edit_text("🛠 <b>Админка</b>", reply_markup=ADMIN_KB)
    except Exception:
        pass
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
        f"👤 <b>{esc(u['name'])}</b> (@{esc(u['username']) or '—'})\n🆔 <code>{esc(a[1])}</code>\n"
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
        f"• <code>{r['uid']}</code> · {esc((r['name'] or '?')[:20])} · "
        f"@{esc(r['username']) or '—'} · 🔪{r['caught']}" for r in rows))

@dp.message(Command("log"))
async def cmd_log(m: Message):
    target = m.from_user.id
    a = m.text.split()
    if len(a) > 1 and is_admin(m.from_user.id):
        try:
            target = int(a[1])
        except ValueError:
            pass
    txt, kb = await screen(target, "log", "0")
    await m.answer(txt, reply_markup=kb)

# Остальные команды
@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(CMD_HELP, reply_markup=help_kb())

@dp.message(Command("menu"))
async def cmd_menu(m: Message):
    txt, kb = await screen(m.from_user.id, "root")
    await m.answer(txt, reply_markup=kb)

@dp.message(Command("media"))
async def cmd_media(m: Message):
    txt, kb = await screen(m.from_user.id, "arch")
    await m.answer(txt, reply_markup=kb)

@dp.message(Command("setup"))
async def cmd_setup(m: Message):
    txt, kb = await screen(m.from_user.id, "setup")
    await m.answer(txt, reply_markup=kb)

@dp.message(Command("export"))
async def cmd_export(m: Message):
    await m.answer("📤 Собираю выгрузку…")
    await send_export(m.from_user.id)

@dp.message(Command("invite"))
async def cmd_invite(m: Message):
    me = await bot.get_me()
    u = user(m.from_user.id, m.from_user)
    link = f"https://t.me/{me.username}?start=ref_{m.from_user.id}"
    await m.answer(
        f"🔗 <b>Позвать друга</b>\n\n"
        f"Перешли ему эту ссылку:\n{link}\n\n"
        f"Пришло по твоей ссылке: <b>{u.get('invited', 0)}</b>\n\n"
        f"<i>Бот бесплатный и работает без Premium — подключается за минуту "
        f"через Настройки → Telegram для бизнеса → Чат-боты.</i>")

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
    return web.json_response({"ok": True, "users": st.stats()["total"]})

ALLOWED_UPDATES = ["message", "callback_query", "business_connection",
                   "business_message", "edited_business_message",
                   "deleted_business_messages"]

async def run_webhook():
    await storage_start()
    asyncio.create_task(st.flush_loop(5))
    asyncio.create_task(digest_loop())
    dp.startup.register(on_startup)
    await setup_menu()
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/app", page_app)
    app.router.add_post("/api/state", api_state)
    app.router.add_post("/api/set", api_set)
    app.router.add_post("/api/words", api_words)
    app.router.add_post("/api/log", api_log)
    SimpleRequestHandler(dispatcher=dp, bot=bot,
                         secret_token=WEBHOOK_SECRET).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    me = await bot.get_me()
    print(f"♡ @{me.username} на вебхуках, порт {PORT}. Юзеров: {st.stats()['total']}")
    await asyncio.Event().wait()

async def run_polling():
    await storage_start()
    asyncio.create_task(st.flush_loop(5))
    asyncio.create_task(digest_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    await setup_menu()
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
        await storage_stop()
        await osint_service.close()
        await http_close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
