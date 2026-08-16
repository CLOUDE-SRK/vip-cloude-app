import asyncio
import html
import logging
import os
import json
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.utils.exceptions import (
    BotBlocked, ChatNotFound, UserDeactivated, TelegramAPIError
)
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

# ── XAVFSIZLIK: haqiqiy narxlar faqat shu yerda belgilanadi ──────────────
# Frontend (index.html) dan kelgan "price" hech qachon ishonib bo'lmaydi —
# u brauzer konsolidan osongina o'zgartirilishi mumkin. Shuning uchun har bir
# xarid uchun narx SHU serverdagi jadvaldan olinadi, client yuborgan qiymat
# faqat solishtirish/tekshirish uchun ishlatiladi.
# index.html dagi vipData va UC_PACKAGES bilan har doim SINXRON turishi shart!
VIP_PRICES = {
    "15 kunlik": 30000,
    "1 oylik":   60000,
    "1 sezon":   100000,
    "VIP sezon": 299000,
}

UC_PACKAGES = {
    60:   11999,
    120:  24999,
    180:  39000,
    325:  59000,
    660:  115000,
    1800: 299000,
    3830: 569000,
    8100: 1199000,
}

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(bot)

# Admin "📣 E'lon" tugmasini bosgach, uning KEYINGI yuboradigan xabarini
# hammaga broadcast qilish uchun shu holatni eslab turamiz. Faqat ADMIN_ID
# shu to'plamda bo'lishi mumkin.
pending_announcement = set()


def esc(text):
    """Foydalanuvchi kiritgan matnni (masalan first_name) HTML xabarlarga
    xavfsiz qoʻshish uchun escape qiladi. Aks holda ismida &, <, > kabi
    belgilar boʻlsa Telegram 'cant parse entities' xatosini qaytaradi va
    handler try/except bilan oʻralmagan joyda bot shu yerda yiqiladi."""
    return html.escape(str(text or ""))


def format_topup_history_message(row, status_label: str) -> str:
    """Admin panelidagi 'Pul o'tkazmalar' tarixi kartochkasi bilan bir xil
    ko'rinishdagi xabarni tayyorlaydi — botda tasdiqlash/bekor qilishdan
    keyin adminga shu formatda yuboriladi, shunda bot va panel tarixi
    ko'rinish jihatidan bir xil bo'ladi."""
    status_emoji = "✅" if status_label == "Tasdiqlangan" else "❌"

    user = db.get_user(row["user_id"])
    if user:
        name = user["first_name"] or user["username"] or "Foydalanuvchi"
    else:
        name = "Foydalanuvchi"

    dt = datetime.fromtimestamp(row["created_at"]) if row["created_at"] else datetime.now()

    return (
        f"{status_emoji} <b>O'tkazma #{row['id']}</b> · {dt.strftime('%d.%m %H:%M')}\n"
        f"Holat: <b>{status_label}</b>\n\n"
        f"👤 Foydalanuvchi: {esc(name)}\n"
        f"🆔 TG ID: <code>{row['user_id']}</code>\n"
        f"💵 Summa: <b>{row['amount']:,} so'm</b>\n"
        f"🏦 Usul: {esc(row['method'])}"
    )

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
    # "Yordam" o'rniga "📣 E'lon" — bu tugma hammaga ko'rinadi, lekin
    # bosilganda faqat ADMIN_ID uchun ishlaydi (callback ichida tekshiriladi).
    # Shunday qilinishining sababi: shu bitta /start handler ham admin,
    # ham oddiy foydalanuvchi uchun ishlatiladi, shuning uchun tugma
    # ko'rinishini oldindan admin/oddiy foydalanuvchi deb ikkiga ajratish
    # shart emas — ruxsat callback bosilganda tekshiriladi.
    kb.row(
        InlineKeyboardButton("📢 Bizning kanal", url="https://t.me/CLOUDE_CHEATS"),
        InlineKeyboardButton("📣 E'lon", callback_data="announce_start"),
    )

    photo_path = "start_banner.png"  # bot.py bilan bir xil papkada turishi kerak
    with open(photo_path, "rb") as photo:
        await message.answer_photo(
            photo=photo,
            reply_markup=kb
        )


