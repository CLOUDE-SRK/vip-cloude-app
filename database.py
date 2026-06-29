import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "cloude.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            first_name  TEXT,
            balance     INTEGER DEFAULT 0,
            refs        INTEGER DEFAULT 0,
            ref_by      INTEGER DEFAULT NULL,
            created_at  INTEGER DEFAULT (strftime('%s','now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS topup_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            amount      INTEGER,
            method      TEXT,
            status      TEXT DEFAULT 'pending',
            msg_id      INTEGER,
            created_at  INTEGER DEFAULT (strftime('%s','now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS uc_orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            pubg_id     TEXT,
            uc_amount   INTEGER,
            price       INTEGER,
            status      TEXT DEFAULT 'pending',
            msg_id      INTEGER,
            created_at  INTEGER DEFAULT (strftime('%s','now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS vip_orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            vip_type    TEXT,
            price       INTEGER,
            status      TEXT DEFAULT 'pending',
            created_at  INTEGER DEFAULT (strftime('%s','now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            user_id     INTEGER,
            task_key    TEXT,
            status      TEXT DEFAULT 'pending',
            PRIMARY KEY (user_id, task_key)
        )
    """)

    conn.commit()
    conn.close()

def get_user(user_id: int):
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return user

def ensure_user(user_id: int, username: str = None, first_name: str = None, ref_by: int = None):
    conn = get_conn()
    existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO users (user_id, username, first_name, ref_by) VALUES (?,?,?,?)",
            (user_id, username, first_name, ref_by)
        )
        if ref_by:
            conn.execute("UPDATE users SET refs = refs + 1 WHERE user_id=?", (ref_by,))
        conn.commit()
    conn.close()

def get_balance(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["balance"] if row else 0

def add_balance(user_id: int, amount: int):
    conn = get_conn()
    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def deduct_balance(user_id: int, amount: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row or row["balance"] < amount:
        conn.close()
        return False
    conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()
    return True

def create_topup(user_id: int, amount: int, method: str, msg_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO topup_requests (user_id, amount, method, msg_id) VALUES (?,?,?,?)",
        (user_id, amount, method, msg_id)
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid

def get_topup(request_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM topup_requests WHERE id=?", (request_id,)).fetchone()
    conn.close()
    return row

def has_screenshot_today(user_id: int) -> bool:
    """Foydalanuvchi bugun (kalendar kuni, UTC) allaqachon to'lov
    screenshoti yuborganmi, shuni tekshiradi. Kunlik 1 ta screenshot
    limitini amalga oshirish uchun ishlatiladi. method='screenshot'
    bo'lgan yozuvlarni (topup_request orqali kelganlarni emas)
    hisobga oladi."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT 1 FROM topup_requests
        WHERE user_id=? AND method='screenshot'
          AND date(created_at, 'unixepoch') = date('now')
        LIMIT 1
        """,
        (user_id,)
    ).fetchone()
    conn.close()
    return row is not None

def approve_topup(request_id: int, amount: int = None):
    """Topup so'rovini tasdiqlaydi VA summani foydalanuvchi balansiga
    qo'shadi.

    `amount` parametri ixtiyoriy: admin screenshot/raqam orqali summani
    tasdiqlaganda haqiqiy summa shu yerga keladi va topup_requests
    jadvalidagi 'amount' ustuni ham shu qiymat bilan yangilanadi
    (screenshot kelganda amount=0 bilan yaratilgani uchun, buni
    keyinroq to'g'ri summa bilan to'ldirish kerak).

    MUHIM: balansga pul aynan shu funksiya ichida bir marta qo'shiladi.
    bot.py tarafida bu funksiyani chaqirgandan keyin yana add_balance()
    chaqirilmasligi kerak - aks holda pul ikki marta qo'shilib ketadi.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT user_id, amount, status FROM topup_requests WHERE id=?", (request_id,)
    ).fetchone()
    if not row or row["status"] != "pending":
        conn.close()
        return False

    final_amount = amount if amount is not None else row["amount"]

    conn.execute(
        "UPDATE topup_requests SET status='approved', amount=? WHERE id=?",
        (final_amount, request_id)
    )
    conn.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (final_amount, row["user_id"])
    )
    conn.commit()
    conn.close()
    return True

def create_uc_order(user_id: int, pubg_id: str, uc_amount: int, price: int, msg_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO uc_orders (user_id, pubg_id, uc_amount, price, msg_id) VALUES (?,?,?,?,?)",
        (user_id, pubg_id, uc_amount, price, msg_id)
    )
    oid = cur.lastrowid
    conn.commit()
    conn.close()
    return oid

def get_uc_order(order_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM uc_orders WHERE id=?", (order_id,)).fetchone()
    conn.close()
    return row

def approve_uc_order(order_id: int):
    conn = get_conn()
    conn.execute("UPDATE uc_orders SET status='approved' WHERE id=?", (order_id,))
    conn.commit()
    conn.close()

def get_pending_uc_orders():
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            o.id, o.user_id, o.pubg_id, o.uc_amount, o.price,
            o.status, o.created_at,
            u.first_name, u.username
        FROM uc_orders o
        LEFT JOIN users u ON u.user_id = o.user_id
        WHERE o.status = 'pending'
        ORDER BY o.id DESC
    """).fetchall()
    conn.close()
    return rows

def get_recent_uc_orders(limit: int = 50):
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            o.id, o.user_id, o.pubg_id, o.uc_amount, o.price,
            o.status, o.created_at,
            u.first_name, u.username
        FROM uc_orders o
        LEFT JOIN users u ON u.user_id = o.user_id
        ORDER BY o.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return rows

def cancel_uc_order(order_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT user_id, price, status FROM uc_orders WHERE id=?", (order_id,)
    ).fetchone()
    if not row or row["status"] != "pending":
        conn.close()
        return None
    conn.execute("UPDATE uc_orders SET status='cancelled' WHERE id=?", (order_id,))
    conn.commit()
    conn.close()
    return {"user_id": row["user_id"], "price": row["price"]}

def get_user_history(user_id: int, limit: int = 100):
    conn = get_conn()

    uc_rows = conn.execute(
        "SELECT id, uc_amount, price, pubg_id, status, created_at FROM uc_orders WHERE user_id=?",
        (user_id,)
    ).fetchall()

    vip_rows = conn.execute(
        "SELECT id, vip_type, price, status, created_at FROM vip_orders WHERE user_id=?",
        (user_id,)
    ).fetchall()

    topup_rows = conn.execute(
        "SELECT id, amount, method, status, created_at FROM topup_requests WHERE user_id=?",
        (user_id,)
    ).fetchall()

    conn.close()

    items = []

    for r in uc_rows:
        items.append({
            "kind": "uc",
            "id": r["id"],
            "title": f"{r['uc_amount']:,} UC".replace(",", " "),
            "sub": f"PUBG ID: {r['pubg_id']}",
            "amount": -r["price"],
            "status": r["status"],
            "created_at": r["created_at"],
        })

    for r in vip_rows:
        items.append({
            "kind": "vip",
            "id": r["id"],
            "title": r["vip_type"],
            "sub": "VIP paket",
            "amount": -r["price"],
            "status": r["status"],
            "created_at": r["created_at"],
        })

    for r in topup_rows:
        amount = r["amount"] if r["amount"] else 0
        items.append({
            "kind": "topup",
            "id": r["id"],
            "title": "Hisob to'ldirish",
            "sub": r["method"] or "",
            "amount": amount,
            "status": r["status"],
            "created_at": r["created_at"],
        })

    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items[:limit]

def create_vip_order(user_id: int, vip_type: str, price: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO vip_orders (user_id, vip_type, price, status) VALUES (?,?,?,'approved')",
        (user_id, vip_type, price)
    )
    oid = cur.lastrowid
    conn.commit()
    conn.close()
    return oid

def get_task(user_id: int, task_key: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM tasks WHERE user_id=? AND task_key=?", (user_id, task_key)).fetchone()
    conn.close()
    return row

def set_task_done(user_id: int, task_key: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO tasks (user_id, task_key, status) VALUES (?,?,'done')",
        (user_id, task_key)
    )
    conn.commit()
    conn.close()
    
