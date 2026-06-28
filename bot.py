import asyncio
import logging
import os
import json

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
)

import database as db

logging.basicConfig(level=logging.INFO)

BOT_TOKEN   = os.environ["BOT_TOKEN"]
ADMIN_ID    = int(os.environ["ADMIN_ID"])
VIP_CHAT_ID = int(os.environ["VIP_CHAT_ID"])
WEBAPP_URL  = os.environ["WEBAPP_URL"]

TASK_REWARDS = {"tg": 5000, "ig": 3000, "yt": 3000}

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(bot)

# ── /start ──────────────────────────────────────────────
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    args = message.get_args()
    ref_by = None
    if args.startswith("ref"):
        try:
            ref_by = int(args[3:])
            if ref_by == message.from_user.id:
                ref_by = None
        except ValueError:
            ref_by = None

    db.ensure_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        ref_by=ref_by
    )

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(
        text="CLOUDE VIP PREMIUM 💎",
        web_app=WebAppInfo(url=WEBAPP_URL)
    ))
    await message.answer("CLOUDE VIP PREMIUM 💎", reply_markup=kb)

# ── Webapp sendData ──────────────────────────────────────
@dp.message_handler(content_types=types.ContentType.WEB_APP_DATA)
async def handle_webapp_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        return

    dtype = data.get("type")
    user_id = message.from_user.id
    user = message.from_user

    # Topup so'rov
    if dtype == "topup_request":
        amount = data.get("amount", 0)
        method = data.get("method", "")

        await message.answer("✅ So'rovingiz qabul qilindi!\nAdmin tez orada tasdiqlaydi. 🕐")

        admin_text = (
            f"💰 <b>Topup so'rov</b>\n\n"
            f"👤 <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"💵 Summa: <b>{amount:,} so'm</b>\n"
            f"🏦 Usul: {method}\n\n"
            f"Tasdiqlash uchun summani yuboring (faqat raqam):"
        )
        sent = await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
        db.create_topup(user_id, amount, method, sent.message_id)

    # UC buyurtma
    elif dtype == "uc_purchase_request":
        pubg_id   = data.get("player_id", "")
        uc_amount = data.get("uc", 0)
        price     = data.get("price", 0)

        ok = db.deduct_balance(user_id, price)
        if not ok:
            await message.answer("❌ Balans yetarli emas.")
            return

        await message.answer(f"✅ UC buyurtmangiz qabul qilindi! Admin tez orada yuklaydi. 🎮")

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"uc_done:{user_id}"))

        admin_text = (
            f"🎮 <b>UC Buyurtma</b>\n\n"
            f"👤 <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
            f"🆔 TG ID: <code>{user.id}</code>\n"
            f"🕹 PUBG ID: <code>{pubg_id}</code>\n"
            f"💎 UC: <b>{uc_amount:,} UC</b>\n"
            f"💵 Narxi: {price:,} so'm"
        )
        sent = await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=kb)
        db.create_uc_order(user_id, pubg_id, uc_amount, price, sent.message_id)

    # VIP xarid
    elif dtype == "vip_purchase":
        vip_type = data.get("vip_type", "")
        price    = data.get("price", 0)

        ok = db.deduct_balance(user_id, price)
        if not ok:
            await message.answer("❌ Balans yetarli emas.")
            return

        db.create_vip_order(user_id, vip_type, price)
        try:
            await bot.unban_chat_member(VIP_CHAT_ID, user_id)
        except Exception:
            pass

        await message.answer(f"🎉 <b>{vip_type}</b> VIP faollashtirildi!\nKanalga kirish: @CLOUDE_CHEATS", parse_mode="HTML")

    # Task verify
    elif dtype == "task_verify_request":
        task_key = data.get("task", "")
        if task_key == "tg":
            try:
                member = await bot.get_chat_member("@CLOUDE_CHEATS", user_id)
                if member.status in ("member", "administrator", "creator"):
                    db.set_task_done(user_id, "tg")
                    reward = TASK_REWARDS["tg"]
                    db.add_balance(user_id, reward)
                    await message.answer(f"✅ A'zolik tasdiqlandi! +{reward:,} so'm qo'shildi.")
                else:
                    await message.answer("❌ Siz hali kanalga a'zo emassiz.")
            except Exception:
                await message.answer("⚠️ Tekshirishda xatolik. Qayta urining.")
        else:
            reward = TASK_REWARDS.get(task_key, 3000)
            kb = InlineKeyboardMarkup()
            kb.add(
                InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"task_ok:{user_id}:{task_key}:{reward}"),
                InlineKeyboardButton("❌ Rad etish", callback_data=f"task_no:{user_id}:{task_key}")
            )
            admin_text = (
                f"📋 <b>Vazifa tekshirish</b>\n\n"
                f"👤 <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
                f"🆔 ID: <code>{user.id}</code>\n"
                f"📌 Vazifa: <b>{task_key.upper()}</b>"
            )
            await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=kb)
            await message.answer("⏳ So'rovingiz adminga yuborildi.")

# ── Screenshot ───────────────────────────────────────────
@dp.message_handler(content_types=types.ContentType.PHOTO)
async def handle_photo(message: types.Message):
    user = message.from_user
    db.ensure_user(user.id, user.username, user.first_name)

    await message.answer("✅ Skrinshot qabul qilindi!\nAdmin tez orada tasdiqlaydi. 🕐")

    caption = (
        f"📸 <b>To'lov screenshoti</b>\n\n"
        f"👤 <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        f"Tasdiqlash uchun summani yuboring (faqat raqam):"
    )
    await message.forward(ADMIN_ID)
    sent = await bot.send_message(ADMIN_ID, caption, parse_mode="HTML")
    db.create_topup(user.id, 0, "screenshot", sent.message_id)

# ── Admin raqam yozsa topup tasdiqlash ──────────────────
@dp.message_handler(lambda m: m.from_user.id == ADMIN_ID and m.text and m.text.strip().isdigit())
async def admin_confirm(message: types.Message):
    amount = int(message.text.strip())
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
        f"✅ Hisobingizga <b>{amount:,} so'm</b> qo'shildi! 🎉",
        parse_mode="HTML"
    )

# ── Callback: UC tasdiqlash ──────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("uc_done:"))
async def uc_done(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    user_id = int(call.data.split(":")[1])
    await bot.send_message(user_id, "✅ UC muvaffaqiyatli yuklandi! 🎮")
    await call.message.edit_reply_markup()
    await call.answer("✅ Tasdiqlandi")

# ── Callback: Task tasdiqlash ────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("task_ok:"))
async def task_ok(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    _, user_id, task_key, reward = call.data.split(":")
    user_id, reward = int(user_id), int(reward)
    db.set_task_done(user_id, task_key)
    db.add_balance(user_id, reward)
    await bot.send_message(user_id, f"✅ Vazifa tasdiqlandi! +{reward:,} so'm qo'shildi! 🎉")
    await call.message.edit_reply_markup()
    await call.answer("✅ Tasdiqlandi")

@dp.callback_query_handler(lambda c: c.data.startswith("task_no:"))
async def task_no(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    _, user_id, task_key = call.data.split(":")
    await bot.send_message(int(user_id), "❌ Vazifa tasdiqlanmadi. Qayta urining.")
    await call.message.edit_reply_markup()
    await call.answer("❌ Rad etildi")

# ── Start ────────────────────────────────────────────────
if __name__ == "__main__":
    db.init_db()
    executor.start_polling(dp, skip_updates=True)
