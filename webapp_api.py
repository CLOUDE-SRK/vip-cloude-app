"""
Webapp <-> Bot server (FastAPI)
"""
import os
import hmac
import hashlib
import json
import time
import uuid
import logging
import httpx
from urllib.parse import parse_qsl
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import database as db

app = FastAPI()


@app.on_event("startup")
async def _init_database():
    # MUHIM: avval jadvallar faqat bot.py ishga tushganda (agar u umuman
    # ishga tushirilgan bo'lsa) yaratilar edi. Agar webapp_api.py alohida
    # xizmat sifatida (bot.py'siz) ishga tushsa, jadvallar hali yo'q bo'lib,
    # birinchi so'rovlardayoq xatolik berardi. Endi bu yerda ham chaqiriladi —
    # init_db() ichidagi "CREATE TABLE IF NOT EXISTS" xavfsiz, bir necha marta
    # chaqirilsa ham hech narsani buzmaydi.
    db.init_db()

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
VIP_CHAT_ID = int(os.environ["VIP_CHAT_ID"])

ADMIN_BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# DIQQAT: Ma'lumotlar bazasi (balans, top, buyurtmalar) Supabase Postgres'da
# saqlanadi. Rasmlar (profil avatarlari, vazifa logotiplari) endi ALOHIDA —
# Supabase STORAGE'da saqlanadi (ma'lumotlar bazasi emas, alohida fayl
# xizmati). Bu ham Render konteynerdan mustaqil, shuning uchun deploy/
# restart/spin-down endi rasmlarga ham ta'sir qilmaydi.
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SUPABASE_STORAGE_BUCKET = "vip-cloude-uploads"


async def _supabase_storage_upload(path: str, contents: bytes, content_type: str) -> str:
    """Faylni Supabase Storage'ga yuklaydi va uning ommaviy (public) URL'ini
    qaytaradi. Bucket 'public' deb sozlangan bo'lishi SHART (Supabase
    dashboard -> Storage -> bucket yaratganda "Public bucket" belgisini
    yoqish), aks holda qaytgan URL orqali rasm ko'rinmaydi."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_STORAGE_BUCKET}/{path}",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": content_type,
            },
            content=contents,
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Rasmni Supabase Storage'ga yuklab bo'lmadi: {resp.status_code} {resp.text[:200]}"
        )
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_STORAGE_BUCKET}/{path}"


async def _supabase_storage_delete(path: str):
    """Eski rasmni Supabase Storage'dan o'chiradi. Xato bo'lsa e'tiborsiz
    qoldiramiz (masalan fayl allaqachon yo'q bo'lsa) — bu asosiy amalni
    to'xtatmasligi kerak."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.request(
                "DELETE",
                f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_STORAGE_BUCKET}/{path}",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                },
            )
    except Exception:
        pass


def _supabase_path_from_public_url(url: str) -> Optional[str]:
    """To'liq public URL'dan Storage ichidagi nisbiy yo'lni ajratib oladi
    (masalan eski avatarni o'chirish uchun kerak bo'ladi)."""
    marker = f"/storage/v1/object/public/{SUPABASE_STORAGE_BUCKET}/"
    if url and marker in url:
        return url.split(marker, 1)[1]
    return None



ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# ---------------------------------------------------------------
# HTML FAYLLARNI SERVERDAN TO'G'RIDAN-TO'G'RI CHIQARISH
# (bot.py dagi WEBAPP_URL va admin_bot.py dagi ADMIN_WEBAPP_URL
#  shu manzillardan biriga ishora qiladi — qaysi manzil ishlatilishidan
#  qat'iy nazar ishlashi uchun bir nechta variant qo'shilgan)
# ---------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.get("/")
async def serve_index_root():
    return FileResponse(os.path.join(_BASE_DIR, "index.html"))


@app.get("/index.html")
async def serve_index_html():
    return FileResponse(os.path.join(_BASE_DIR, "index.html"))


@app.get("/admin")
async def serve_admin_no_ext():
    return FileResponse(os.path.join(_BASE_DIR, "admin.html"))


@app.get("/admin.html")
async def serve_admin_html():
    return FileResponse(os.path.join(_BASE_DIR, "admin.html"))
MAX_AVATAR_BYTES = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------
# SERVERDAGI QATTIQ NARX RO'YXATLARI
# (index.html dagi UC_PACKAGES / VIP kartalar bilan bir xil bo'lishi SHART —
#  frontendda narx o'zgarsa, bu yerda ham albatta yangilang!)
# ---------------------------------------------------------------

