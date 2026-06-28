import asyncio
import logging
import os
import json
import hmac
import hashlib
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage

import database as db

logging.basicConfig(level=logging.INFO)

BOT_TOKEN   = os.environ["BOT_TOKEN"]
ADMIN_ID    = int(os.environ["ADMIN_ID"])       # Sizning Telegram ID
VIP_CHAT_ID = int(os.environ["VIP_CHAT_ID"])    # VIP kanal/guruh ID (masalan -100xxxxxxxxxx)
WEBAPP_URL  = os.environ["WEBAPP_URL"]          # GitHub Pages URL

TASK_REWARDS = {"tg": 5000, "ig": 3000, "yt": 3000}

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ─────────────────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    ref_by = None
    if len(args) > 1 and args[1].startswith("ref"):
        try:
            ref_by = int(args[1][3:])
            if ref_by == message.from_user.id:
                ref_by = None
        except ValueError:
            ref_by = None

    db.ensure_user(
        user_id    = message.from_user.id,
        username   = message.from_user.username,
        first_name = message.from_user.first_name,
        ref_by     = ref_by
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="CLOUDE VIP PREMIUM 💎",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])

    await message.answer(
        "CLOUDE VIP PREMIUM 💎",
        reply_markup=kb
    )

# ─────────────────────────────────────────────────────────
#  Webapp dan kelgan ma'lumotlar (sendData)
# ─────────────────────────────────────────────────────────
@router.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        return

    dtype = data.get("type")
    user_id = message.from_user.id

    # ── Screenshot / Topup so'rov ──────────────────────────
    if dtype == "topup_request":
        amount = data.get("amount", 0)
        method = data.get("method", "")

        await message.answer(
            "✅ So'rovingiz qabul qilindi!\n"
            "Admin tez orada tasdiqlaydi. Iltimos kuting. 🕐"
        )

        # Adminga xabar
        user = message.from_user
        admin_text = (
            f"💰 <b>Topup so'rov</b>\n\n"
            f"👤 Foydalanuvchi: <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"💵 Summa: <b>{amount:,} so'm</b>\n"
            f"🏦 Usul: {method}\n\n"
            f"⬇️ Skrinshot quyida keladi. Tasdiqlash uchun summani kiriting:"
        )
        sent = await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
        db.create_topup(user_id, amount, method, sent.message_id)

    # ── Screenshot rasm ────────────────────────────────────
    # (foydalanuvchi rasm yuborsa — quyida alohida handler bor)

    # ── UC buyurtma ────────────────────────────────────────
    elif dtype == "uc_purchase_request":
        pubg_id   = data.get("player_id", "")
        uc_amount = data.get("uc", 0)
        price     = data.get("price", 0)

        # Balansdan ayiramiz
        ok = db.deduct_balance(user_id, price)
        if not ok:
            await message.answer("❌ Balans yetarli emas. Iltimos hisobni to'ldiring.")
            return

        await message.answer(
            f"✅ UC buyurtmangiz qabul qilindi!\n"
            f"Admin tez orada {uc_amount:,} UC ni PUBG ID ga yuklaydi. 🎮"
        )

        user = message.from_user
        admin_text = (
            f"🎮 <b>UC Buyurtma</b>\n\n"
            f"👤 Foydalanuvchi: <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
            f"🆔 TG ID: <code>{user.id}</code>\n"
            f"🕹 PUBG ID: <code>{pubg_id}</code>\n"
            f"💎 UC: <b>{uc_amount:,} UC</b>\n"
            f"💵 Narxi: {price:,} so'm"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Tasdiqlash",
                callback_data=f"uc_done:{user_id}"
            )
        ]])
        sent = await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=kb)
        db.create_uc_order(user_id, pubg_id, uc_amount, price, sent.message_id)

    # ── VIP xarid ──────────────────────────────────────────
    elif dtype == "vip_purchase":
        vip_type = data.get("vip_type", "")
        price    = data.get("price", 0)

        ok = db.deduct_balance(user_id, price)
        if not ok:
            await message.answer("❌ Balans yetarli emas.")
            return

        db.create_vip_order(user_id, vip_type, price)

        # VIP kanalga qo'shamiz
        try:
            await bot.approve_chat_join_request(VIP_CHAT_ID, user_id)
        except Exception:
            pass
        try:
            await bot.unban_chat_member(VIP_CHAT_ID, user_id)
        except Exception:
            pass

        await message.answer(
            f"🎉 <b>{vip_type}</b> VIP muvaffaqiyatli faollashtirildi!\n"
            f"VIP kanalga kirish uchun: @CLOUDE_CHEATS",
            parse_mode="HTML"
        )

    # ── Task verify ────────────────────────────────────────
    elif dtype == "task_verify_request":
        task_key = data.get("task", "")
        if task_key == "tg":
            # Telegram kanalga a'zoligini avtomatik tekshirish
            try:
                member = await bot.get_chat_member("@CLOUDE_CHEATS", user_id)
                if member.status in ("member", "administrator", "creator"):
                    db.set_task_done(user_id, "tg")
                    reward = TASK_REWARDS["tg"]
                    db.add_balance(user_id, reward)
                    await message.answer(f"✅ A'zolik tasdiqlandi! +{reward:,} so'm qo'shildi.")
                else:
                    await message.answer("❌ Siz hali kanalga a'zo emassiz. Avval obuna bo'ling.")
            except Exception:
                await message.answer("⚠️ Tekshirishda xatolik. Biroz kutib qayta urining.")
        else:
            # IG / YT — admin tasdiqlashi kerak
            user = message.from_user
            admin_text = (
                f"📋 <b>Vazifa tekshirish so'rovi</b>\n\n"
                f"👤 <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
                f"🆔 ID: <code>{user.id}</code>\n"
                f"📌 Vazifa: <b>{task_key.upper()}</b>"
            )
            reward = TASK_REWARDS.get(task_key, 3000)
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Tasdiqlash",
                    callback_data=f"task_ok:{user_id}:{task_key}:{reward}"
                ),
                InlineKeyboardButton(
                    text="❌ Rad etish",
                    callback_data=f"task_no:{user_id}:{task_key}"
                )
            ]])
            await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=kb)
            await message.answer("⏳ So'rovingiz adminga yuborildi. Tez orada tekshiriladi.")

