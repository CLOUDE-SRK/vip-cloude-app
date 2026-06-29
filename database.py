import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ["DATABASE_URL"]


def get_conn():
    """PostgreSQL ulanishini ochadi. RealDictCursor ishlatiladi,
    shunda natijalar lug'at (dict) kabi - row['column_name'] - olinadi,
    bu SQLite'dagi sqlite3.Row xulq-atvoriga o'xshash."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    # Foydalanuvchilar
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     BIGINT PRIMARY KEY,
            username    TEXT,
            first_name  TEXT,
            balance     BIGINT DEFAULT 0,
            refs        INTEGER DEFAULT 0,
            ref_by      BIGINT DEFAULT NULL,
            created_at  BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
        )
    """)

    # Topup so'rovlar (screenshot)
    c.execute("""
        CREATE TABLE IF NOT EXISTS topup_requests (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT,
            amount      BIGINT,
            method      TEXT,
            status      TEXT DEFAULT 'pending',
            msg_id      BIGINT,
            created_at  BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
        )
    """)

    # UC buyurtmalar
    c.execute("""
        CREATE TABLE IF NOT EXISTS uc_orders (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT,
            pubg_id     TEXT,
            uc_amount   BIGINT,
            price       BIGINT,
            status      TEXT DEFAULT 'pending',
            msg_id      BIGINT,
            created_at  BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
        )
    """)

    # VIP xaridlar
    c.execute("""
        CREATE TABLE IF NOT EXISTS vip_orders (
            id          SERIAL PRIMARY KEY,
            user_id     BIGINT,
            vip_type    TEXT,
            price       BIGINT,
            status      TEXT DEFAULT 'pending',
            created_at  BIGINT DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
        )
    """)

    # Vazifalar holati
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            user_id     BIGINT,
            task_key    TEXT,
            status      TEXT DEFAULT 'pending',
            PRIMARY KEY (user_id, task_key)
        )
    """)

    conn.commit()
    conn.close()


# ── Foydalanuvchi ──────────────────────────────────────────
def get_user(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
    user = c.fetchone()
    conn.close()
    return user


def ensure_user(user_id: int, username: str = None, first_name: str = None, ref_by: int = None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id=%s", (user_id,))
    existing = c.fetchone()
    if not existing:
        c.execute(
            "INSERT INTO users (user_id, username, first_name, ref_by) VALUES (%s,%s,%s,%s)",
            (user_id, username, first_name, ref_by)
        )
        if ref_by:
            c.execute("UPDATE users SET refs = refs + 1 WHERE user_id=%s", (ref_by,))
        conn.commit()
    conn.close()


def get_balance(user_id: int) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=%s", (user_id,))
    row = c.fetchone()
    conn.close()
    return row["balance"] if row else 0


def add_balance(user_id: int, amount: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, user_id))
    conn.commit()
    conn.close()


def deduct_balance(user_id: int, amount: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=%s", (user_id,))
    row = c.fetchone()
    if not row or row["balance"] < amount:
        conn.close()
        return False
    c.execute("UPDATE users SET balance = balance - %s WHERE user_id=%s", (amount, user_id))
    conn.commit()
    conn.close()
    return True


# ── Topup ──────────────────────────────────────────────────
def create_topup(user_id: int, amount: int, method: str, msg_id: int) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO topup_requests (user_id, amount, method, msg_id) VALUES (%s,%s,%s,%s) RETURNING id",
        (user_id, amount, method, msg_id)
    )
    rid = c.fetchone()["id"]
    conn.commit()
    conn.close()
    return rid


def get_topup(request_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM topup_requests WHERE id=%s", (request_id,))
    row = c.fetchone()
    conn.close()
    return row


def approve_topup(request_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE topup_requests SET status='approved' WHERE id=%s", (request_id,))
    conn.commit()
    conn.close()


# ── UC Orders ─────────────────────────────────────────────
def create_uc_order(user_id: int, pubg_id: str, uc_amount: int, price: int, msg_id: int) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO uc_orders (user_id, pubg_id, uc_amount, price, msg_id) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (user_id, pubg_id, uc_amount, price, msg_id)
    )
    oid = c.fetchone()["id"]
    conn.commit()
    conn.close()
    return oid


def get_uc_order(order_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM uc_orders WHERE id=%s", (order_id,))
    row = c.fetchone()
    conn.close()
    return row


def approve_uc_order(order_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE uc_orders SET status='approved' WHERE id=%s", (order_id,))
    conn.commit()
    conn.close()


# ── ADMIN PANEL uchun qo'shimcha funksiyalar ───────────────
def get_pending_uc_orders():
    """Admin panelda ko'rsatish uchun barcha 'pending' UC buyurtmalarini,
    foydalanuvchi ismi bilan birga qaytaradi (eng yangisi birinchi)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT
            o.id, o.user_id, o.pubg_id, o.uc_amount, o.price,
            o.status, o.created_at,
            u.first_name, u.username
        FROM uc_orders o
        LEFT JOIN users u ON u.user_id = o.user_id
        WHERE o.status = 'pending'
        ORDER BY o.id DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def get_recent_uc_orders(limit: int = 50):
    """Admin panelda 'barcha so'nggi buyurtmalar' ko'rinishi uchun
    (pending + approved + cancelled), eng yangisi birinchi."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT
            o.id, o.user_id, o.pubg_id, o.uc_amount, o.price,
            o.status, o.created_at,
            u.first_name, u.username
        FROM uc_orders o
        LEFT JOIN users u ON u.user_id = o.user_id
        ORDER BY o.id DESC
        LIMIT %s
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    return rows


def cancel_uc_order(order_id: int):
    """Buyurtmani bekor qilish va foydalanuvchiga pulni qaytarish uchun
    narxni qaytaradi (None bo'lsa - topilmadi yoki allaqachon ko'rib chiqilgan)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, price, status FROM uc_orders WHERE id=%s", (order_id,))
    row = c.fetchone()
    if not row or row["status"] != "pending":
        conn.close()
        return None
    c.execute("UPDATE uc_orders SET status='cancelled' WHERE id=%s", (order_id,))
    conn.commit()
    conn.close()
    return {"user_id": row["user_id"], "price": row["price"]}


# ── VIP Orders ────────────────────────────────────────────
def create_vip_order(user_id: int, vip_type: str, price: int) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO vip_orders (user_id, vip_type, price, status) VALUES (%s,%s,%s,'approved') RETURNING id",
        (user_id, vip_type, price)
    )
    oid = c.fetchone()["id"]
    conn.commit()
    conn.close()
    return oid


# ── Tasks ─────────────────────────────────────────────────
def get_task(user_id: int, task_key: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM tasks WHERE user_id=%s AND task_key=%s", (user_id, task_key))
    row = c.fetchone()
    conn.close()
    return row


def set_task_done(user_id: int, task_key: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO tasks (user_id, task_key, status) VALUES (%s,%s,'done')
           ON CONFLICT (user_id, task_key) DO UPDATE SET status='done'""",
        (user_id, task_key)
    )
    conn.commit()
    conn.close()
