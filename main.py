import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Event loop нужно создать ДО импорта pyrogram
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from pyrogram import Client
from pyrogram.types import Message
from openai import OpenAI

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

app = Client(
    "mr_shelby_business_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=TELEGRAM_BOT_TOKEN,
    in_memory=True
)

SYSTEM_PROMPT = (
    "Отвечай от первого лица как живой человек, владелец аккаунта. "
    "Пиши просто, понятным разговорным языком, без канцеляризмов и шаблонов бота. "
    "Отвечай кратко, как в обычной переписке, не признавайся, что ты ИИ или бот."
)


# Мини-сервер, чтобы Render не убивал Web Service из-за отсутствия открытого порта
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


@app.on_business_message()
async def handle_business_message(client: Client, message: Message):
    if message.from_user and message.from_user.is_self:
        return

    user_text = message.text or ""
    if not user_text:
        return

    try:
        response = await asyncio.to_thread(
            openai_client.chat.completions.create,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text}
            ],
            max_tokens=250,
            temperature=0.7
        )

        reply_text = response.choices[0].message.content.strip()
        await message.reply_text(reply_text)

    except Exception as e:
        print(f"Ошибка при обработке сообщения: {e}")


async def main():
    async with app:
        print("Бот успешно запущен!")
        await asyncio.Event().wait()


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    loop.run_until_complete(main())