VIP_PACKAGES = {
    "15kun":    {"name": "15 kunlik",  "base_price": 30000},
    "1oy":      {"name": "1 oylik",    "base_price": 60000},
    "1sezon":   {"name": "1 sezon",    "base_price": 100000},
    "vipsezon": {"name": "VIP sezon",  "base_price": 299000},
}

UC_PACKAGES = [
    {"uc": 60,   "price": 11999},
    {"uc": 120,  "price": 24999},
    {"uc": 180,  "price": 39000},
    {"uc": 325,  "price": 59000},
    {"uc": 660,  "price": 115000},
    {"uc": 1800, "price": 299000},
    {"uc": 3830, "price": 569000},
    {"uc": 8100, "price": 1199000},
]

# bot.py dagi TASK_REWARDS bilan bir xil bo'lishi kerak
TASK_REWARDS = {"tg": 5000, "ig": 3000, "yt": 3000}
# Adminga ko'rsatiladigan nom ham serverda qattiq belgilanadi — aks holda
# body.task_title orqali istalgan matn (hatto HTML) admin xabariga
# kirib ketishi mumkin edi (parse_mode="HTML" bilan yuboriladi).
TASK_NAMES = {"tg": "Telegram kanalga a'zolik", "ig": "Instagram", "yt": "YouTube"}


def get_ref_discount(refs: int) -> int:
    """Frontenddagi getRefDiscount() bilan bir xil mantiq."""
    if refs >= 30:
        return 15
    if refs >= 20:
        return 10
    if refs >= 10:
        return 5
    return 0


