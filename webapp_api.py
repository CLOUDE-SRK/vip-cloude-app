# =====================================================================
# TUZATISH: webapp_api.py ga qo'shiladigan / almashtiriladigan qismlar
# =====================================================================
#
# MUAMMO: /buy_vip va /uc_order endpointlari narxni (`price`) to'g'ridan-
# to'g'ri clientdan qabul qilardi. Foydalanuvchi DevTools orqali
# `price: 1` yuborib, deyarli tekin VIP/UC "sotib olishi" mumkin edi —
# admin panelda ham xuddi shu (soxta) narx ko'rinardi.
#
# YECHIM: narxlar endi FAQAT serverda, quyidagi qattiq ro'yxatlarda
# saqlanadi. Client faqat qaysi paketni tanlaganini (kalit/indeks)
# yuboradi, price/uc/discount hisobini server o'zi qiladi.
# =====================================================================


# ---------------------------------------------------------------
# 1) SERVERDAGI QATTIQ NARX RO'YXATLARI
#    (index.html dagi UC_PACKAGES / VIP kartalar bilan bir xil bo'lishi SHART —
#    frontendda narx o'zgarsa, bu yerda ham albatta yangilang!)
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


def get_ref_discount(refs: int) -> int:
    """Frontenddagi getRefDiscount() bilan bir xil mantiq."""
    if refs >= 30:
        return 15
    if refs >= 20:
        return 10
    if refs >= 10:
        return 5
    return 0


# ---------------------------------------------------------------
# 2) /buy_vip — TUZATILGAN VERSIYA
#    Eski VipOrderRequest(package: str, price: int) o'rniga endi
#    faqat vip_key qabul qilinadi.
# ---------------------------------------------------------------

# ESKI:
# class VipOrderRequest(BaseModel):
#     package: str
#     price: int

# YANGI:
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
    # (clientdan kelgan hech qanday discount qiymatiga ishonmaymiz)
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


# ---------------------------------------------------------------
# 3) /uc_order — TUZATILGAN VERSIYA
#    Eski UcOrderRequest(player_id, uc: int, price: int) o'rniga
#    endi faqat package_index qabul qilinadi.
# ---------------------------------------------------------------

# ESKI:
# class UcOrderRequest(BaseModel):
#     player_id: str
#     uc: int
#     price: int

# YANGI:
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


# ---------------------------------------------------------------
# 4) /task/verify_request — TUZATILGAN VERSIYA
#    reward endi clientdan emas, serverdagi TASK_REWARDS /
#    custom_tasks jadvalidan olinadi.
# ---------------------------------------------------------------

# bot.py da mavjud bo'lgan TASK_REWARDS bilan bir xil bo'lishi kerak:
TASK_REWARDS = {"tg": 5000, "ig": 3000, "yt": 3000}

# ESKI:
# class VerifyRequestBody(BaseModel):
#     task_key: str
#     task_title: str
#     reward: int = 0

# YANGI:
class VerifyRequestBody(BaseModel):
    task_key: str
    task_title: str
    custom_task_id: int | None = None  # agar bu admin qo'shgan custom vazifa bo'lsa


@app.post("/task/verify_request")
async def task_verify_request(request: Request, body: VerifyRequestBody):
    init_data = request.headers.get("X-Init-Data", "")
    user = verify_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = user["id"]
    db.ensure_user(user_id, user.get("username"), user.get("first_name"))

    # Mukofotni SERVERDA aniqlaymiz — clientga ishonmaymiz
    reward = TASK_REWARDS.get(body.task_key)
    if reward is None:
        # tg/ig/yt bo'lmasa, custom_tasks jadvalidan qidiramiz
        if body.custom_task_id is None:
            raise HTTPException(status_code=400, detail="Noma'lum vazifa")
        rows = db.get_custom_tasks(active_only=True)
        match = next((r for r in rows if r["id"] == body.custom_task_id), None)
        if not match:
            raise HTTPException(status_code=400, detail="Vazifa topilmadi yoki faol emas")
        reward = match["reward"]

    req_id = db.create_verify_request(user_id, body.task_key, body.task_title, reward)

    try:
        import httpx
        admin_text = (
            f"📋 <b>Yangi vazifa so'rovi (#{req_id})</b>\n\n"
            f"👤 <a href='tg://user?id={user_id}'>{user.get('first_name','Foydalanuvchi')}</a>\n"
            f"🆔 TG ID: <code>{user_id}</code>\n"
            f"✅ Vazifa: <b>{body.task_title}</b>\n"
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
