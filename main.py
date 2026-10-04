import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from google import genai
from google.genai import types

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = "gemini-2.5-flash"

gemini = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

SYSTEM_PROMPT = (
    "Ты отвечаешь в переписке от имени владельца аккаунта. Его зовут Дмитрий, "
    "друзья называют Дима или Димон. Пиши от первого лица, как живой парень, "
    "коротко, разговорно, без канцеляризмов. Копируй стиль, длину сообщений и "
    "манеру речи, которые видны в ранее написанных сообщениях Димы.\n\n"
    "Если собеседник девушка: флиртуй уверенно и чётко, делай комплименты, "
    "шути с намёками и лёгким пошлым юмором 18+, но без откровенных "
    "сексуальных описаний. Ненавязчиво выясняй, как её зовут, сколько ей лет, "
    "чем занимается. Задавай максимум один вопрос за сообщение и не задавай "
    "то, что уже известно из переписки.\n"
    "Если собеседник парень: общайся по-дружески, на бро, с юмором.\n"
    "Если собеседнику меньше 18 лет или это похоже на школьника, не флиртуй "
    "и не шути про 18+: общайся нейтрально и дружелюбно.\n"
    "Если пол неясен, сначала общайся нейтрально и узнай имя."
)

owners = {}
history = {}  # chat_id -> список (кто, текст)


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


def ask_gemini(transcript: str) -> str:
    response = gemini.models.generate_content(
        model=MODEL,
        contents=transcript,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=400,
            temperature=0.9,
        ),
    )
    return (response.text or "").strip()


@dp.business_message()
async def handle_business_message(message: Message):
    if not message.text:
        return

    conn_id = message.business_connection_id
    if conn_id not in owners:
        conn = await bot.get_business_connection(conn_id)
        owners[conn_id] = conn.user.id

    chat_id = message.chat.id
    is_owner = bool(message.from_user and message.from_user.id == owners[conn_id])
    name = message.chat.first_name or "Собеседник"

    log = history.setdefault(chat_id, [])
    log.append(("Дима" if is_owner else name, message.text))
    del log[:-30]  # помним последние 30 сообщений

    if is_owner:
        return

    transcript = (
        f"Переписка Димы с человеком по имени {name} (в Telegram). "
        "Ниже последние сообщения. Напиши следующий ответ Димы, "
        "только текст ответа.\n\n"
        + "\n".join(f"{who}: {text}" for who, text in log)
        + "\nДима:"
    )

    try:
        reply_text = await asyncio.to_thread(ask_gemini, transcript)
        if not reply_text:
            return
        log.append(("Дима", reply_text))
        await bot.send_message(
            chat_id=chat_id,
            text=reply_text,
            business_connection_id=conn_id,
        )
    except Exception as e:
        print(f"Ошибка при обработке сообщения: {e}")


async def main():
    print("Бот успешно запущен!")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "business_connection", "business_message"],
    )


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    asyncio.run(main())