def _verify_init_data_with_token(init_data: str, bot_token: str) -> Optional[dict]:
    if not bot_token:
        return None
    vals = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = vals.pop("hash", None)
    if not received_hash:
        return None

    auth_date = vals.get("auth_date")
    if auth_date:
        try:
            if time.time() - int(auth_date) > 86400:
                return None
        except ValueError:
            pass

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(vals.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        return None

    user_json = vals.get("user", "{}")
    try:
        return json.loads(user_json)
    except Exception:
        return None


def verify_init_data(init_data: str) -> Optional[dict]:
    return _verify_init_data_with_token(init_data, BOT_TOKEN)


def verify_admin_init_data(init_data: str) -> Optional[dict]:
    user = _verify_init_data_with_token(init_data, ADMIN_BOT_TOKEN)
    if not user:
        return None
    if user.get("id") != ADMIN_ID:
        return None
    return user


def require_admin(request: Request) -> dict:
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_admin_init_data(init_data)
    if not user:
        raise HTTPException(status_code=403, detail="Ruxsat yo'q")
    return user


@app.get("/balance")
async def get_balance(request: Request):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    db.ensure_user(user_id, user.get("username"), user.get("first_name"))
    balance = db.get_balance(user_id)

    tasks_done = {}
    for key in ["tg", "ig", "yt"]:
        row = db.get_task(user_id, key)
        tasks_done[key] = (row and row["status"] == "done")

    conn = db.get_conn()
    refs = conn.execute("SELECT refs FROM users WHERE user_id=?", (user_id,)).fetchone()
    # Admin qo'shgan custom vazifalar ham "tasks" jadvalida "custom_<id>" kaliti bilan
    # saqlanadi (approve_verify_request shunday yozadi). Buni ham qaytarmasak,
    # webapp har safar tugmani "Bajardim" holatiga qaytarib qo'yadi va foydalanuvchi
    # bir xil vazifani qayta-qayta yuborib, bir necha marta mukofot olishi mumkin bo'ladi.
    custom_done_rows = conn.execute(
        "SELECT task_key FROM tasks WHERE user_id=? AND status='done' AND task_key LIKE 'custom\\_%' ESCAPE '\\'",
        (user_id,)
    ).fetchall()
    for row in custom_done_rows:
        tasks_done[row["task_key"]] = True
    conn.close()
    refs_count = refs["refs"] if refs else 0

    profile = db.get_profile_admin(user_id)
    is_premium = db.is_premium_active(user_id)

    return {
        "balance": balance,
        "tasks_done": tasks_done,
        "refs": refs_count,
        "is_premium": is_premium,
        "premium_until": profile["premium_until"] if profile else None,
        "avatar_url": profile["avatar_url"] if profile else None
    }


@app.get("/history")
async def get_history(request: Request):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    items = db.get_user_history(user_id)
    return items


class WheelJoinBody(BaseModel):
    bet_amount: int


@app.post("/wheel/join")
async def wheel_join(request: Request, body: WheelJoinBody):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    db.ensure_user(user_id, user.get("username"), user.get("first_name"))

    result = db.join_wheel_room(user_id, user.get("first_name"), body.bet_amount)
    if "error" in result:
        code_map = {
            "invalid_amount": 400,
            "insufficient_balance": 400,
            "already_in_room": 409,
        }
        raise HTTPException(status_code=code_map.get(result["error"], 400), detail=result["error"])
    return result


@app.get("/wheel/room/{room_id}")
async def wheel_room_status(room_id: int, request: Request):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    room = db.get_wheel_room(room_id, user["id"])
    if not room:
        raise HTTPException(status_code=404, detail="room_not_found")
    # Admin (ADMIN_ID) uchun frontendda ismni qisqartirmaslik uchun
    # har bir o'yinchiga is_admin belgisini shu yerda (serverda)
    # qo'shamiz - admin ID mijoz tomonda hech qachon oshkor qilinmaydi.
    for p in room["players"]:
        p["is_admin"] = (p.get("user_id") == ADMIN_ID)
    return room


class WheelLeaveBody(BaseModel):
    room_id: int


@app.post("/wheel/leave")
async def wheel_leave(request: Request, body: WheelLeaveBody):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    result = db.leave_wheel_room(user["id"], body.room_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class WheelForceSpinBody(BaseModel):
    room_id: int


@app.post("/wheel/force_spin")
async def wheel_force_spin(request: Request, body: WheelForceSpinBody):
    # DIQQAT: bu endpoint frontendda hech kimga (oddiy foydalanuvchilarga)
    # ko'rinadigan tarzda belgilanmagan - charxpalak markazidagi hub HAR
    # QANDAY foydalanuvchi uchun bir xil ko'rinadi va bir xil onclick'ga
    # ega. Faqat shu yerda, serverda, chaqiruvchi ADMIN_ID bilan mos
    # kelishi tekshiriladi. Mos kelmasa - hech qanday maxsus xato kodi
    # yoki signal qaytarilmaydi, shunchaki "ok: False" - bu shubha
    # uyg'otmasligi uchun.
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user or user.get("id") != ADMIN_ID:
        return {"ok": False}

    result = db.force_start_wheel_room(body.room_id)
    return {"ok": "error" not in result}


@app.get("/wheel/counts")
async def wheel_counts(request: Request):
    """Har bir tikish miqdori uchun kutish xonasida nechta o'yinchi
    borligini qaytaradi - foydalanuvchi hali qo'shilmasdan turib qaysi
    summada o'yinchi ko'proq ekanini ko'rishi uchun."""
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    return db.get_wheel_waiting_counts()


@app.get("/wheel/active")
async def wheel_active(request: Request):
    """Sahifa qayta ochilganda foydalanuvchi hali kutish/tayyor xonasida
    turgan bo'lsa, o'sha xonaga qaytarish uchun."""
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    room_id = db.get_user_active_wheel_room(user["id"])
    return {"room_id": room_id}


@app.get("/leaderboard")
async def get_leaderboard():
    conn = db.get_conn()
    rows = conn.execute("""
        SELECT user_id, first_name, username, refs, avatar_url
        FROM users
        WHERE refs > 0
        ORDER BY refs DESC
        LIMIT 10
    """).fetchall()
    conn.close()

    result = []
    for row in rows:
        name = row["first_name"] or row["username"] or "Foydalanuvchi"
        initials = name[:2].upper()
        result.append({
            "user_id": row["user_id"],
            "name": name,
            "initials": initials,
            "refs": row["refs"],
            "avatar_url": row["avatar_url"],
            "is_premium": db.is_premium_active(row["user_id"])
        })

    return result


@app.get("/custom_tasks")
async def list_custom_tasks():
    rows = db.get_custom_tasks(active_only=True)
    return [{
        "id": r["id"], "title": r["title"], "reward": r["reward"],
        "url": r["url"], "icon_url": r["icon_url"]
    } for r in rows]


class VerifyRequestBody(BaseModel):
    task_key: str
    task_title: str
    custom_task_id: Optional[int] = None  # agar bu admin qo'shgan custom vazifa bo'lsa


@app.post("/task/verify_request")
async def task_verify_request(request: Request, body: VerifyRequestBody):
    """Foydalanuvchi biror vazifani 'bajardim' deganda shu yerga so'rov
    yuboradi, admin panelda ko'rinadi va admin tasdiqlaydi/bekor qiladi."""
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    db.ensure_user(user_id, user.get("username"), user.get("first_name"))

    # XAVFSIZLIK: agar bu maxsus (custom) vazifa bo'lsa, task_key va
    # task_title ni CLIENTGA ISHONIB OLMAYMIZ — ular butunlay o'ylab
    # topilgan bo'lishi mumkin edi. Avval bu ikkisi to'g'ridan-to'g'ri
    # client'dan olinar edi, shuning uchun foydalanuvchi bir xil
    # custom_task_id uchun har safar boshqacha task_key yuborib
    # (masalan "custom_1", "custom_1x", "custom_1xx"...), tizimni har
    # birini "yangi vazifa" deb aldab, mukofotni cheksiz marta olishi
    # mumkin edi. Endi ikkalasi ham SERVERDA, custom_task_id orqali
    # bazadan olingan haqiqiy qiymatlardan hosil qilinadi.
    task_key = body.task_key
    task_title = body.task_title
    reward = None

    if body.custom_task_id is not None:
        rows = db.get_custom_tasks(active_only=True)
        match = next((r for r in rows if r["id"] == body.custom_task_id), None)
        if not match:
            raise HTTPException(status_code=400, detail="Vazifa topilmadi yoki faol emas")
        task_key = f"custom_{body.custom_task_id}"   # kanonik, o'zgarmas kalit
        task_title = match["title"]                    # adminga haqiqiy nom ko'rsatiladi
        reward = match["reward"]

    # Vazifa allaqachon bajarilgan bo'lsa yoki admin javobini kutayotgan bo'lsa,
    # qayta so'rov yaratmaymiz — aks holda foydalanuvchi bir xil vazifa uchun
    # bir necha marta mukofot olishi mumkin bo'lardi.
    existing_task = db.get_task(user_id, task_key)
    if existing_task and existing_task["status"] == "done":
        raise HTTPException(status_code=400, detail="Bu vazifa allaqachon bajarilgan")
    if db.has_pending_verify_request(user_id, task_key):
        raise HTTPException(status_code=400, detail="Bu vazifa admin tasdiqini kutmoqda")

    # Oddiy (tg/ig/yt) vazifalar uchun mukofot va nomni SERVERDA aniqlaymiz
    if reward is None:
        reward = TASK_REWARDS.get(task_key)
        if reward is None:
            raise HTTPException(status_code=400, detail="Noma'lum vazifa")
        task_title = TASK_NAMES.get(task_key, task_key)

    req_id = db.create_verify_request(user_id, task_key, task_title, reward)

    try:
        import httpx
        admin_text = (
            f"📋 <b>Yangi vazifa so'rovi (#{req_id})</b>\n\n"
            f"👤 <a href='tg://user?id={user_id}'>{user.get('first_name','Foydalanuvchi')}</a>\n"
            f"🆔 TG ID: <code>{user_id}</code>\n"
            f"✅ Vazifa: <b>{task_title}</b>\n"
            f"💰 Mukofot: <b>{reward:,} so'm</b>\n\n"
            f"Admin panelda ko'rib, tasdiqlang."
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_ID, "text": admin_text, "parse_mode": "HTML"}
            )
    except Exception:
        pass

    return {"ok": True, "request_id": req_id}


@app.post("/profile/avatar")
async def set_profile_avatar(request: Request, file: UploadFile = File(...)):
    """Faqat premium foydalanuvchilar galereyadan rasm yuklab profil rasm qo'ya oladi."""
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    if not db.is_premium_active(user_id):
        raise HTTPException(status_code=403, detail="Faqat premium foydalanuvchilar uchun")

    ext = ALLOWED_IMAGE_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="Faqat rasm fayllari (jpg, png, webp, gif) qabul qilinadi")

    contents = await file.read()
    if len(contents) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="Rasm hajmi 5MB dan oshmasligi kerak")

    filename = f"{user_id}_{uuid.uuid4().hex}{ext}"
    avatar_url = await _supabase_storage_upload(f"avatars/{filename}", contents, file.content_type)

    # Eski rasmni o'chiramiz (agar bo'lsa)
    old_profile = db.get_profile_admin(user_id)
    old_url = old_profile["avatar_url"] if old_profile else None

    ok = db.set_avatar(user_id, avatar_url)
    if not ok:
        await _supabase_storage_delete(f"avatars/{filename}")
        raise HTTPException(status_code=403, detail="Faqat premium foydalanuvchilar uchun")

    old_path = _supabase_path_from_public_url(old_url) if old_url else None
    if old_path:
        await _supabase_storage_delete(old_path)

    return {"ok": True, "avatar_url": avatar_url}


