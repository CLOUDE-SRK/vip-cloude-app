import sqlite3
import os
import time

DB_PATH = os.environ.get("DB_PATH", "cloude.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols

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

    c.execute("""
        CREATE TABLE IF NOT EXISTS earn_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            amount      INTEGER,
            created_at  INTEGER DEFAULT (strftime('%s','now'))
        )
    """)

    # Maxsus (admin tomonidan qo'shiladigan) vazifalar ro'yxati
    c.execute("""
        CREATE TABLE IF NOT EXISTS custom_tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT,
            reward      INTEGER DEFAULT 0,
            url         TEXT DEFAULT NULL,
            icon_url    TEXT DEFAULT NULL,
            active      INTEGER DEFAULT 1,
            created_at  INTEGER DEFAULT (strftime('%s','now'))
        )
    """)

    # Foydalanuvchi "bajardim" deb yuborgan, admin tasdiqlashi kerak
    # bo'lgan vazifa so'rovlari (tg/ig/yt obuna, custom_tasks va h.k.)
    c.execute("""
        CREATE TABLE IF NOT EXISTS verify_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            task_key    TEXT,
            task_title  TEXT,
            reward      INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'pending',
            created_at  INTEGER DEFAULT (strftime('%s','now'))
        )
    """)

    conn.commit()

    # Eski bazalarda yo'q bo'lishi mumkin bo'lgan ustunlarni qo'shamiz
    if not _column_exists(conn, "users", "is_premium"):
        conn.execute("ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0")
    if not _column_exists(conn, "users", "premium_until"):
        conn.execute("ALTER TABLE users ADD COLUMN premium_until INTEGER DEFAULT NULL")
    if not _column_exists(conn, "users", "avatar_url"):
        conn.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT DEFAULT NULL")
    if not _column_exists(conn, "custom_tasks", "url"):
        conn.execute("ALTER TABLE custom_tasks ADD COLUMN url TEXT DEFAULT NULL")
    if not _column_exists(conn, "custom_tasks", "icon_url"):
        conn.execute("ALTER TABLE custom_tasks ADD COLUMN icon_url TEXT DEFAULT NULL")

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

def is_premium_active(user_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT is_premium, premium_until FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    if not row or not row["is_premium"]:
        return False
    if row["premium_until"] and row["premium_until"] < int(time.time()):
        return False
    return True

def get_earn_today(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) as total FROM earn_log
        WHERE user_id=? AND date(created_at, 'unixepoch') = date('now')
        """,
        (user_id,)
    ).fetchone()
    conn.close()
    return row["total"] if row else 0

def add_earn_tap(user_id: int, amount: int, cap: int = 500) -> dict:
    """Tanga bosishdan kelgan miqdorni qo'shadi. Agar foydalanuvchida
    faol premium bo'lsa, kunlik limit (cap) 2 baravar (masalan 500 ->
    1000) qilib qo'llaniladi."""
    premium = is_premium_active(user_id)
    effective_cap = cap * 2 if premium else cap

    conn = get_conn()
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) as total FROM earn_log
        WHERE user_id=? AND date(created_at, 'unixepoch') = date('now')
        """,
        (user_id,)
    ).fetchone()
    earned_today = row["total"] if row else 0

    remaining = max(0, effective_cap - earned_today)
    actual_amount = min(max(0, amount), remaining)

    if actual_amount > 0:
        conn.execute(
            "INSERT INTO earn_log (user_id, amount) VALUES (?,?)",
            (user_id, actual_amount)
        )
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (actual_amount, user_id)
        )

    new_balance_row = conn.execute(
        "SELECT balance FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    new_balance = new_balance_row["balance"] if new_balance_row else 0

    conn.commit()
    conn.close()

    return {
        "added": actual_amount,
        "balance": new_balance,
        "earn_today": earned_today + actual_amount,
        "cap": effective_cap,
        "premium": premium
    }

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


# ── CUSTOM TASKS (vazifalar) ─────────────────────────────────
def create_custom_task(title: str, reward: int, url: str = None, icon_url: str = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO custom_tasks (title, reward, url, icon_url) VALUES (?,?,?,?)",
        (title, reward, url, icon_url)
    )
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return tid

def get_custom_tasks(active_only: bool = True):
    conn = get_conn()
    if active_only:
        rows = conn.execute(
            "SELECT * FROM custom_tasks WHERE active=1 ORDER BY id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM custom_tasks ORDER BY id DESC"
        ).fetchall()
    conn.close()
    return rows

def deactivate_custom_task(task_id: int):
    conn = get_conn()
    conn.execute("UPDATE custom_tasks SET active=0 WHERE id=?", (task_id,))
    conn.commit()
    conn.close()


# ── VERIFY REQUESTS (referal/vazifa tasdiqlash so'rovlari) ──
def has_pending_verify_request(user_id: int, task_key: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM verify_requests WHERE user_id=? AND task_key=? AND status='pending'",
        (user_id, task_key)
    ).fetchone()
    conn.close()
    return row is not None


def create_verify_request(user_id: int, task_key: str, task_title: str, reward: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO verify_requests (user_id, task_key, task_title, reward) VALUES (?,?,?,?)",
        (user_id, task_key, task_title, reward)
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid

def get_pending_verify_requests():
    conn = get_conn()
    rows = conn.execute("""
        SELECT v.*, u.first_name, u.username
        FROM verify_requests v
        LEFT JOIN users u ON u.user_id = v.user_id
        WHERE v.status = 'pending'
        ORDER BY v.id DESC
    """).fetchall()
    conn.close()
    return rows

def get_verify_request(request_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM verify_requests WHERE id=?", (request_id,)).fetchone()
    conn.close()
    return row

def approve_verify_request(request_id: int):
    req = get_verify_request(request_id)
    if not req or req["status"] != "pending":
        return None
    conn = get_conn()
    conn.execute("UPDATE verify_requests SET status='approved' WHERE id=?", (request_id,))
    conn.execute(
        "INSERT OR REPLACE INTO tasks (user_id, task_key, status) VALUES (?,?,'done')",
        (req["user_id"], req["task_key"])
    )
    if req["reward"]:
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (req["reward"], req["user_id"])
        )
    conn.commit()
    conn.close()
    return req

def reject_verify_request(request_id: int):
    conn = get_conn()
    row = conn.execute("SELECT status FROM verify_requests WHERE id=?", (request_id,)).fetchone()
    if not row or row["status"] != "pending":
        conn.close()
        return False
    conn.execute("UPDATE verify_requests SET status='rejected' WHERE id=?", (request_id,))
    conn.commit()
    conn.close()
    return True


# ── TOP O'YINCHILAR / RAQOBAT ────────────────────────────────
def get_top_players(limit: int = 10):
    conn = get_conn()
    rows = conn.execute("""
        SELECT user_id, first_name, username, refs
        FROM users
        WHERE refs > 0
        ORDER BY refs DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return rows