# ── Callback: "📣 E'lon" tugmasi bosilganda ──────────────
@dp.callback_query_handler(lambda c: c.data == "announce_start")
async def announce_start(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Bu boʻlim faqat ᴵᴬᴹ𝘾𝙇𝙊𝙐𝘿𝙀 uchun", show_alert=True)
    pending_announcement.add(call.from_user.id)
    await call.answer()
    await bot.send_message(ADMIN_ID, "📢 Marhamat, e'loningizni kiriting:")


# ── Admin "E'lon" tugmasidan keyin yuborgan XOHLAGAN xabarini
# (matn, rasm, video va h.k.) /start bosgan barcha foydalanuvchilarga
# broadcast qiladi. MUHIM: shu handler quyidagi handle_webapp_data,
# handle_photo va admin_confirm handlerlaridan OLDIN turishi shart —
# aks holda masalan admin raqam yuborsa (masalan narx e'lon qilsa),
# admin_confirm handleri uni "to'lov tasdiqlash" deb tushunib qolar edi.
@dp.message_handler(
    lambda m: m.from_user.id == ADMIN_ID and m.from_user.id in pending_announcement,
    content_types=types.ContentType.ANY,
)
async def announce_broadcast(message: types.Message):
    pending_announcement.discard(message.from_user.id)

    user_ids = db.get_all_user_ids()
    status = await message.answer(f"⏳ E'lon yuborilmoqda... (0/{len(user_ids)})")

    sent, blocked, failed = 0, 0, 0
    for i, uid in enumerate(user_ids, start=1):
        try:
            await message.copy_to(uid)
            sent += 1
        except (BotBlocked, UserDeactivated, ChatNotFound):
            blocked += 1
        except TelegramAPIError:
            failed += 1
        except Exception:
            failed += 1

        await asyncio.sleep(0.05)
        if i % 25 == 0:
            try:
                await status.edit_text(f"⏳ E'lon yuborilmoqda... ({i}/{len(user_ids)})")
            except Exception:
                pass

    await status.edit_text(
        f"✅ E'lon yuborildi.\n"
        f"Jami: {len(user_ids)}\n"
        f"Yuborildi: {sent}\n"
        f"Bloklagan/o'chirilgan: {blocked}\n"
        f"Xatolik: {failed}"
    )


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

        # XAVFSIZLIK: amount frontenddan noto'g'ri turda (masalan matn) kelib
        # qolsa ham ",": formatlash xato bermasin uchun avval int ga o'giramiz.
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            logging.warning(f"[TOPUP] Notoʻgʻri amount qiymati: {amount!r} user_id={user_id}")
            amount = 0

        # Avval bazaga yozamiz — shu topup_id orqali keyin admin tugmasi
        # (tasdiqlash/bekor qilish) ANIQ shu yozuvga bog'lanadi. Shu bilan
        # bir vaqtda bir nechta odam so'rov yuborsa ham, admin summani
        # qo'lda yozib "qaysi biriga tegishli" deb taxmin qilishga
        # majbur bo'lmaydi — har bir xabarning o'z tugmasi bor.
        topup_id = db.create_topup(user_id, amount, method, 0)

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"topup_ok:{topup_id}"))
        kb.add(InlineKeyboardButton("❌ Bekor qilish", callback_data=f"topup_cancel:{topup_id}"))

        admin_text = (
            f"💰 <b>Topup so'rov</b>\n\n"
            f"👤 <a href='tg://user?id={user.id}'>{esc(user.first_name)}</a>\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"💵 Summa: <b>{amount:,} so'm</b>\n"
            f"🏦 Usul: {esc(method)}\n\n"
            f"So'rov ID: <code>{topup_id}</code>"
        )
        try:
            await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logging.error(f"[TOPUP] Admin ga xabar yuborishda XATO: {e}")

    # ── UC buyurtma ──
    elif dtype == "uc_purchase_request":
        logging.info(f"[UC_ORDER] Boshlandi user_id={user_id}")
        pubg_id       = data.get("player_id", "")
        uc_amount     = data.get("uc", 0)
        client_price  = data.get("price", 0)

        # XAVFSIZLIK: client yuborgan narxga ISHONMAYMIZ. Faqat serverdagi
        # UC_PACKAGES jadvalidan haqiqiy narxni olamiz. Agar bunday UC
        # to'plami umuman mavjud bo'lmasa — so'rov butunlay rad etiladi.
        price = UC_PACKAGES.get(uc_amount)
        if price is None:
            logging.warning(f"[UC_ORDER] NOMA'LUM UC to'plami: uc={uc_amount} (client_price={client_price}) user_id={user_id}")
            return
        if client_price != price:
            logging.warning(f"[UC_ORDER] Narx mos kelmadi! client={client_price} haqiqiy={price} user_id={user_id} — haqiqiy narx ishlatiladi")

        real_balance = db.get_balance(user_id)
        logging.info(f"[UC_ORDER] real_balance={real_balance} price={price}")
        if real_balance < price:
            logging.warning(f"[UC_ORDER] Balans yetarli emas: {real_balance} < {price}")
            return

        ok = db.deduct_balance(user_id, price)
        logging.info(f"[UC_ORDER] deduct_balance natija: {ok}")
        if not ok:
            return

        # Avval bazaga order sifatida yozamiz — shu order_id orqali keyin
        # admin tugmalari (approve/cancel) aniq shu yozuvga bog'lanadi.
        order_id = db.create_uc_order(user_id, pubg_id, uc_amount, price, 0)

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ UC Yuklandi", callback_data=f"uc_done:{order_id}"))
        kb.add(InlineKeyboardButton("❌ Bekor qilish", callback_data=f"uc_cancel:{order_id}"))

        admin_text = (
            f"🎮 <b>UC Buyurtma</b>\n\n"
            f"👤 <a href='tg://user?id={user.id}'>{esc(user.first_name)}</a>\n"
            f"🆔 TG ID: <code>{user.id}</code>\n"
            f"🕹 PUBG ID: <code>{esc(pubg_id)}</code>\n"
            f"💎 UC: <b>{uc_amount:,} UC</b>\n"
            f"💵 Narxi: <b>{price:,} so'm</b>\n"
            f"💰 Qolgan balans: {db.get_balance(user_id):,} so'm"
        )
        try:
            await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=kb)
            logging.info(f"[UC_ORDER] Admin ({ADMIN_ID}) ga xabar yuborildi")
        except Exception as e:
            logging.error(f"[UC_ORDER] Admin ga xabar yuborishda XATO: {e}")

    # ── VIP xarid ──
    elif dtype == "vip_purchase":
        vip_type     = data.get("vip_type", "")
        client_price = data.get("price", 0)

        # XAVFSIZLIK: bu yerdagi eng muhim tekshiruv. Client yuborgan "price"
        # ga hech qachon ishonmaymiz — aks holda foydalanuvchi brauzer
        # konsolidan price:1 yuborib, VIP kanalga bepul kirib olishi mumkin edi.
        # Narx FAQAT serverdagi VIP_PRICES jadvalidan olinadi.
        price = VIP_PRICES.get(vip_type)
        if price is None:
            logging.warning(f"[VIP] NOMA'LUM VIP turi: '{vip_type}' (client_price={client_price}) user_id={user_id} — rad etildi")
            return
        if client_price != price:
            logging.warning(f"[VIP] Narx mos kelmadi! client={client_price} haqiqiy={price} user_id={user_id} — haqiqiy narx ishlatiladi")

        real_balance = db.get_balance(user_id)
        if real_balance < price:
            return

        ok = db.deduct_balance(user_id, price)
        if not ok:
            return

        db.create_vip_order(user_id, vip_type, price)

        # Avval ban olib tashlaymiz (agar ban bo'lsa)
        try:
            await bot.unban_chat_member(VIP_CHAT_ID, user_id)
        except Exception:
            pass

        # Bir martalik invite link yaratib foydalanuvchiga yuboramiz
        try:
            expire = datetime.now() + timedelta(days=1)
            link = await bot.create_chat_invite_link(
                chat_id=VIP_CHAT_ID,
                member_limit=1,
                expire_date=expire
            )
            await bot.send_message(
                user_id,
                f"✅ <b>{esc(vip_type)}</b> faollashtirildi!\n\n"
                f"👇 VIP kanalga kirish uchun tugmani bosing:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("💎 VIP Kanalga kirish", url=link.invite_link)
                )
            )
        except Exception as e:
            logging.error(f"[VIP] Invite link yaratishda xato: {e}")

        admin_text = (
            f"👑 <b>VIP Xarid</b>\n\n"
            f"👤 <a href='tg://user?id={user.id}'>{esc(user.first_name)}</a>\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"📦 Paket: <b>{esc(vip_type)}</b>\n"
            f"💵 Narxi: <b>{price:,} so'm</b>"
        )
        try:
            await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"[VIP] Admin ga xabar yuborishda XATO: {e}")

    # ── Task verify ──
    elif dtype == "task_verify_request":
        task_key = data.get("task", "")

        row = db.get_task(user_id, task_key)
        if row and row["status"] == "done":
            return

        if task_key == "tg":
            try:
                member = await bot.get_chat_member("@CLOUDE_CHEATS", user_id)
                if member.status in ("member", "administrator", "creator"):
                    db.set_task_done(user_id, "tg")
                    reward = TASK_REWARDS["tg"]
                    db.add_balance(user_id, reward)
                else:
                    pass
            except Exception as e:
                logging.error(f"Task tg check error: {e}")
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


