import os
import asyncio
import aiohttp
from aiohttp import web
import json
import logging
import hashlib
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bg_status_bot")

# ==== CONFIG ====
TELEGRAM_TOKEN = os.environ.get("8130372610:AAEpWmaVAR7-5q42K6fD7NU0rBEuvDKeCYI")
TELEGRAM_CHAT_ID = os.environ.get("6094061742")

# твои два заявления
CLAIMS = [
    {"num": "23859/2023", "pin": "339020"},
    {"num": "23860/2023", "pin": "265854"},
]

# 3 раза в сутки = каждые 8 часов
CHECK_INTERVAL = 8 * 60 * 60
SAVE_PATH = "/tmp/status_cache.json"

# ==== HELPERS ====
def send_telegram(text: str):
    """Отправка уведомления в Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_TOKEN or CHAT_ID not set")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID.strip(), "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if not r.ok:
            logger.warning("Telegram error: %s", r.text)
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
    except Exception as e:
        logger.warning("Save state failed: %s", e)

# ==== CORE ====
async def fetch_status(session, claim):
    """Проверка статуса заявления на сайте Минюста Болгарии"""
    num = claim["num"]
    pin = claim["pin"]

    try:
        url = "https://publicbg.mjs.bg/BG/Web/RegisterPublic"
        data = {
            "number": num.split("/")[0],
            "year": num.split("/")[1],
            "pin": pin
        }

        async with session.post(url, data=data, timeout=30) as resp:
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ").lower()

        # поиск ключевых статусов
        if "задължително съгласуване" in text:
            return "В процедура по задължително съгласуване"
        elif "предложена за издаване на указ" in text:
            return "Предложена за указ"
        elif "издаден указ" in text:
            return "Издаден указ"
        else:
            # если не нашли — хэшируем текст, чтобы ловить изменения
            return "HASH_" + hashlib.md5(text.encode("utf-8")).hexdigest()

    except Exception as e:
        logger.warning("Fetch failed for %s: %s", num, e)
        return f"error:{e}"

async def check_loop(app):
    state = load_state()
    async with aiohttp.ClientSession() as session:
        while True:
            logger.info("🔍 Checking statuses...")
            try:
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
                    else:
                        logger.info("✅ No change for %s (%s)", num, status)
            except Exception as e:
                logger.warning("Loop error: %s", e)

            logger.info(f"⏳ Спим {CHECK_INTERVAL // 3600} часов...\n")
            await asyncio.sleep(CHECK_INTERVAL)

# ==== WEB SERVER ====
async def health(request):
    return web.Response(text="ok")

async def start_bg_task(app):
    app["task"] = asyncio.create_task(check_loop(app))

async def cleanup_bg_task(app):
    app["task"].cancel()
    try:
        await app["task"]
    except asyncio.CancelledError:
        pass

def create_app():
    app = web.Application()
    app.router.add_get("/health", health)
    app.on_startup.append(start_bg_task)
    app.on_cleanup.append(cleanup_bg_task)
    return app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    web.run_app(create_app(), host="0.0.0.0", port=port)