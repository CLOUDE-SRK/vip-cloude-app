import logging
import os

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

logging.basicConfig(level=logging.INFO)

ADMIN_BOT_TOKEN = os.environ["ADMIN_BOT_TOKEN"]
ADMIN_ID        = int(os.environ["ADMIN_ID"])
ADMIN_WEBAPP_URL = os.environ["ADMIN_WEBAPP_URL"]

admin_bot = Bot(token=ADMIN_BOT_TOKEN)
admin_dp  = Dispatcher(admin_bot)


@admin_dp.message_handler(commands=["start"])
async def admin_cmd_start(message: types.Message):
    # Faqat ADMIN_ID ushbu botdan foydalana oladi - boshqa hech kim emas.
    if message.from_user.id != ADMIN_ID:
        await message.answer("🚫 Bu bot faqat administratorlar uchun.")
        return

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(
        text="🛠 Admin panelni ochish",
        web_app=WebAppInfo(url=ADMIN_WEBAPP_URL)
    ))
    await message.answer("VIP CLOUDE — Admin panel", reply_markup=kb)
