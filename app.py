import os
import time
import threading
import hashlib
import requests
from bs4 import BeautifulSoup

# ====== НАСТРОЙКИ ======
TELEGRAM_TOKEN = os.getenv("8130372610:AAEpWmaVAR7-5q42K6fD7NU0rBEuvDKeCYI") or "PUT_YOUR_TOKEN_HERE"
TELEGRAM_CHAT_ID = os.getenv("6094061742") or "PUT_CHAT_ID_HERE"

# твои заявления
CLAIMS = [
    {"num": "23859/2023", "pin": "339020"},
    {"num": "23860/2023", "pin": "265854"},
]

# проверка раз в 8 часов
CHECK_INTERVAL = 8 * 60 * 60
# файл чтобы помнить прошлые статусы
STATE_FILE = "status_cache.txt"

# правильный URL (как ты нашёл)
CHECK_URL = "https://publicbg.mjs.bg/BgInfo/BG/Web/RegisterPublic"


# ====== УТИЛИТЫ ======
def tg_send(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[tg] TOKEN or CHAT_ID not set")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
        if not r.ok:
            print("[tg] error:", r.text)
    except Exception as e:
        print("[tg] exception:", e)


def load_state():
    data = {}
    if not os.path.exists(STATE_FILE):
        return data
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            num, status = line.split("::", 1)
            data[num] = status
    return data


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        for k, v in state.items():
            f.write(f"{k}::{v}\n")


# ====== ЛОГИКА ПРОВЕРКИ ======
def fetch_status(num: str, pin: str) -> str:
    """запрашиваем статус у болгар"""
    try:
        number, year = num.split("/")
        resp = requests.post(
            CHECK_URL,
            data={"number": number, "year": year, "pin": pin},
            timeout=20,
        )
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ").lower()

        if "задължително съгласуване" in text:
            return "В процедура по задължително съгласуване"
        if "предложена за издаване на указ" in text:
            return "Предложена за указ"
        if "издаден указ" in text:
            return "Издаден указ"

        # если текст новый/другой
        return "HASH_" + hashlib.md5(text.encode("utf-8")).hexdigest()

    except Exception as e:
        return f"error:{e}"


def check_all(manual=False):
    """проверяем все заявления, сравниваем с прошлым состоянием"""
    print("[check] start (manual=" + str(manual) + ")")
    state = load_state()
    out_lines = []
    changed = False

    for claim in CLAIMS:
        num = claim["num"]
        pin = claim["pin"]
        status = fetch_status(num, pin)
        prev = state.get(num)

        out_lines.append(f"{num} — {status}")

        if prev != status:
            changed = True
            state[num] = status
            # уведомляем об изменении
            tg_send(
                f"⚡️ Статус заявления *{num}* изменился.\nБыло: `{prev}`\nСтало: `{status}`"
            )

    save_state(state)

    # если это ручная проверка — шлём результат даже без изменений
    if manual:
        tg_send("📋 Ручная проверка:\n" + "\n".join(out_lines))

    print("[check] done")


# ====== ПОТОК АВТОПРОВЕРКИ ======
def auto_checker():
    # при старте один раз проверим и скажем что живы
    tg_send("✅ Бот запущен на сервере. Буду проверять каждые 8 часов.")
    check_all(manual=False)
    while True:
        time.sleep(CHECK_INTERVAL)
        check_all(manual=False)


# ====== ПОТОК TELEGRAM POLLING ======
def telegram_poll():
    """
    простой опрос бота: если ты пишешь /check — он делает check_all(manual=True)
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[poll] no token/chat_id, skip polling")
        return

    print("[poll] telegram polling started")
    offset = None
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
                msg = upd.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id", ""))
                text = (msg.get("text") or "").strip()

                # реагируем только на твой чат
                if chat_id == str(TELEGRAM_CHAT_ID):
                    if text.lower() == "/check":
                        check_all(manual=True)
                    elif text.lower() in ("/start", "привет", "hi"):
                        tg_send("👋 Я тут. Напиши /check чтобы проверить сейчас.")
        except Exception as e:
            print("[poll] error:", e)
            time.sleep(5)


if __name__ == "__main__":
    # запускаем два параллельных потока
    t1 = threading.Thread(target=auto_checker, daemon=True)
    t1.start()

    t2 = threading.Thread(target=telegram_poll, daemon=True)
    t2.start()

    # чтобы главный поток не завершился
    while True:
        time.sleep(60)