class VipOrderRequest(BaseModel):
    vip_key: str   # masalan: "15kun", "1oy", "1sezon", "vipsezon"


@app.post("/buy_vip")
async def buy_vip(request: Request, body: VipOrderRequest):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    db.ensure_user(user_id, user.get("username"), user.get("first_name"))

    pkg = VIP_PACKAGES.get(body.vip_key)
    if not pkg:
        raise HTTPException(status_code=400, detail="Noto'g'ri paket")

    # Referal chegirmasini SERVERDAGI refs soniga qarab hisoblaymiz
    conn = db.get_conn()
    row = conn.execute("SELECT refs FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    refs = row["refs"] if row else 0
    discount = get_ref_discount(refs)

    final_price = round(pkg["base_price"] * (1 - discount / 100))

    ok = db.deduct_balance(user_id, final_price)
    if not ok:
        raise HTTPException(status_code=400, detail="Balans yetarli emas")

    order_id = db.create_vip_order(user_id, pkg["name"], final_price)

    try:
        import httpx
        from datetime import datetime, timedelta, timezone

        expire_ts = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())

        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/unbanChatMember",
                json={"chat_id": VIP_CHAT_ID, "user_id": user_id}
            )

            link_res = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/createChatInviteLink",
                json={
                    "chat_id": VIP_CHAT_ID,
                    "member_limit": 1,
                    "expire_date": expire_ts
                }
            )
            link_data = link_res.json()

            if link_data.get("ok"):
                invite_link = link_data["result"]["invite_link"]
                await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": user_id,
                        "text": (
                            f"✅ <b>{pkg['name']}</b> faollashtirildi!\n\n"
                            f"👇 VIP kanalga kirish uchun tugmani bosing:"
                        ),
                        "parse_mode": "HTML",
                        "reply_markup": {
                            "inline_keyboard": [[
                                {"text": "💎 VIP Kanalga kirish", "url": invite_link}
                            ]]
                        }
                    }
                )
    except Exception:
        pass

    try:
        import httpx
        admin_text = (
            f"👑 <b>Yangi VIP xarid</b>\n\n"
            f"👤 <a href='tg://user?id={user_id}'>{user.get('first_name','Foydalanuvchi')}</a>\n"
            f"🆔 TG ID: <code>{user_id}</code>\n"
            f"📦 Paket: <b>{pkg['name']}</b>\n"
            f"💵 Narxi: <b>{final_price:,} so'm</b>" + (f" ({discount}% chegirma)" if discount else "")
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_ID, "text": admin_text, "parse_mode": "HTML"}
            )
    except Exception:
        pass

    return {"ok": True, "order_id": order_id, "price": final_price}


