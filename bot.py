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
    logging.info(f"[WEB_APP_DATA] KELDI: raw={message.web_app_data.data}")
    try:
        data = json.loads(message.web_app_data.data)
    except Exception as e:
        logging.error(f"[WEB_APP_DATA] JSON PARSE XATO: {e}")
        return

    dtype = data.get("type")
    user_id = message.from_user.id
    user = message.from_user
    logging.info(f"[WEB_APP_DATA] dtype={dtype} user_id={user_id} data={data}")

    # ── Topup so'rov ──
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

    # ── UC buyurtma ──
    elif dtype == "uc_purchase_request":
        logging.info(f"[UC_ORDER] Boshlandi user_id={user_id}")
        pubg_id   = data.get("player_id", "")
        uc_amount = data.get("uc", 0)
        price     = data.get("price", 0)

        real_balance = db.get_balance(user_id)
        logging.info(f"[UC_ORDER] real_balance={real_balance} price={price}")
        if real_balance < price:
            logging.warning(f"[UC_ORDER] Balans yetarli emas: {real_balance} < {price}")
            await message.answer(f"❌ Balans yetarli emas.\nBalansingiz: {real_balance:,} so'm\nKerakli summa: {price:,} so'm")
            return

        ok = db.deduct_balance(user_id, price)
        logging.info(f"[UC_ORDER] deduct_balance natija: {ok}")
        if not ok:
            await message.answer("❌ Balans yetarli emas.")
            return

        await message.answer(f"✅ UC buyurtmangiz qabul qilindi!\nAdmin tez orada yuklaydi. 🎮")

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ UC Yuklandi", callback_data=f"uc_done:{user_id}:{uc_amount}"))
        kb.add(InlineKeyboardButton("❌ Bekor qilish", callback_data=f"uc_cancel:{user_id}:{price}"))

        admin_text = (
            f"🎮 <b>UC Buyurtma</b>\n\n"
            f"👤 <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
            f"🆔 TG ID: <code>{user.id}</code>\n"
            f"🕹 PUBG ID: <code>{pubg_id}</code>\n"
            f"💎 UC: <b>{uc_amount:,} UC</b>\n"
            f"💵 Narxi: <b>{price:,} so'm</b>\n"
            f"💰 Qolgan balans: {db.get_balance(user_id):,} so'm"
        )
        try:
            await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=kb)
            logging.info(f"[UC_ORDER] Admin ({ADMIN_ID}) ga xabar yuborildi")
        except Exception as e:
            logging.error(f"[UC_ORDER] Admin ga xabar yuborishda XATO: {e}")
        db.create_uc_order(user_id, pubg_id, uc_amount, price, 0)

    # ── VIP xarid ──
    elif dtype == "vip_purchase":
        vip_type = data.get("vip_type", "")
        price    = data.get("price", 0)

        real_balance = db.get_balance(user_id)
        if real_balance < price:
            await message.answer(f"❌ Balans yetarli emas.\nBalansingiz: {real_balance:,} so'm")
            return

        ok = db.deduct_balance(user_id, price)
        if not ok:
            await message.answer("❌ Balans yetarli emas.")
            return

        db.create_vip_order(user_id, vip_type, price)
        try:
            await bot.unban_chat_member(VIP_CHAT_ID, user_id)
        except Exception:
            pass

        admin_text = (
            f"👑 <b>VIP Xarid</b>\n\n"
            f"👤 <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"📦 Paket: <b>{vip_type}</b>\n"
            f"💵 Narxi: <b>{price:,} so'm</b>"
        )
        await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
        await message.answer(f"🎉 <b>{vip_type}</b> VIP faollashtirildi!\nKanalga kirish: @CLOUDE_CHEATS", parse_mode="HTML")

    # ── Task verify ──
    elif dtype == "task_verify_request":
        task_key = data.get("task", "")

        # Allaqachon bajarilganmi?
        row = db.get_task(user_id, task_key)
        if row and row["status"] == "done":
            await message.answer("✅ Bu vazifa allaqachon bajarilgan.")
            return

        if task_key == "tg":
            try:
                member = await bot.get_chat_member("@CLOUDE_CHEATS", user_id)
                if member.status in ("member", "administrator", "creator"):
                    db.set_task_done(user_id, "tg")
                    reward = TASK_REWARDS["tg"]
                    db.add_balance(user_id, reward)
                    await message.answer(f"✅ A'zolik tasdiqlandi! +{reward:,} so'm qo'shildi. 🎉")
                else:
                    await message.answer("❌ Siz hali @CLOUDE_CHEATS kanalga a'zo emassiz.")
            except Exception as e:
                logging.error(f"Task tg check error: {e}")
                await message.answer("⚠️ Tekshirishda xatolik. Qayta urining.")
        else:
            reward = TASK_REWARDS.get(task_key, 3000)
            kb = InlineKeyboardMarkup()
            kb.add(
                InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"task_ok:{user_id}:{task_key}:{reward}"),
                InlineKeyboardButton("❌ Rad etish", callback_data=f"task_no:{user_id}:{task_key}")
            )
            task_names = {"ig": "Instagram", "yt": "YouTube"}
            admin_text = (
                f"📋 <b>Vazifa tekshirish</b>\n\n"
                f"👤 <a href='tg://user?id={user.id}'>{user.first_name}</a>\n"
                f"🆔 ID: <code>{user.id}</code>\n"
                f"📌 Vazifa: <b>{task_names.get(task_key, task_key.upper())}</b>\n"
                f"💰 Mukofot: <b>{reward:,} so'm</b>"
            )
            await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=kb)
            await message.answer("⏳ So'rovingiz adminga yuborildi. Tez orada tekshiriladi.")