def reset_competition():
    conn = get_conn()
    conn.execute("UPDATE users SET refs = 0")
    conn.commit()
    conn.close()


# ── PROFIL (admin tomonidan qidirish/boshqarish) ─────────────
def get_profile_admin(user_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row

PREMIUM_DURATIONS = {
    "15kun": 15 * 86400,
    "1oy": 30 * 86400,
    "1sezon": 90 * 86400,
}

def gift_premium(user_id: int, duration_key: str):
    seconds = PREMIUM_DURATIONS.get(duration_key)
    if seconds is None:
        return None
    conn = get_conn()
    row = conn.execute("SELECT premium_until FROM users WHERE user_id=?", (user_id,)).fetchone()
    now = int(time.time())
    base = now
    if row and row["premium_until"] and row["premium_until"] > now:
        base = row["premium_until"]
    new_until = base + seconds
    conn.execute(
        "UPDATE users SET is_premium=1, premium_until=? WHERE user_id=?",
        (new_until, user_id)
    )
    conn.commit()
    conn.close()
    return new_until

def set_avatar(user_id: int, avatar_url: str) -> bool:
    if not is_premium_active(user_id):
        return False
    conn = get_conn()
    conn.execute("UPDATE users SET avatar_url=? WHERE user_id=?", (avatar_url, user_id))
    conn.commit()
    conn.close()
    return True
