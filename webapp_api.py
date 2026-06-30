"""
Webapp <-> Bot server (FastAPI)
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
VIP_CHAT_ID = int(os.environ["VIP_CHAT_ID"])

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


@app.get("/custom_tasks")
async def list_custom_tasks():
    rows = db.get_custom_tasks(active_only=True)
    return [{"id": r["id"], "title": r["title"], "reward": r["reward"]} for r in rows]


class VerifyRequestBody(BaseModel):
    task_key: str
    task_title: str
    reward: int = 0


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

    req_id = db.create_verify_request(user_id, body.task_key, body.task_title, body.reward)

    try:
        import httpx
        admin_text = (
            f"📋 <b>Yangi vazifa so'rovi (#{req_id})</b>\n\n"
            f"👤 <a href='tg://user?id={user_id}'>{user.get('first_name','Foydalanuvchi')}</a>\n"
            f"🆔 TG ID: <code>{user_id}</code>\n"
            f"✅ Vazifa: <b>{body.task_title}</b>\n"
            f"💰 Mukofot: <b>{body.reward:,} so'm</b>\n\n"
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


class AvatarBody(BaseModel):
    avatar_url: str


@app.post("/profile/avatar")
async def set_profile_avatar(request: Request, body: AvatarBody):
    """Faqat premium foydalanuvchilar profil rasm qo'ya oladi."""
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    ok = db.set_avatar(user_id, body.avatar_url)
    if not ok:
        raise HTTPException(status_code=403, detail="Faqat premium foydalanuvchilar uchun")
    return {"ok": True}


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
                            f"✅ <b>{body.package}</b> faollashtirildi!\n\n"
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


class EarnTapRequest(BaseModel):
    amount: int


@app.post("/earn_tap")
async def earn_tap(request: Request, body: EarnTapRequest):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    db.ensure_user(user_id, user.get("username"), user.get("first_name"))

    amount = max(0, min(body.amount, 5))
    result = db.add_earn_tap(user_id, amount, cap=500)

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

    result = db.cancel_uc_order(order_id)
    if not result:
        raise HTTPException(status_code=400, detail="Buyurtma topilmadi yoki allaqachon ko'rib chiqilgan")

    db.add_balance(result["user_id"], result["price"])
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
        "active": bool(r["active"]), "created_at": r["created_at"]
    } for r in rows]


class CustomTaskBody(BaseModel):
    title: str
    reward: int


@app.post("/admin/custom_tasks")
async def admin_create_custom_task(request: Request, body: CustomTaskBody):
    require_admin(request)
    tid = db.create_custom_task(body.title, body.reward)
    return {"ok": True, "id": tid}


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

    return {"ok": True}


class GiftPremiumBody(BaseModel):
    duration: str  # '15kun' | '1oy' | '1sezon'


@app.post("/admin/profile/{user_id}/gift_premium")
async def admin_gift_premium(user_id: int, request: Request, body: GiftPremiumBody):
    require_admin(request)
    row = db.get_profile_admin(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    new_until = db.gift_premium(user_id, body.duration)
    if new_until is None:
        raise HTTPException(status_code=400, detail="Noto'g'ri muddat")

    label = {"15kun": "15 kunlik", "1oy": "1 oylik", "1sezon": "1 sezonlik"}.get(body.duration, body.duration)

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": user_id,
                    "text": (
                        f"🎁 Sizga <b>{label} Premium</b> sovg'a qilindi!\n\n"
                        f"✨ Endi profilga rasm qo'ya olasiz va pul ishlashda 2x tezroq ishlay olasiz."
                    ),
                    "parse_mode": "HTML"
                }
            )
    except Exception:
        pass

    return {"ok": True, "premium_until": new_until}


@app.get("/admin")
async def serve_admin_panel():
    return FileResponse("admin.html", media_type="text/html")


@app.get("/health")
async def health():
    return {"status": "ok"}