class UcOrderRequest(BaseModel):
    player_id: str
    package_index: int   # UC_PACKAGES ro'yxatidagi indeks (0-7)


@app.post("/uc_order")
async def uc_order(request: Request, body: UcOrderRequest):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    first_name = user.get("first_name", "Foydalanuvchi")
    username = user.get("username", "")

    db.ensure_user(user_id, username, first_name)

    if body.package_index < 0 or body.package_index >= len(UC_PACKAGES):
        raise HTTPException(status_code=400, detail="Noto'g'ri paket")

    pkg = UC_PACKAGES[body.package_index]
    uc_amount = pkg["uc"]
    price = pkg["price"]

    pubg_id = (body.player_id or "").strip()
    if not pubg_id:
        raise HTTPException(status_code=400, detail="PUBG ID kiritilmagan")

    ok = db.deduct_balance(user_id, price)
    if not ok:
        raise HTTPException(status_code=400, detail="Balans yetarli emas")

    order_id = db.create_uc_order(user_id, pubg_id, uc_amount, price, 0)

    try:
        import httpx
        admin_text = (
            f"🎮 <b>Yangi UC buyurtma (#{order_id})</b>\n\n"
            f"👤 <a href='tg://user?id={user_id}'>{first_name}</a>\n"
            f"🆔 TG ID: <code>{user_id}</code>\n"
            f"🕹 PUBG ID: <code>{pubg_id}</code>\n"
            f"💎 UC: <b>{uc_amount:,} UC</b>\n"
            f"💵 Narxi: <b>{price:,} so'm</b>\n\n"
            f"Admin panelda ko'rib, tasdiqlang."
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_ID, "text": admin_text, "parse_mode": "HTML"}
            )
    except Exception:
        pass

    return {"ok": True, "order_id": order_id, "uc": uc_amount, "price": price}


