import os
import asyncio
import aiohttp
from aiohttp import web
import json
import logging
import hashlib
import requests
from bs4 import BeautifulSoup
from threading import Thread
from time import sleep

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bg_status_bot")

# ==== CONFIG ====
TELEGRAM_TOKEN = os.environ.get("8130372610:AAEpWmaVAR7-5q42K6fD7NU0rBEuvDKeCYI")
TELEGRAM_CHAT_ID = os.environ.get("6094061742")

CLAIMS = [
    {"num": "23859/2023", "pin": "339020"},
    {"num": "23860/2023", "pin": "265854"},
]

CHECK_INTERVAL = 8 * 60 * 60  # 8 часов
SAVE_PATH = "/tmp/status_cache.json"


# ==== HELPERS ====
def send_telegram(text: str):
    """Отправка сообщения в Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID.strip(), "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


def load_state():
    try:
        with open(SAVE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(SAVE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ==== CORE ====
async def fetch_status(session, claim):
    """Запрос статуса с сайта Минюста Болгарии (исправленный URL)"""
    num = claim["num"]
    pin = claim["pin"]

    try:
        url = "https://publicbg.mjs.bg/BgInfo/BG/Web/RegisterPublic"
        data = {
            "number": num.split("/")[0],
            "year": num.split("/")[1],
            "pin": pin
        }
        async with session.post(url, data=data, timeout=30) as resp:
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ").lower()

        if "задължително съгласуване" in text:
            return "В процедура по задължително съгласуване"
        elif "предложена за издаване на указ" in text:
            return "Предложена за указ"
        elif "издаден указ" in text:
            return "Издаден указ"
        else:
            # fallback для любых новых формулировок
            return "HASH_" + hashlib.md5(text.encode("utf-8")).hexdigest()

    except Exception as e:
        logger.warning("Fetch failed for %s: %s", num, e)
        return f"error:{e}"

async def check_all(app, manual=False):
    """Проверка всех заявлений"""
    state = load_state()
    async with aiohttp.ClientSession() as session:
        text_out = []
        for claim in CLAIMS:
            num = claim["num"]
            status = await fetch_status(session, claim)
            prev = state.get(num)
            if prev != status:
                msg = (
                    f"⚡️ *Изменение статуса заявления {num}*\n\n"
                    f"Было: `{prev}`\n"
                    f"Стало: `{status}`"
                )
                send_telegram(msg)
                state[num] = status
                save_state(state)
            text_out.append(f"{num} — {status}")
        if manual:
            send_telegram("📋 *Результаты ручной проверки:*\n\n" + "\n".join(text_out))
    save_state(state)


async def periodic_checker(app):
    """Автоматическая проверка каждые 8 часов"""
    await asyncio.sleep(5)
    send_telegram("✅ Бот запущен и работает. Проверка каждые 8 часов.")
    while True:
        try:
            await check_all(app)
        except Exception as e:
            logger.warning("Loop error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)


# ==== TELEGRAM COMMAND HANDLER ====
def telegram_listener():
    """Постоянный опрос Telegram, чтобы ловить команду /check"""
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 30, "offset": offset}
            r = requests.get(url, params=params, timeout=35)
            data = r.json()
            if "result" in data:
                for upd in data["result"]:
                    offset = upd["update_id"] + 1
                    msg = upd.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id"))
                    text = msg.get("text", "").strip().lower()
                    if chat_id == TELEGRAM_CHAT_ID and text == "/check":
                        loop = asyncio.get_event_loop()
                        loop.create_task(check_all(None, manual=True))
        except Exception as e:
            logger.warning("Telegram listener error: %s", e)
        sleep(5)


# ==== WEB SERVER ====
async def health(request):
    return web.Response(text="ok")

async def start_bg(app):
    app["task"] = asyncio.create_task(periodic_checker(app))
    Thread(target=telegram_listener, daemon=True).start()

async def cleanup_bg(app):
    app["task"].cancel()
    try:
        await app["task"]
    except asyncio.CancelledError:
        pass

def create_app():
    app = web.Application()
    app.router.add_get("/health", health)
    app.on_startup.append(start_bg)
    app.on_cleanup.append(cleanup_bg)
    return app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    web.run_app(create_app(), host="0.0.0.0", port=port)