# ── Screenshot (to'lov) ──────────────────────────────────
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

# ── Admin: raqam yozsa topup tasdiqlash ─────────────────
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

    # MUHIM: approve_topup() endi `amount` ham qabul qiladi va balansga
    # pulni shu funksiya ICHIDA bir marta qo'shadi (topup_requests
    # jadvalidagi 'amount' ustunini ham yangilab). Bu yerda alohida
    # db.add_balance() chaqirilmaydi - aks holda pul ikki marta
    # qo'shilib ketadi.
    ok = db.approve_topup(row["id"], amount)
    if not ok:
        await message.answer("❌ Bu so'rov allaqachon tasdiqlangan yoki topilmadi.")
        return

    new_balance = db.get_balance(row["user_id"])
    await message.answer(
        f"✅ {row['user_id']} ga {amount:,} so'm qo'shildi.\n"
        f"💰 Yangi balans: {new_balance:,} so'm"
    )
    await bot.send_message(
        row["user_id"],
        f"✅ Hisobingizga <b>{amount:,} so'm</b> qo'shildi! 🎉\n"
        f"💰 Joriy balans: <b>{new_balance:,} so'm</b>",
        parse_mode="HTML"
    )

# ── Callback: UC tasdiqlash ──────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("uc_done:"))
async def uc_done(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    parts = call.data.split(":")
    user_id = int(parts[1])
    uc_amount = parts[2] if len(parts) > 2 else "?"
    await bot.send_message(user_id, f"✅ <b>{uc_amount} UC</b> muvaffaqiyatli yuklandi! 🎮", parse_mode="HTML")
    await call.message.edit_reply_markup()
    await call.answer("✅ Tasdiqlandi")

# ── Callback: UC bekor qilish ────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("uc_cancel:"))
async def uc_cancel(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    parts = call.data.split(":")
    user_id = int(parts[1])
    price = int(parts[2]) if len(parts) > 2 else 0
    if price > 0:
        db.add_balance(user_id, price)
    await bot.send_message(user_id, f"❌ UC buyurtmangiz bekor qilindi. {price:,} so'm qaytarildi.")
    await call.message.edit_reply_markup()
    await call.answer("❌ Bekor qilindi")

# ── Callback: Task tasdiqlash ────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("task_ok:"))
async def task_ok(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    _, user_id, task_key, reward = call.data.split(":")
    user_id, reward = int(user_id), int(reward)

    row = db.get_task(user_id, task_key)
    if row and row["status"] == "done":
        await call.answer("⚠️ Bu vazifa allaqachon tasdiqlangan")
        return

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
        