class EarnTapRequest(BaseModel):
    amount: int
    boost_active: bool = False


@app.post("/earn_tap")
async def earn_tap(request: Request, body: EarnTapRequest):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    db.ensure_user(user_id, user.get("username"), user.get("first_name"))

    amount = max(0, min(body.amount, 500))
    result = db.add_earn_tap(user_id, amount, cap=100, boost_active=body.boost_active)

    return {
        "ok": True,
        "added": result["added"],
        "balance": result["balance"],
        "earn_today": result["earn_today"],
        "cap": result["cap"],
        "premium": result["premium"]
    }


# ── ADMIN PANEL: 1) BUYURTMALAR ───────────────────────────────
@app.get("/admin/orders")
async def admin_list_orders(request: Request, status: str = "pending"):
    require_admin(request)

    if status == "all":
        rows = db.get_recent_uc_orders(limit=50)
    else:
        rows = db.get_pending_uc_orders()

    result = []
    for r in rows:
        name = r["first_name"] or r["username"] or "Foydalanuvchi"
        result.append({
            "id": r["id"],
            "user_id": r["user_id"],
            "name": name,
            "username": r["username"],
            "pubg_id": r["pubg_id"],
            "uc_amount": r["uc_amount"],
            "price": r["price"],
            "status": r["status"],
            "created_at": r["created_at"],
        })
    return result


@app.post("/admin/orders/{order_id}/approve")
async def admin_approve_order(order_id: int, request: Request):
    require_admin(request)

    order = db.get_uc_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Buyurtma topilmadi")
    if order["status"] != "pending":
        raise HTTPException(status_code=400, detail="Bu buyurtma allaqachon ko'rib chiqilgan")

    db.approve_uc_order(order_id)
    return {"ok": True}


@app.post("/admin/orders/{order_id}/cancel")
async def admin_cancel_order(order_id: int, request: Request):
    require_admin(request)

    # XAVFSIZLIK: db.cancel_uc_order() ENDI pulni o'zi, bitta atomik
    # tranzaksiyada qaytaradi (database.py da tuzatildi). Bu yerda
    # qo'shimcha db.add_balance() chaqirish PULNI IKKI MARTA
    # QAYTARIB YUBORAR EDI — shuning uchun olib tashlandi.
    result = db.cancel_uc_order(order_id)
    if not result:
        raise HTTPException(status_code=400, detail="Buyurtma topilmadi yoki allaqachon ko'rib chiqilgan")

    return {"ok": True}


# ── ADMIN PANEL: 2) REFERAL SO'ROVLARI (vazifa tasdiqlash) ────
@app.get("/admin/verify_requests")
async def admin_list_verify_requests(request: Request, status: str = "pending"):
    require_admin(request)
    rows = db.get_pending_verify_requests()
    result = []
    for r in rows:
        name = r["first_name"] or r["username"] or "Foydalanuvchi"
        result.append({
            "id": r["id"],
            "user_id": r["user_id"],
            "name": name,
            "task_key": r["task_key"],
            "task_title": r["task_title"],
            "reward": r["reward"],
            "status": r["status"],
            "created_at": r["created_at"],
        })
    return result


@app.post("/admin/verify_requests/{request_id}/approve")
async def admin_approve_verify_request(request_id: int, request: Request):
    require_admin(request)
    req = db.approve_verify_request(request_id)
    if not req:
        raise HTTPException(status_code=400, detail="So'rov topilmadi yoki allaqachon ko'rib chiqilgan")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": req["user_id"],
                    "text": f"✅ \"{req['task_title']}\" vazifangiz tasdiqlandi! +{req['reward']:,} so'm balansingizga qo'shildi."
                }
            )
    except Exception:
        pass

    return {"ok": True}


