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

ADMIN_BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _verify_init_data_with_token(init_data: str, bot_token: str) -> dict | None:
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


def verify_init_data(init_data: str) -> dict | None:
    return _verify_init_data_with_token(init_data, BOT_TOKEN)


def verify_admin_init_data(init_data: str) -> dict | None:
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
    conn.close()
    refs_count = refs["refs"] if refs else 0

    return {
        "balance": balance,
        "tasks_done": tasks_done,
        "refs": refs_count
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


@app.get("/leaderboard")
async def get_leaderboard():
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


class VipOrderRequest(BaseModel):
    package: str
    price: int


@app.post("/buy_vip")
async def buy_vip(request: Request, body: VipOrderRequest):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    db.ensure_user(user_id, user.get("username"), user.get("first_name"))

    ok = db.deduct_balance(user_id, body.price)
    if not ok:
        raise HTTPException(status_code=400, detail="Balans yetarli emas")

    order_id = db.create_vip_order(user_id, body.package, body.price)

    # Faqat adminga xabar — foydalanuvchiga emas
    try:
        import httpx
        admin_text = (
            f"👑 <b>Yangi VIP xarid</b>\n\n"
            f"👤 <a href='tg://user?id={user_id}'>{user.get('first_name','Foydalanuvchi')}</a>\n"
            f"🆔 TG ID: <code>{user_id}</code>\n"
            f"📦 Paket: <b>{body.package}</b>\n"
            f"💵 Narxi: <b>{body.price:,} so'm</b>"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_ID, "text": admin_text, "parse_mode": "HTML"}
            )
    except Exception:
        pass

    return {"ok": True, "order_id": order_id}


class UcOrderRequest(BaseModel):
    player_id: str
    uc: int
    price: int


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

    ok = db.deduct_balance(user_id, body.price)
    if not ok:
        raise HTTPException(status_code=400, detail="Balans yetarli emas")

    order_id = db.create_uc_order(user_id, body.player_id, body.uc, body.price, 0)

    # Faqat adminga xabar — foydalanuvchiga emas
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
        pass

    return {"ok": True, "order_id": order_id}


# ── ADMIN PANEL endpointlari ────────────────────────────────
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

    # Foydalanuvchiga xabar YUBORILMAYDI
    return {"ok": True}


@app.post("/admin/orders/{order_id}/cancel")
async def admin_cancel_order(order_id: int, request: Request):
    require_admin(request)

    result = db.cancel_uc_order(order_id)
    if not result:
        raise HTTPException(status_code=400, detail="Buyurtma topilmadi yoki allaqachon ko'rib chiqilgan")

    db.add_balance(result["user_id"], result["price"])

    # Foydalanuvchiga xabar YUBORILMAYDI
    return {"ok": True}


@app.get("/admin")
async def serve_admin_panel():
    return FileResponse("admin.html", media_type="text/html")


@app.get("/health")
async def health():
    return {"status": "ok"}
            
