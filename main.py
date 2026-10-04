import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from openai import OpenAI

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai_client = OpenAI(api_key=OPENAI_API_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

SYSTEM_PROMPT = (
    "Отвечай от первого лица как живой человек, владелец аккаунта. "
    "Пиши просто, понятным разговорным языком, без канцеляризмов и шаблонов бота. "
    "Отвечай кратко, как в обычной переписке, не признавайся, что ты ИИ или бот."
)


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


@dp.business_message()
async def handle_business_message(message: Message):
    if not message.text:
        return
    # не отвечаем на собственные сообщения владельца
    if message.from_user and message.from_user.id == message.business_connection_id:
        return

    try:
        response = await asyncio.to_thread(
            openai_client.chat.completions.create,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message.text},
            ],
            max_tokens=250,
            temperature=0.7,
        )
        reply_text = response.choices[0].message.content.strip()
        await message.answer(reply_text)
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
