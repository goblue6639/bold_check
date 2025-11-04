import os
import time
import hashlib
import logging
import threading
import requests
from bs4 import BeautifulSoup

# ====== Настройки ======
TELEGRAM_TOKEN = os.getenv("8130372610:AAEpWmaVAR7-5q42K6fD7NU0rBEuvDKeCYI")
TELEGRAM_CHAT_ID = os.getenv("6094061742")

# Заявки: входящий номер (“num”) и PIN код (“pin”)
CLAIMS = [
    {"num": "23859/2023", "pin": "339020"},
    {"num": "23860/2023", "pin": "265854"},
]

CHECK_INTERVAL = 8 * 60 * 60  # 8 часов
STATE_FILE = "status_cache.txt"

BASE_URL = "https://publicbg.mjs.bg/BgInfo"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bold_check_bot")

# ====== Telegram отправка ======
def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram token or chat id not set")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if not resp.ok:
            logger.error(f"[tg] error: {resp.text}")
        else:
            logger.info(f"[tg] sent message")
    except Exception as e:
        logger.error(f"[tg] exception: {e}")

# ====== Состояния ======
def load_state():
    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("::", 1)
                if len(parts) == 2:
                    state[parts[0]] = parts[1]
    return state

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        for num, val in state.items():
            f.write(f"{num}::{val}\n")

# ====== Логика парсинга ======
def fetch_status(num, pin):
    """
    Запрос к сайту publicbg для номера и PIN, возвращает текст статуса.
    """
    try:
        # Разделяем номер на число/год
        parts = num.split("/")
        if len(parts) != 2:
            return "Неверный номер"
        number = parts[0]
        year = parts[1]
        
        # Отправляем POST запрос
        resp = requests.post(
            BASE_URL,
            data={
                "number": number,
                "year": year,
                "pin": pin
            },
            timeout=20
        )
        resp.raise_for_status()
        html = resp.text
        
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ").strip().lower()
        
        # Проверим несколько ключевых фраз
        if "задължително съгласуване" in text:
            return "В процедура по задължително съгласуване"
        if "издаден указ" in text:
            return "Издаден указ"
        if "предложена за издаване на указ" in text:
            return "Предложена за указ"
        
        # Если ничего из стандартных фраз
        # Возвращаем хэш текста, чтобы отличать изменения
        h = hashlib.md5(text.encode("utf-8")).hexdigest()
        return "UNKNOWN_" + h

    except Exception as e:
        return f"error:{e}"

def check_all(manual=False):
    logger.info(f"[check] start (manual={manual})")
    state = load_state()
    lines = []
    changed = False

    for claim in CLAIMS:
        num = claim["num"]
        pin = claim["pin"]
        status = fetch_status(num, pin)
        lines.append(f"{num} — {status}")
        
        prev = state.get(num)
        if prev != status:
            changed = True
            state[num] = status
            send_telegram(f"⚡ *{num}* — статус изменился:\nБыло: `{prev}`\nСтало: `{status}`")

    save_state(state)
    
    if manual:
        send_telegram("📋 Ручная проверка:\n" + "\n".join(lines))
    logger.info("[check] done")

# ====== Функции потоков ======
def auto_loop():
    send_telegram("✅ Бот запущен и готов к проверкам!")
    check_all(manual=False)
    while True:
        time.sleep(CHECK_INTERVAL)
        check_all(manual=False)

def telegram_poll():
    offset = None
    send_telegram("👋 Напиши /check чтобы получить статус прямо сейчас.")
    logger.info("[poll] telegram polling started")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
            r = requests.get(url, params=params, timeout=35)
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                chat = msg.get("chat", {})
                chat_id = str(chat.get("id", ""))
                text = msg.get("text", "").strip().lower()
                if chat_id == str(TELEGRAM_CHAT_ID):
                    if text == "/check":
                        check_all(manual=True)
                    elif text in ("/start", "start", "привет", "hi"):
                        send_telegram("👋 Бот работает. Напиши /check для проверки.")
        except Exception as e:
            logger.error(f"[poll] error: {e}")
            time.sleep(5)

# ====== Запуск ======
if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Не указаны TELEGRAM_TOKEN или TELEGRAM_CHAT_ID!")
        exit(1)
    
    t1 = threading.Thread(target=auto_loop, daemon=True)
    t1.start()
    t2 = threading.Thread(target=telegram_poll, daemon=True)
    t2.start()
    while True:
        time.sleep(60)
