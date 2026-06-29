"""
Webapp <-> Bot server (FastAPI)
Webapp bu serverga so'rov qilib foydalanuvchi balansini oladi va
UC buyurtmalarini yuboradi. Admin panel ham shu server orqali ishlaydi.
"""
import os
import hmac
import hashlib
import json
import time
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import database as db

app = FastAPI()

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])

# Admin panel uchun ALOHIDA bot tokeni (admin_bot.py shu yerdagi
# tokendan foydalanadi). Hozircha bo'sh bo'lishi mumkin - shunda
# faqat asosiy bot orqali bo'lgan yo'l ishlaydi.
ADMIN_BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _verify_init_data_with_token(init_data: str, bot_token: str) -> dict | None:
    """Berilgan bot tokeni bilan Telegram initData ni tekshiradi."""
    if not bot_token:
        return None
    vals = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = vals.pop("hash", None)
    if not received_hash:
        return None

    # auth_date eskirganini tekshirish (24 soatdan eski bo'lsa rad etamiz)
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


def verify_init_data(init_data: str) -> dict | None:
    """Asosiy bot (foydalanuvchi webapp) uchun initData tekshiradi."""
    return _verify_init_data_with_token(init_data, BOT_TOKEN)


def verify_admin_init_data(init_data: str) -> dict | None:
    """Admin panel uchun initData tekshiradi:
    1) Imzo to'g'ri bo'lishi kerak (admin botning O'Z tokeni bilan)
    2) Yuboruvchi user_id ADMIN_ID ga teng bo'lishi kerak
    Shu ikkisi bajarilmasa - kirish rad etiladi."""
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

    # Vazifalar holati
    tasks_done = {}
    for key in ["tg", "ig", "yt"]:
        row = db.get_task(user_id, key)
        tasks_done[key] = (row and row["status"] == "done")

    # Referrallar soni
    conn = db.get_conn()
    refs = conn.execute("SELECT refs FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    refs_count = refs["refs"] if refs else 0

    return {
        "balance": balance,
        "tasks_done": tasks_done,
        "refs": refs_count
    }


@app.get("/leaderboard")
async def get_leaderboard():
    """Top 10 foydalanuvchi referral soniga qarab"""
    conn = db.get_conn()
    rows = conn.execute("""
        SELECT user_id, first_name, username, refs
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
            "refs": row["refs"]
        })

    return result


class UcOrderRequest(BaseModel):
    player_id: str
    uc: int
    price: int


@app.post("/uc_order")
async def uc_order(request: Request, body: UcOrderRequest):
    """Foydalanuvchi UC sotib olganda webapp shu endpointga HTTP
    so'rov yuboradi (avvalgi tg.sendData() o'rniga). Bu yondashuv
    ishonchli, chunki natija darhol (HTTP javob orqali) ma'lum bo'ladi -
    Telegram update yetib bormay qolishi xavfi yo'q."""
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    first_name = user.get("first_name", "Foydalanuvchi")
    username = user.get("username", "")

    db.ensure_user(user_id, username, first_name)

    # Balansni tekshir va kamayt (atomik - deduct_balance ichida tekshiradi)
    ok = db.deduct_balance(user_id, body.price)
    if not ok:
        raise HTTPException(status_code=400, detail="Balans yetarli emas")

    # Buyurtmani bazaga 'pending' holida yozamiz - admin panel buni ko'radi
    order_id = db.create_uc_order(user_id, body.player_id, body.uc, body.price, 0)

    # Adminga oddiy bildirishnoma (tugmasiz) - faqat xabardor qilish uchun.
    # Asosiy tasdiqlash ADMIN PANEL orqali bo'ladi, shu xabar shart emas,
    # lekin tezkor xabardorlik uchun foydali. Xato bersa ham buyurtma
    # bazada saqlangani uchun jarayon davom etadi.
    try:
        import httpx
        admin_text = (
            f"🎮 <b>Yangi UC buyurtma (#{order_id})</b>\n\n"
            f"👤 <a href='tg://user?id={user_id}'>{first_name}</a>\n"
            f"🆔 TG ID: <code>{user_id}</code>\n"
            f"🕹 PUBG ID: <code>{body.player_id}</code>\n"
            f"💎 UC: <b>{body.uc:,} UC</b>\n"
            f"💵 Narxi: <b>{body.price:,} so'm</b>\n\n"
            f"Admin panelda ko'rib, tasdiqlang."
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_ID, "text": admin_text, "parse_mode": "HTML"}
            )
    except Exception:
        pass  # bildirishnoma yetib bormasa ham buyurtma bazada qoladi

    return {"ok": True, "order_id": order_id}


# ── ADMIN PANEL endpointlari ────────────────────────────────
@app.get("/admin/orders")
async def admin_list_orders(request: Request, status: str = "pending"):
    """Admin panel uchun buyurtmalar ro'yxati.
    status='pending' -> faqat kutilayotganlar
    status='all'     -> so'nggi 50 ta (holatidan qatiy nazar)
    """
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
    """Admin panelda 'Tasdiqlash' tugmasi bosilganda chaqiriladi.
    Buyurtma holatini 'approved' ga o'zgartiradi va foydalanuvchiga
    UC yuklanganini Telegram orqali xabar qiladi."""
    require_admin(request)

    order = db.get_uc_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Buyurtma topilmadi")
    if order["status"] != "pending":
        raise HTTPException(status_code=400, detail="Bu buyurtma allaqachon ko'rib chiqilgan")

    db.approve_uc_order(order_id)

    try:
        import httpx
        text = (
            f"✅ <b>{order['uc_amount']:,} UC</b> muvaffaqiyatli yuklandi! 🎮\n"
            f"🕹 PUBG ID: <code>{order['pubg_id']}</code>"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": order["user_id"], "text": text, "parse_mode": "HTML"}
            )
    except Exception:
        pass

    return {"ok": True}


@app.post("/admin/orders/{order_id}/cancel")
async def admin_cancel_order(order_id: int, request: Request):
    """Admin panelda 'Bekor qilish' tugmasi bosilganda chaqiriladi.
    Buyurtmani bekor qiladi, pulni foydalanuvchiga qaytaradi va
    xabar beradi."""
    require_admin(request)

    result = db.cancel_uc_order(order_id)
    if not result:
        raise HTTPException(status_code=400, detail="Buyurtma topilmadi yoki allaqachon ko'rib chiqilgan")

    db.add_balance(result["user_id"], result["price"])

    try:
        import httpx
        text = f"❌ UC buyurtmangiz bekor qilindi. {result['price']:,} so'm hisobingizga qaytarildi."
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": result["user_id"], "text": text}
            )
    except Exception:
        pass

    return {"ok": True}


@app.get("/admin")
async def serve_admin_panel():
    """Admin panel (admin.html) statik faylini qaytaradi.
    Bu sahifaning o'zi ochiq, lekin undagi barcha API
    so'rovlari (/admin/orders va h.k.) initData orqali
    ADMIN_ID tekshiruvidan o'tishi shart - shuning uchun
    sahifaning o'zini ochish xavfsiz."""
    return FileResponse("admin.html", media_type="text/html")


@app.get("/health")
async def health():
    return {"status": "ok"}