# ── Callback: Topup tasdiqlash (tugma orqali, ID aniq bog'langan) ────────
@dp.callback_query_handler(lambda c: c.data.startswith("topup_ok:"))
async def topup_ok(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    topup_id = int(call.data.split(":")[1])
    ok = db.approve_topup(topup_id)  # bazada saqlangan summa ishlatiladi
    if not ok:
        return await call.answer("⚠️ Bu so'rov allaqachon yakunlangan yoki topilmadi")
    row = db.get_topup(topup_id)
    await call.message.edit_reply_markup()
    await call.answer("✅ Tasdiqlandi")
    try:
        await bot.send_message(
            call.from_user.id,
            format_topup_history_message(row, "Tasdiqlangan"),
            parse_mode="HTML"
        )
    except Exception:
        pass


# ── Callback: Topup bekor qilish ─────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("topup_cancel:"))
async def topup_cancel(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    topup_id = int(call.data.split(":")[1])
    ok = db.reject_topup(topup_id)
    if not ok:
        await call.answer("⚠️ Bu so'rov allaqachon yakunlangan yoki topilmadi")
        await call.message.edit_reply_markup()
        return
    await call.answer("❌ Bekor qilindi")
    await call.message.edit_reply_markup()
    row = db.get_topup(topup_id)
    try:
        await bot.send_message(
            call.from_user.id,
            format_topup_history_message(row, "Rad etilgan"),
            parse_mode="HTML"
        )
    except Exception:
        pass


# ── Admin: raqam yozsa topup tasdiqlash (ESKI, zaxira usul — masalan,
# to'langan summa so'rovdagidan farq qilsa qo'lda tuzatish uchun) ───────
@dp.message_handler(lambda m: m.from_user.id == ADMIN_ID and m.text and m.text.strip().isdigit())
async def admin_confirm(message: types.Message):
    amount = int(message.text.strip())
    conn = db.get_conn()
    pending = conn.execute(
        "SELECT * FROM topup_requests WHERE status='pending' ORDER BY id DESC"
    ).fetchall()
    conn.close()

    if not pending:
        await message.answer("❌ Pending so'rov topilmadi.")
        return

    # XAVFSIZLIK: agar bir vaqtda bir nechta odam pending bo'lsa, "eng oxirgisini"
    # taxmin qilib avtomatik tasdiqlash NOTO'G'RI ODAMGA PUL TUSHISHIGA olib
    # kelishi mumkin. Shu sababli, bunday holatda hech narsani taxmin qilmaymiz —
    # ro'yxatni ko'rsatib, admindan aniq so'rov ID sini so'raymiz.
    if len(pending) > 1:
        lines = [f"⚠️ {len(pending)} ta pending so'rov bor — kimga tegishli ekanini aniqlashtiring:\n"]
        for r in pending[:10]:
            lines.append(f"ID:{r['id']} — user:{r['user_id']} — {r['method']}")
        lines.append("\nTasdiqlash uchun: /confirm <so'rov_ID> <summa>")
        await message.answer("\n".join(lines))
        return

    row = pending[0]
    ok = db.approve_topup(row["id"], amount)
    if not ok:
        await message.answer("❌ Bu so'rov allaqachon tasdiqlangan yoki topilmadi.")
        return

    updated_row = db.get_topup(row["id"])
    await message.answer(
        format_topup_history_message(updated_row, "Tasdiqlangan"),
        parse_mode="HTML"
    )


# ── Admin: bir nechta pending bo'lganda aniq ID bilan tasdiqlash ─────────
@dp.message_handler(commands=["confirm"])
async def admin_confirm_explicit(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.get_args().split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer("Foydalanish: /confirm <so'rov_ID> <summa>")
        return
    req_id, amount = int(parts[0]), int(parts[1])

    ok = db.approve_topup(req_id, amount)
    if not ok:
        await message.answer("❌ Bu so'rov allaqachon tasdiqlangan yoki topilmadi.")
        return

    row = db.get_topup(req_id)
    await message.answer(
        format_topup_history_message(row, "Tasdiqlangan"),
        parse_mode="HTML"
    )


# ── /broadcast: hamma foydalanuvchiga xabar yuborish ─────
# Faqat admin ishlata oladi. Ikki xil ishlatilishi bor:
#   1) /broadcast <matn>              -> shu matnni hammaga yuboradi
#   2) biror xabarga (rasm/video/...) reply qilib /broadcast yozish
#      -> o'sha xabarni o'zgarishsiz hammaga forward/copy qiladi
# "Hamma" deganda db.get_all_user_ids() qaytargan barcha user_id lar
# nazarda tutiladi — bunga nafaqat /start bosganlar, balki webappni
# ochganlar ham kiradi, chunki ikkalasi ham ensure_user() ni chaqiradi.
@dp.message_handler(commands=["broadcast"])
async def admin_broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    text = message.get_args()
    reply = message.reply_to_message

    if not text and not reply:
        await message.answer(
            "Foydalanish:\n"
            "1) /broadcast Xabar matni\n"
            "2) Yoki yubormoqchi bo'lgan xabaringizga (rasm/video ham bo'lishi mumkin) "
            "reply qilib /broadcast deb yozing."
        )
        return

    user_ids = db.get_all_user_ids()
    status = await message.answer(f"⏳ Yuborilmoqda... (0/{len(user_ids)})")

    sent, blocked, failed = 0, 0, 0
    for i, uid in enumerate(user_ids, start=1):
        try:
            if reply:
                await reply.copy_to(uid)
            else:
                await bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except (BotBlocked, UserDeactivated, ChatNotFound):
            blocked += 1
        except TelegramAPIError:
            failed += 1
        except Exception:
            failed += 1

        # Telegramning flood-limitiga tushib qolmaslik uchun kichik pauza
        await asyncio.sleep(0.05)

        if i % 25 == 0:
            try:
                await status.edit_text(f"⏳ Yuborilmoqda... ({i}/{len(user_ids)})")
            except Exception:
                pass

    await status.edit_text(
        f"✅ Broadcast tugadi.\n"
        f"Jami: {len(user_ids)}\n"
        f"Yuborildi: {sent}\n"
        f"Bloklagan/o'chirilgan: {blocked}\n"
        f"Xatolik: {failed}"
    )


# ── Callback: UC tasdiqlash ──────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("uc_done:"))
async def uc_done(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    order_id = int(call.data.split(":")[1])
    # MUHIM: avval bu yerda bazaga umuman yozilmagan edi — order abadiy
    # "pending" holatida qolib, keyinchalik yana "bekor qilish" orqali
    # ikki karra pul qaytarib olish (allaqachon yetkazilgan UC ustiga)
    # imkoniyati bor edi. Endi to'g'ridan-to'g'ri "approved" qilib qo'yamiz.
    db.approve_uc_order(order_id)
    await call.message.edit_reply_markup()
    await call.answer("✅ Tasdiqlandi")


# ── Callback: UC bekor qilish ────────────────────────────
@dp.callback_query_handler(lambda c: c.data.startswith("uc_cancel:"))
async def uc_cancel(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    order_id = int(call.data.split(":")[1])
    # cancel_uc_order o'zi statusni tekshiradi (faqat 'pending' bo'lsa
    # bekor qiladi va pulni qaytaradi) — shu bilan allaqachon
    # tasdiqlangan (approved) buyurtmani qayta bekor qilib, pulni
    # ikki marta qaytarish imkonsiz bo'lib qoladi.
    result = db.cancel_uc_order(order_id)
    if result is None:
        await call.answer("⚠️ Bu buyurtma allaqachon yakunlangan yoki topilmadi")
    else:
        await call.answer("❌ Bekor qilindi, pul qaytarildi")
    await call.message.edit_reply_markup()


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
    await call.message.edit_reply_markup()
    await call.answer("✅ Tasdiqlandi")


@dp.callback_query_handler(lambda c: c.data.startswith("task_no:"))
async def task_no(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    await call.message.edit_reply_markup()
    await call.answer("❌ Rad etildi")


# ── Start ────────────────────────────────────────────────
if __name__ == "__main__":
    db.init_db()
    executor.start_polling(dp, skip_updates=True)
    