# ─────────────────────────────────────────────────────────
#  Foydalanuvchi screenshot yuborsa
# ─────────────────────────────────────────────────────────
@router.message(F.photo)
async def handle_photo(message: Message):
    user = message.from_user
    db.ensure_user(user.id, user.username, user.first_name)

    await message.answer(
        "✅ Skrinshot qabul qilindi!\n"
        "Admin tez orada so'rovingizni tasdiqlaydi. 🕐"
    )

    # Adminga forward + tugma
    caption = (
        f"📸 <b>To'lov screenshoti</b>\n\n"
        f"👤 <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        f"Tasdiqlash uchun summani kiriting (faqat raqam):\n"
        f"Masalan: <code>50000</code>"
    )
    await message.forward(ADMIN_ID)
    sent = await bot.send_message(ADMIN_ID, caption, parse_mode="HTML")
    # pending topup yaratamiz (summa noma'lum, admin yozadi)
    db.create_topup(user.id, 0, "screenshot", sent.message_id)

# ─────────────────────────────────────────────────────────
#  Admin: summani yozib tasdiqlash
# ─────────────────────────────────────────────────────────
@router.message(F.text & F.from_user.id == ADMIN_ID)
async def admin_text(message: Message):
    text = message.text.strip()

    # Faqat raqam bo'lsa — topup tasdiqlash
    if text.isdigit():
        amount = int(text)

        # Oxirgi pending topup ni topamiz
        import sqlite3
        conn = db.get_conn()
        row = conn.execute(
            "SELECT * FROM topup_requests WHERE status='pending' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if not row:
            await message.answer("❌ Pending so'rov topilmadi.")
            return

        db.add_balance(row["user_id"], amount)
        db.approve_topup(row["id"])

        await message.answer(f"✅ {row['user_id']} ga {amount:,} so'm qo'shildi.")
        await bot.send_message(
            row["user_id"],
            f"✅ Hisobingizga <b>{amount:,} so'm</b> qo'shildi! 🎉\n"
            f"Webappga qaytib xaridni davom ettiring.",
            parse_mode="HTML"
        )
    else:
        await message.answer("ℹ️ Tasdiqlash uchun faqat raqam yuboring (masalan: 50000)")

# ─────────────────────────────────────────────────────────
#  Callback: UC tasdiqlash
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("uc_done:"))
async def uc_done(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")

    user_id = int(call.data.split(":")[1])
    await bot.send_message(
        user_id,
        "✅ UC muvaffaqiyatli yuklandi! O'yinda tekshiring. 🎮"
    )
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("✅ Tasdiqlandi")

# ─────────────────────────────────────────────────────────
#  Callback: Task tasdiqlash / rad etish
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("task_ok:"))
async def task_ok(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")

    _, user_id, task_key, reward = call.data.split(":")
    user_id = int(user_id)
    reward  = int(reward)

    db.set_task_done(user_id, task_key)
    db.add_balance(user_id, reward)

    await bot.send_message(
        user_id,
        f"✅ Vazifa tasdiqlandi! <b>+{reward:,} so'm</b> hisobingizga qo'shildi! 🎉",
        parse_mode="HTML"
    )
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("✅ Tasdiqlandi")

@router.callback_query(F.data.startswith("task_no:"))
async def task_no(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")

    _, user_id, task_key = call.data.split(":")
    user_id = int(user_id)

    await bot.send_message(
        user_id,
        f"❌ Vazifa tasdiqlanmadi. Iltimos qayta urinib ko'ring."
    )
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("❌ Rad etildi")

# ─────────────────────────────────────────────────────────
#  Start
# ─────────────────────────────────────────────────────────
async def main():
    db.init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
