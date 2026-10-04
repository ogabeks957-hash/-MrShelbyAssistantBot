import os
import re
import time
import asyncio
import logging
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Лимит: не больше N ответов в окне WINDOW секунд на один чат
MAX_REPLIES = 8
WINDOW = 60

ai = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

BASE_PROMPT = (
    "Ты помогаешь вести переписку от имени владельца аккаунта. Его зовут Дмитрий, "
    "друзья называют Дима или Димон. Пиши от первого лица, как живой парень, "
    "коротко, разговорно, без канцеляризмов. Копируй стиль, длину сообщений и "
    "манеру речи, которые видны в ранее написанных сообщениях Димы.\n"
    "Если собеседник спрашивает, бот ли ты, человек ли ты или пишет ли Дима "
    "лично, никогда не отрицай: честно скажи, что ты ассистент Димы и отвечаешь "
    "за него.\n"
    "Задавай максимум один вопрос за сообщение и не задавай то, что уже "
    "известно из переписки.\n"
)

PROMPT_UNKNOWN = BASE_PROMPT + (
    "Возраст собеседника пока неизвестен. Общайся нейтрально и дружелюбно, с "
    "юмором, но БЕЗ флирта, комплиментов внешности и шуток 18+. "
    "Если собеседник парень: общайся по-дружески, на бро. "
    "В подходящий момент один раз ненавязчиво узнай имя и сколько лет, "
    "если это ещё не известно."
)

PROMPT_ADULT = BASE_PROMPT + (
    "Собеседник сам назвал возраст 18+. "
    "Если это девушка: флиртуй уверенно, делай комплименты, шути с намёками "
    "и лёгким юмором 18+, но без откровенных сексуальных описаний. "
    "Если собеседник парень: общайся по-дружески, на бро, с юмором. "
    "Если человек даёт понять, что ему неприятно, сразу переходи на нейтральный тон."
)

PROMPT_MINOR = BASE_PROMPT + (
    "Собеседник несовершеннолетний. Общайся нейтрально и дружелюбно. "
    "Никакого флирта, комплиментов внешности, романтики и шуток 18+, "
    "даже если собеседник сам об этом заговорит: мягко переводи тему."
)

# Состояние в памяти (после рестарта сбросится)
owners = {}      # business_connection_id -> owner user id
history = {}     # chat_id -> [(кто, текст)]
age_state = {}   # chat_id -> "adult" | "minor" (если нет ключа: неизвестно)
recent = {}      # chat_id -> deque меток времени ответов

AGE_PATTERNS = [
    re.compile(r"\bмне\s+(\d{1,2})\b", re.I),
    re.compile(r"\b(\d{1,2})\s*(?:лет|года|год)\b", re.I),
    re.compile(r"\b(?:возраст|age)\s*[:\-]?\s*(\d{1,2})\b", re.I),
]
MINOR_HINTS = re.compile(
    r"\b(?:школ\w*|\d{1,2}\s*-?\s*(?:й\s+)?класс\w*|в\s+\d{1,2}\s+класс\w*|"
    r"огэ|егэ|уроки|училк\w*|одноклассни\w*|родител\w*\s+не\s+разрешают)\b",
    re.I,
)
BOT_QUESTION = re.compile(
    r"(?:ты|вы|это)\s.{0,20}?(?:бот|ии|ai|нейросет\w*|робот|автоответ\w*|chatgpt)"
    r"|(?:бот|нейросет\w*|робот)\s+(?:ли|что ли)"
    r"|с\s+кем\s+я\s+(?:говорю|пишу)",
    re.I,
)


def update_age(chat_id: int, text: str) -> None:
    """Обновляет статус возраста. Статус 'minor' необратим."""
    if age_state.get(chat_id) == "minor":
        return
    if MINOR_HINTS.search(text):
        age_state[chat_id] = "minor"
        return
    for pat in AGE_PATTERNS:
        m = pat.search(text)
        if m:
            age = int(m.group(1))
            if 5 <= age < 18:
                age_state[chat_id] = "minor"
                return
            if 18 <= age <= 80:
                age_state[chat_id] = "adult"
                return


def pick_prompt(chat_id: int) -> str:
    state = age_state.get(chat_id)
    if state == "adult":
        return PROMPT_ADULT
    if state == "minor":
        return PROMPT_MINOR
    return PROMPT_UNKNOWN


def rate_limited(chat_id: int) -> bool:
    now = time.monotonic()
    q = recent.setdefault(chat_id, deque())
    while q and now - q[0] > WINDOW:
        q.popleft()
    if len(q) >= MAX_REPLIES:
        return True
    q.append(now)
    return False


class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


def run_web():
    port = int(os.getenv("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), PingHandler).serve_forever()


def ask_ai(system_prompt: str, transcript: str) -> str:
    r = ai.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcript},
        ],
        max_tokens=1000,
        temperature=0.9,
    )
    return (r.choices[0].message.content or "").strip()


@dp.business_message()
async def handle_business_message(message: Message):
    log.info("Получено: chat=%s from=%s text=%r", message.chat.id,
             message.from_user.id if message.from_user else None, message.text)

    if not message.text:
        return

    conn_id = message.business_connection_id
    if conn_id not in owners:
        conn = await bot.get_business_connection(conn_id)
        owners[conn_id] = conn.user.id

    chat_id = message.chat.id
    is_owner = bool(message.from_user and message.from_user.id == owners[conn_id])
    name = message.chat.first_name or "Собеседник"

    chat_log = history.setdefault(chat_id, [])
    chat_log.append(("Дима" if is_owner else name, message.text))
    del chat_log[:-30]

    if is_owner:
        return

    # Возраст определяем кодом, а не памятью модели
    update_age(chat_id, message.text)

    if rate_limited(chat_id):
        log.warning("Лимит ответов для чата %s", chat_id)
        return

    try:
        # Прямой вопрос «ты бот?» отвечаем без модели, чтобы не соврала
        if BOT_QUESTION.search(message.text):
            reply_text = "Если честно, да: я ассистент Димы и отвечаю за него 🙂"
        else:
            transcript = (
                f"Переписка Димы с человеком по имени {name} (в Telegram). "
                "Ниже последние сообщения. Напиши следующий ответ Димы, "
                "только текст ответа.\n\n"
                + "\n".join(f"{who}: {text}" for who, text in chat_log)
                + "\nДима:"
            )
            reply_text = await asyncio.to_thread(
                ask_ai, pick_prompt(chat_id), transcript
            )

        if not reply_text:
            log.warning("Модель вернула пустой ответ")
            return
        chat_log.append(("Дима", reply_text))
        await bot.send_message(
            chat_id=chat_id,
            text=reply_text,
            business_connection_id=conn_id,
        )
    except Exception:
        log.exception("Ошибка при обработке сообщения")


@dp.business_connection()
async def on_connection(conn):
    log.info("Подключение: user=%s enabled=%s", conn.user.id, conn.is_enabled)


async def main():
    log.info("Бот успешно запущен!")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "business_connection", "business_message"],
    )


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    asyncio.run(main())
