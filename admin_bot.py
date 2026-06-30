import logging
import os

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.utils import executor

logging.basicConfig(level=logging.INFO)

ADMIN_BOT_TOKEN  = os.environ["ADMIN_BOT_TOKEN"]
ADMIN_ID         = int(os.environ["ADMIN_ID"])
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
        text="Admin panelni ochish",
        web_app=WebAppInfo(url=ADMIN_WEBAPP_URL)
    ))
    await message.answer("VIP CLOUDE — Admin panel", reply_markup=kb)


# Bu fayl ikki holatda ishlatilishi mumkin:
# 1) `python admin_bot.py` orqali to'g'ridan-to'g'ri (mustaqil process,
#    main.py tomonidan subprocess.run orqali chaqiriladi)
# 2) `from admin_bot import admin_dp` orqali boshqa joydan import
#    qilinganda - bu holda quyidagi blok ishlamaydi, faqat admin_dp
#    obyekti ishlatiladi.
if __name__ == "__main__":
    logging.info("Admin bot mustaqil process sifatida ishga tushdi.")
    executor.start_polling(admin_dp, skip_updates=True)