@app.post("/admin/verify_requests/{request_id}/reject")
async def admin_reject_verify_request(request_id: int, request: Request):
    require_admin(request)
    ok = db.reject_verify_request(request_id)
    if not ok:
        raise HTTPException(status_code=400, detail="So'rov topilmadi yoki allaqachon ko'rib chiqilgan")
    return {"ok": True}


# ── ADMIN PANEL: 3) VAZIFALAR (custom tasks CRUD) ──────────────
@app.get("/admin/custom_tasks")
async def admin_list_custom_tasks(request: Request):
    require_admin(request)
    rows = db.get_custom_tasks(active_only=False)
    return [{
        "id": r["id"], "title": r["title"], "reward": r["reward"],
        "url": r["url"], "icon_url": r["icon_url"],
        "active": bool(r["active"]), "created_at": r["created_at"]
    } for r in rows]


class CustomTaskBody(BaseModel):
    title: str
    reward: int
    url: Optional[str] = None
    icon_url: Optional[str] = None


@app.post("/admin/custom_tasks")
async def admin_create_custom_task(request: Request, body: CustomTaskBody):
    require_admin(request)
    tid = db.create_custom_task(body.title, body.reward, body.url, body.icon_url)
    return {"ok": True, "id": tid}


@app.post("/admin/custom_tasks/icon")
async def admin_upload_task_icon(request: Request, file: UploadFile = File(...)):
    require_admin(request)

    ext = ALLOWED_IMAGE_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="Faqat rasm fayllari qabul qilinadi (jpg/png/webp/gif)")

    contents = await file.read()
    if len(contents) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="Fayl hajmi 5 MB dan katta bo'lmasligi kerak")

    filename = f"{uuid.uuid4().hex}{ext}"
    icon_url = await _supabase_storage_upload(f"task_icons/{filename}", contents, file.content_type)
    return {"ok": True, "icon_url": icon_url}


@app.post("/admin/custom_tasks/{task_id}/deactivate")
async def admin_deactivate_custom_task(task_id: int, request: Request):
    require_admin(request)
    db.deactivate_custom_task(task_id)
    return {"ok": True}


# ── ADMIN PANEL: 4) TOP O'YINCHILAR / RAQOBAT ──────────────────
@app.get("/admin/top_players")
async def admin_top_players(request: Request):
    require_admin(request)
    rows = db.get_top_players(limit=10)
    result = []
    for r in rows:
        name = r["first_name"] or r["username"] or "Foydalanuvchi"
        result.append({
            "user_id": r["user_id"],
            "name": name,
            "refs": r["refs"]
        })
    return result


@app.post("/admin/reset_competition")
async def admin_reset_competition(request: Request):
    require_admin(request)
    db.reset_competition()
    return {"ok": True}


# ── ADMIN PANEL: 5) PROFIL (qidiruv, pul qo'shish, premium) ───
@app.get("/admin/profile/{user_id}")
async def admin_get_profile(user_id: int, request: Request):
    require_admin(request)
    row = db.get_profile_admin(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    return {
        "user_id": row["user_id"],
        "name": row["first_name"] or row["username"] or "Foydalanuvchi",
        "username": row["username"],
        "balance": row["balance"],
        "refs": row["refs"],
        "is_premium": db.is_premium_active(user_id),
        "premium_until": row["premium_until"],
        "avatar_url": row["avatar_url"],
    }


class AddBalanceBody(BaseModel):
    amount: int


@app.post("/admin/profile/{user_id}/add_balance")
async def admin_add_balance(user_id: int, request: Request, body: AddBalanceBody):
    require_admin(request)
    row = db.get_profile_admin(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    db.add_balance(user_id, body.amount)

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": user_id,
                    "text": f"💰 Hisobingizga {body.amount:,} so'm qo'shildi."
                }
            )
    except Exception:
        pass

    return {"ok": True, "balance": db.get_balance(user_id)}


class GiftPremiumBody(BaseModel):
    duration_key: str  # "15kun", "1oy", "1sezon"


@app.post("/admin/profile/{user_id}/gift_premium")
async def admin_gift_premium(user_id: int, request: Request, body: GiftPremiumBody):
    require_admin(request)
    row = db.get_profile_admin(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    new_until = db.gift_premium(user_id, body.duration_key)
    if new_until is None:
        raise HTTPException(status_code=400, detail="Noto'g'ri muddat")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": user_id,
                    "text": "🎁 Sizga Premium status sovg'a qilindi!"
                }
            )
    except Exception:
        pass

    return {"ok": True, "premium_until": new_until}
