"""
Webapp ↔ Bot server (FastAPI)
Webapp bu serverga so'rov qilib foydalanuvchi balansini oladi.
"""
import os
import hmac
import hashlib
import json
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import database as db

app = FastAPI()

BOT_TOKEN = os.environ["BOT_TOKEN"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def verify_init_data(init_data: str) -> dict | None:
    """Telegram initData ni tekshiradi. Haqiqiy bo'lsa user dict qaytaradi."""
    vals = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = vals.pop("hash", None)
    if not received_hash:
        return None

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(vals.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        return None

    user_json = vals.get("user", "{}")
    try:
        return json.loads(user_json)
    except Exception:
        return None

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

from pydantic import BaseModel

class UcOrderRequest(BaseModel):
    player_id: str
    uc: int
    price: int

@app.post("/uc_order")
async def uc_order(request: Request, body: UcOrderRequest):
    import httpx, os
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    first_name = user.get("first_name", "Foydalanuvchi")
    username = user.get("username", "")

    db.ensure_user(user_id, username, first_name)

    # Balansni tekshir va kamayt
    ok = db.deduct_balance(user_id, body.price)
    if not ok:
        raise HTTPException(status_code=400, detail="Balans yetarli emas")

    # Adminga Telegram xabar yuborish
    bot_token = os.environ["BOT_TOKEN"]
    admin_id = int(os.environ["ADMIN_ID"])

    tg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    text = (
        f"🎮 <b>UC Buyurtma</b>\n\n"
        f"👤 <a href=\'tg://user?id={user_id}\'>{first_name}</a>\n"
        f"🆔 TG ID: <code>{user_id}</code>\n"
        f"🕹 PUBG ID: <code>{body.player_id}</code>\n"
        f"💎 UC: <b>{body.uc:,} UC</b>\n"
        f"💵 Narxi: <b>{body.price:,} so\'m</b>\n"
        f"💰 Qolgan balans: <b>{db.get_balance(user_id):,} so\'m</b>"
    )

    async with httpx.AsyncClient() as client:
        await client.post(tg_url, json={
            "chat_id": admin_id,
            "text": text,
            "parse_mode": "HTML"
        })

    db.create_uc_order(user_id, body.player_id, body.uc, body.price, 0)

    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok"}
