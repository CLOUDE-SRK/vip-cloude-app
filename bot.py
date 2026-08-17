import asyncio
import html
import logging
import os
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

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
# Bot ishga tushgan vaqt — WebApp havolasiga "?v=..." sifatida
# qo'shiladi, shunda Telegram har bot qayta deploy qilinganda
# (index.html yangilanganda) eski keshlangan sahifani emas,
# har doim eng so'nggi versiyani yuklaydi.
_BOOT_VERSION = int(time.time())

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(bot)

# Admin "📣 E'lon" tugmasini bosgach, bosqichma-bosqich holatni shu yerda
# saqlaymiz: avval kontent (matn/rasm/video) so'raladi, keyin ixtiyoriy
# tugma (matn + havola) qo'shish so'raladi — PayerPin kabi botlardagi
# "Harid qilish" turidagi tugmali e'lonlarni yaratish uchun.
# Format: {admin_id: {"stage": ..., "chat_id": ..., "message_id": ..., "btn_text": ...}}
announce_state = {}


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


def format_uc_order_message(row, status_label: str) -> str:
    """Admin panelidagi 'Buyurtmalar' tarixi kartochkasi bilan bir xil
    ko'rinishdagi xabarni tayyorlaydi — botda tasdiqlash/bekor qilishdan
    keyin adminga shu formatda yuboriladi."""
    status_emoji = "✅" if status_label == "Tasdiqlangan" else "❌"

    user = db.get_user(row["user_id"])
    if user:
        name = user["first_name"] or user["username"] or "Foydalanuvchi"
    else:
        name = "Foydalanuvchi"

    dt = datetime.fromtimestamp(row["created_at"]) if row["created_at"] else datetime.now()

    return (
        f"{status_emoji} <b>UC buyurtma #{row['id']}</b> · {dt.strftime('%d.%m %H:%M')}\n"
        f"Holat: <b>{status_label}</b>\n\n"
        f"👤 Foydalanuvchi: {esc(name)}\n"
        f"🆔 TG ID: <code>{row['user_id']}</code>\n"
        f"🕹 PUBG ID: <code>{esc(row['pubg_id'])}</code>\n"
        f"💎 UC: <b>{row['uc_amount']:,} UC</b>\n"
        f"💵 Narxi: <b>{row['price']:,} so'm</b>"
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
    # WEBAPP_URL'ga har doim bitta versiya belgisi (?v=...) qo'shamiz.
    # Telegram WebApp'larni URL bo'yicha keshlaydi — versiya raqami
    # o'zgarmasa, foydalanuvchilar index.html'ni yangilagandan keyin
    # ham ESKI versiyani ko'rishda davom etishi mumkin. Shu belgi
    # tufayli Telegram uni "yangi" sahifa deb hisoblab, keshni chetlab
    # o'tadi va har doim eng so'nggi index.html'ni yuklaydi.
    _webapp_url = f"{WEBAPP_URL}{'&' if '?' in WEBAPP_URL else '?'}v={_BOOT_VERSION}"
    kb.add(InlineKeyboardButton(
        text="CLOUDE VIP PREMIUM 💎",
        web_app=WebAppInfo(url=_webapp_url)
    ))
    # "📣 E'lon" tugmasi faqat ADMIN_ID uchun ko'rsatiladi — oddiy
    # foydalanuvchilar bu tugmani umuman ko'rmaydi (PayerPin'dagidek).
    # Oddiy foydalanuvchilar uchun "Isbot kanal" xuddi shu qatorda,
    # "E'lon" turgan joyda chiqadi — ikkalasi bir qatorda, 2-rasmdagidek.
    if message.from_user.id == ADMIN_ID:
        kb.row(
            InlineKeyboardButton("📢 Bizning kanal", url="https://t.me/CLOUDE_CHEATS"),
            InlineKeyboardButton("📣 E'lon", callback_data="announce_start"),
        )
        kb.add(InlineKeyboardButton("🤖 Isbot kanal", url="https://t.me/isbotCLOUDEVIP"))
    else:
        kb.row(
            InlineKeyboardButton("📢 Bizning kanal", url="https://t.me/CLOUDE_CHEATS"),
            InlineKeyboardButton("💯 Isbot kanal", url="https://t.me/isbotCLOUDEVIP"),
        )

    photo_path = "start_banner.png"  # bot.py bilan bir xil papkada turishi kerak
    welcome_name = esc(message.from_user.first_name or "do'stim")
    with open(photo_path, "rb") as photo:
        await message.answer_photo(
            photo=photo,
            caption=f"Xush kelibsiz, <b>{welcome_name}</b> 👋",
            parse_mode="HTML",
            reply_markup=kb
        )


# ── Callback: "📣 E'lon" tugmasi bosilganda ──────────────
@dp.callback_query_handler(lambda c: c.data == "announce_start")
async def announce_start(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Bu boʻlim faqat ᴵᴬᴹ𝘾𝙇𝙊𝙐𝘿𝙀 uchun", show_alert=True)
    announce_state[call.from_user.id] = {"stage": "content"}
    await call.answer()
    cancel_kb = InlineKeyboardMarkup()
    cancel_kb.add(InlineKeyboardButton("❌ Bekor qilish", callback_data="announce_cancel"))
    await bot.send_message(
        ADMIN_ID,
        "📢 Marhamat, e'loningizni kiriting (matn, rasm+matn, video va h.k.):",
        reply_markup=cancel_kb,
    )


# ── E'lon yaratishni istalgan bosqichda bekor qilish ─────
# Tugma (callback) orqali yoki /bekor buyrug'i orqali chaqirilishi mumkin.
async def _cancel_announce(admin_id: int):
    announce_state.pop(admin_id, None)
    await bot.send_message(admin_id, "❌ E'lon yaratish bekor qilindi.")


@dp.callback_query_handler(lambda c: c.data == "announce_cancel")
async def announce_cancel_cb(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    await call.answer()
    try:
        await call.message.edit_reply_markup()
    except Exception:
        pass
    await _cancel_announce(call.from_user.id)


@dp.message_handler(
    lambda m: m.from_user.id == ADMIN_ID
              and m.text
              and m.text.strip().lower() == "/bekor"
              and m.from_user.id in announce_state,
    commands=["bekor"],
)
async def announce_cancel_cmd(message: types.Message):
    await _cancel_announce(message.from_user.id)


# ── Admin "E'lon" tugmasidan keyin yuborgan XOHLAGAN xabarini
# (matn, rasm, video va h.k.) qabul qiladi, so'ng ixtiyoriy tugma
# (masalan "Hisobni to'ldirish") qo'shishni so'raydi. MUHIM: shu handler
# quyidagi admin_confirm handleridan OLDIN turishi shart — aks holda
# masalan admin raqam yuborsa (masalan narx e'lon qilsa), admin_confirm
# handleri uni "to'lov tasdiqlash" deb tushunib qolar edi.
@dp.message_handler(
    lambda m: m.from_user.id == ADMIN_ID
              and announce_state.get(m.from_user.id, {}).get("stage") == "content",
    content_types=types.ContentType.ANY,
)
async def announce_receive_content(message: types.Message):
    if message.content_type == "text" and message.text.strip().lower() == "/bekor":
        return await _cancel_announce(message.from_user.id)
    announce_state[message.from_user.id] = {
        "stage": "button_choice",
        "chat_id": message.chat.id,
        "message_id": message.message_id,
    }
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Ha, tugma qo'shish", callback_data="announce_btn_yes"),
        InlineKeyboardButton("➡️ Yo'q, shu holida yuborish", callback_data="announce_btn_no"),
    )
    kb.add(InlineKeyboardButton("❌ Bekor qilish", callback_data="announce_cancel"))
    await message.answer(
        "🔘 Xabar ostiga tugma (masalan \"Hisobni to'ldirish\") qo'shasizmi?",
        reply_markup=kb
    )


# Yuborilgan e'lonlar tarixi — har bir broadcast uchun qaysi
# foydalanuvchiga qaysi message_id bilan yuborilgani saqlanadi, shunda
# keyinchalik "/elon_ochir <ID>" orqali hammadan o'chirish mumkin bo'ladi.
# DIQQAT: bu xotirada (RAM'da) saqlanadi — bot qayta ishga tushsa
# (masalan Render qayta deploy qilsa), bu tarix o'chib ketadi va eski
# e'lonlarni endi shu buyruq bilan o'chirib bo'lmaydi.
announce_history = {}
_announce_id_counter = 0


async def _do_announce_broadcast(admin_id: int, state: dict, reply_markup=None):
    """Saqlangan e'lon xabarini (state['chat_id']/['message_id']) barcha
    foydalanuvchilarga nusxalab yuboradi. Agar reply_markup berilgan
    bo'lsa, har bir nusxaga o'sha tugma ham qo'shiladi."""
    global _announce_id_counter
    user_ids = db.get_all_user_ids()
    status = await bot.send_message(admin_id, f"⏳ E'lon yuborilmoqda... (0/{len(user_ids)})")

    sent, blocked, failed = 0, 0, 0
    delivered = []  # (user_id, message_id) — keyinchalik o'chirish uchun
    for i, uid in enumerate(user_ids, start=1):
        try:
            copied = await bot.copy_message(
                chat_id=uid,
                from_chat_id=state["chat_id"],
                message_id=state["message_id"],
                reply_markup=reply_markup,
            )
            delivered.append((uid, copied.message_id))
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

    _announce_id_counter += 1
    announce_id = _announce_id_counter
    announce_history[announce_id] = delivered

    await status.edit_text(
        f"✅ E'lon yuborildi. (ID: {announce_id})\n"
        f"Jami: {len(user_ids)}\n"
        f"Yuborildi: {sent}\n"
        f"Bloklagan/o'chirilgan: {blocked}\n"
        f"Xatolik: {failed}\n\n"
        f"❗️Xato ketgan bo'lsa, buni hammadan o'chirish uchun:\n"
        f"/elon_ochir {announce_id}"
    )


@dp.message_handler(commands=["elon_ochir"])
async def announce_delete(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.get_args().strip()
    if not args.isdigit() or int(args) not in announce_history:
        ids = ", ".join(str(i) for i in sorted(announce_history.keys(), reverse=True)[:10])
        await message.answer(
            "⚠️ Foydalanish: /elon_ochir <ID>\n"
            + (f"Mavjud (oxirgi) ID'lar: {ids}" if ids else "Hozircha o'chirish mumkin bo'lgan e'lon yo'q.")
        )
        return

    announce_id = int(args)
    delivered = announce_history[announce_id]
    status = await message.answer(f"⏳ E'lon (ID: {announce_id}) o'chirilmoqda... (0/{len(delivered)})")

    deleted, failed = 0, 0
    for i, (uid, msg_id) in enumerate(delivered, start=1):
        try:
            await bot.delete_message(chat_id=uid, message_id=msg_id)
            deleted += 1
        except Exception:
            # Foydalanuvchi bloklagan, xabar allaqachon o'chirilgan yoki
            # 48 soatdan oshib ketgan bo'lishi mumkin — bularni o'tkazib
            # yuboramiz, jarayon davom etadi.
            failed += 1
        await asyncio.sleep(0.05)
        if i % 25 == 0:
            try:
                await status.edit_text(f"⏳ O'chirilmoqda... ({i}/{len(delivered)})")
            except Exception:
                pass

    announce_history.pop(announce_id, None)
    await status.edit_text(
        f"✅ E'lon (ID: {announce_id}) o'chirildi.\n"
        f"O'chirildi: {deleted}\n"
        f"O'chirilmadi (bloklagan/eski/allaqachon o'chirilgan): {failed}"
    )


@dp.callback_query_handler(lambda c: c.data == "announce_btn_no")
async def announce_btn_no(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    state = announce_state.pop(call.from_user.id, None)
    await call.answer()
    await call.message.edit_reply_markup()
    if not state:
        return
    await _do_announce_broadcast(call.from_user.id, state)


@dp.callback_query_handler(lambda c: c.data == "announce_btn_yes")
async def announce_btn_yes(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    state = announce_state.get(call.from_user.id)
    await call.answer()
    await call.message.edit_reply_markup()
    if not state:
        return
    state["stage"] = "button_text"
    await bot.send_message(call.from_user.id, "✏️ Tugma matnini yuboring (masalan: Hisobni to'ldirish):")


@dp.message_handler(
    lambda m: m.from_user.id == ADMIN_ID
              and announce_state.get(m.from_user.id, {}).get("stage") == "button_text"
              and m.text,
)
async def announce_receive_button_text(message: types.Message):
    if message.text.strip().lower() == "/bekor":
        return await _cancel_announce(message.from_user.id)
    announce_state[message.from_user.id]["btn_text"] = message.text.strip()
    announce_state[message.from_user.id]["stage"] = "button_url"
    await message.answer(
        "🔗 Endi tugma bosilganda ochiladigan havolani yuboring "
        "(masalan: https://t.me/CLOUDE_CHEATS yoki webapp havolasi):\n\n"
        "Bekor qilish uchun /bekor yozing."
    )


@dp.message_handler(
    lambda m: m.from_user.id == ADMIN_ID
              and announce_state.get(m.from_user.id, {}).get("stage") == "button_url"
              and m.text,
)
async def announce_receive_button_url(message: types.Message):
    if message.text.strip().lower() == "/bekor":
        return await _cancel_announce(message.from_user.id)
    state = announce_state.pop(message.from_user.id, None)
    url = message.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await message.answer("⚠️ Havola http:// yoki https:// bilan boshlanishi kerak. Qaytadan urinib ko'ring — /start dan boshlang.")
        return

    kb = InlineKeyboardMarkup()
    # Agar havola bizning WebApp (Mini App) domenimizga tegishli bo'lsa,
    # tugmani web_app sifatida yaratamiz — shunda u Telegram ICHIDA
    # (PayerPin'dagidek) ochiladi, tashqi brauzerga chiqmaydi.
    # Domenni solishtiramiz (http/https, oxiridagi "/" farqiga qaramay),
    # chunki oldingi versiyada satr boshini solishtirish orqali kichik
    # farqlar (masalan trailing slash) tugmani noto'g'ri url turida
    # qoldirib yuborishi mumkin edi.
    same_domain = urlparse(url).netloc.lower() == urlparse(WEBAPP_URL).netloc.lower()
    logging.info(
        f"[Announce] url={url!r} WEBAPP_URL={WEBAPP_URL!r} "
        f"url_domain={urlparse(url).netloc!r} webapp_domain={urlparse(WEBAPP_URL).netloc!r} "
        f"same_domain={same_domain}"
    )
    if same_domain:
        kb.add(InlineKeyboardButton(state["btn_text"], web_app=WebAppInfo(url=url)))
    else:
        kb.add(InlineKeyboardButton(state["btn_text"], url=url))
    await _do_announce_broadcast(message.from_user.id, state, reply_markup=kb)


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


# ── Callback: UC buyurtmani botdan tasdiqlash (endi admin panelga
# kirmasdan, to'g'ridan-to'g'ri shu yerdan tasdiqlash mumkin) ────────────
@dp.callback_query_handler(lambda c: c.data.startswith("ucorder_ok:"))
async def ucorder_ok(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    order_id = int(call.data.split(":")[1])
    ok = db.approve_uc_order(order_id)
    if not ok:
        await call.answer("⚠️ Bu buyurtma allaqachon ko'rib chiqilgan yoki topilmadi")
        await call.message.edit_reply_markup()
        return
    row = db.get_uc_order(order_id)
    await call.message.edit_reply_markup()
    await call.answer("✅ Tasdiqlandi")
    try:
        await bot.send_message(
            call.from_user.id,
            format_uc_order_message(row, "Amalga oshirilgan"),
            parse_mode="HTML"
        )
    except Exception:
        pass


# ── Callback: UC buyurtmani botdan bekor qilish (pul avtomatik
# foydalanuvchiga qaytariladi — db.cancel_uc_order ichida) ──────────────
@dp.callback_query_handler(lambda c: c.data.startswith("ucorder_cancel:"))
async def ucorder_cancel(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await call.answer("❌ Ruxsat yo'q")
    order_id = int(call.data.split(":")[1])
    result = db.cancel_uc_order(order_id)
    if not result:
        await call.answer("⚠️ Bu buyurtma allaqachon ko'rib chiqilgan yoki topilmadi")
        await call.message.edit_reply_markup()
        return
    row = db.get_uc_order(order_id)
    await call.message.edit_reply_markup()
    await call.answer("❌ Bekor qilindi, pul qaytarildi")
    try:
        await bot.send_message(
            call.from_user.id,
            format_uc_order_message(row, "Rad etilgan"),
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


# ── Start ────────────────────────────────────────────────
if __name__ == "__main__":
    db.init_db()
    executor.start_polling(dp, skip_updates=True)
    
