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
    "Отвечай от первого лица как живой человек, владелец аккаунта. "
    "Пиши просто, понятным разговорным языком, без канцеляризмов и шаблонов бота. "
    "Отвечай кратко, как в обычной переписке, не признавайся, что ты ИИ или бот."
)

owners = {}


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


def ask_gemini(text: str) -> str:
    response = gemini.models.generate_content(
        model=MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=300,
            temperature=0.7,
        ),
    )
    return (response.text or "").strip()


@dp.business_message()
async def handle_business_message(message: Message):
    print("Получено сообщение:", message.text)
    if not message.text:
        return

    conn_id = message.business_connection_id
    if conn_id not in owners:
        conn = await bot.get_business_connection(conn_id)
        owners[conn_id] = conn.user.id

    # не отвечаем на сообщения самого владельца
    if message.from_user and message.from_user.id == owners[conn_id]:
        return

    try:
        reply_text = await asyncio.to_thread(ask_gemini, message.text)
        if not reply_text:
            return
        await bot.send_message(
            chat_id=message.chat.id,
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
