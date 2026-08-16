import os
import random
import time

import psycopg2
import psycopg2.pool
import psycopg2.extras

# ── SUPABASE / POSTGRES ULANISHI ─────────────────────────────────────────
# ESKI HOLAT: ma'lumotlar mahalliy SQLite faylida (cloude.db) saqlanardi.
# Render'ning bepul (free) tarifida fayl tizimi "ephemeral" (vaqtinchalik)
# va persistent disk ulash imkoni yo'q — shuning uchun har safar servis
# qayta deploy qilinganda yoki spin-down'dan keyin qayta ishga tushganda
# butun baza (balans, top o'yinchilar, buyurtmalar — hammasi) yo'qolib
# turardi.
#
# YANGI HOLAT: ma'lumotlar endi Render konteynerining o'zidan MUSTAQIL —
# Supabase'dagi tashqi Postgres bazasida saqlanadi. Shuning uchun deploy,
# restart yoki spin-down ma'lumotga umuman ta'sir qilmaydi.
#
# SOZLASH: Render'dagi "Environment" bo'limiga DATABASE_URL o'zgaruvchisini
# qo'shing. Qiymatini Supabase'dan olasiz:
#   Supabase loyihasi -> Project Settings -> Database -> Connection string
#   -> "Transaction pooler" (yoki "Session pooler") variantidagi URI'ni
#   nusxalang (bepul tarifda ko'p qisqa muddatli ulanish ochadigan
#   muhitlar — Render shular jumlasidan — uchun aynan shu tavsiya etiladi,
#   to'g'ridan-to'g'ri 5432 port emas).
DATABASE_URL = os.environ["DATABASE_URL"]

_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=1,
    maxconn=10,
    dsn=DATABASE_URL,
    connect_timeout=10,   # 10 soniyadan ko'p kutmaydi — aks holda ulanish
                          # muammosi bo'lganda butun servis "osilib qolib",
                          # Render hech qachon portni ko'rmasdi.
    sslmode="require",    # Supabase har doim SSL talab qiladi.
)


class _CursorWrapper:
    """psycopg2 kursorini sqlite3 kursoriga o'xshab ishlatish uchun yupqa
    qatlam (fetchone/fetchall/rowcount) — shu bilan webapp_api.py va
    bot.py dagi eski 'conn.execute(...).fetchone()' chaqiruvlari
    o'zgarishsiz qoladi."""

    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount


class _ConnWrapper:
    """sqlite3.Connection'ga o'xshab ishlaydigan yupqa qatlam: conn.execute(...)
    to'g'ridan-to'g'ri ishlaydi va natijalar lug'at kabi o'qiladi (row['col']),
    xuddi avvalgi sqlite3.Row bilan bo'lgani kabi."""

    def __init__(self, raw_conn):
        self._conn = raw_conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # SQLite'dagi '?' placeholder'lari Postgres'da '%s' bo'ladi. Lekin
        # psycopg2 '%' belgisining o'zini ham parametr belgisi sifatida
        # ishlatadi (masalan LIKE ichidagi '%' wildcard bilan to'qnashib
        # qolib, "IndexError: tuple index out of range" kabi xato beradi).
        # Shuning uchun avval har qanday oddiy '%'ni '%%' qilib "escape"
        # qilamiz, KEYIN '?' larni '%s'ga almashtiramiz. Bu SQL matnidagi
        # '?' belgisi hech qachon haqiqiy qiymat sifatida ishlatilmagani
        # uchun xavfsiz (ilgari ham shunday edi).
        pg_sql = sql.replace("%", "%%").replace("?", "%s")
        cur.execute(pg_sql, params)
        return _CursorWrapper(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        # Pool'ga qaytarishdan oldin tugallanmagan tranzaksiyani tozalaymiz.
        # Agar allaqachon commit qilingan bo'lsa bu shunchaki no-op bo'ladi.
        try:
            self._conn.rollback()
        except Exception:
            pass
        _pool.putconn(self._conn)


def get_conn():
    raw = _pool.getconn()
    return _ConnWrapper(raw)


def _column_exists(conn, table, column):
    row = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=? AND column_name=?",
        (table, column),
    ).fetchone()
    return row is not None


def init_db():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     BIGINT PRIMARY KEY,
            username    TEXT,
            first_name  TEXT,
            balance     INTEGER DEFAULT 0,
            refs        INTEGER DEFAULT 0,
            ref_by      BIGINT DEFAULT NULL,
            created_at  BIGINT DEFAULT extract(epoch from now())::bigint
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS topup_requests (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT,
            amount      INTEGER,
            method      TEXT,
            status      TEXT DEFAULT 'pending',
            msg_id      BIGINT,
            created_at  BIGINT DEFAULT extract(epoch from now())::bigint
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS uc_orders (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT,
            pubg_id     TEXT,
            uc_amount   INTEGER,
            price       INTEGER,
            status      TEXT DEFAULT 'pending',
            msg_id      BIGINT,
            created_at  BIGINT DEFAULT extract(epoch from now())::bigint
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vip_orders (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT,
            vip_type    TEXT,
            price       INTEGER,
            status      TEXT DEFAULT 'pending',
            created_at  BIGINT DEFAULT extract(epoch from now())::bigint
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            user_id     BIGINT,
            task_key    TEXT,
            status      TEXT DEFAULT 'pending',
            PRIMARY KEY (user_id, task_key)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS earn_log (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT,
            amount      INTEGER,
            created_at  BIGINT DEFAULT extract(epoch from now())::bigint
        )
    """)

    # Maxsus (admin tomonidan qo'shiladigan) vazifalar ro'yxati
    conn.execute("""
        CREATE TABLE IF NOT EXISTS custom_tasks (
            id          BIGSERIAL PRIMARY KEY,
            title       TEXT,
            reward      INTEGER DEFAULT 0,
            url         TEXT DEFAULT NULL,
            icon_url    TEXT DEFAULT NULL,
            active      INTEGER DEFAULT 1,
            created_at  BIGINT DEFAULT extract(epoch from now())::bigint
        )
    """)

    # Foydalanuvchi "bajardim" deb yuborgan, admin tasdiqlashi kerak
    # bo'lgan vazifa so'rovlari (tg/ig/yt obuna, custom_tasks va h.k.)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS verify_requests (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT,
            task_key    TEXT,
            task_title  TEXT,
            reward      INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'pending',
            created_at  BIGINT DEFAULT extract(epoch from now())::bigint
        )
    """)

    # Omad g'ildiragi (Wheel of Fortune) - 5 kishilik teng ulush o'yin xonalari
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wheel_rooms (
            id          BIGSERIAL PRIMARY KEY,
            bet_amount  INTEGER NOT NULL,
            status      TEXT DEFAULT 'waiting',
            winner_id   BIGINT DEFAULT NULL,
            created_at  BIGINT DEFAULT extract(epoch from now())::bigint,
            spun_at     BIGINT DEFAULT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS wheel_players (
            id          BIGSERIAL PRIMARY KEY,
            room_id     BIGINT NOT NULL,
            user_id     BIGINT NOT NULL,
            first_name  TEXT,
            slot        INTEGER NOT NULL,
            joined_at   BIGINT DEFAULT extract(epoch from now())::bigint,
            UNIQUE (room_id, user_id)
        )
    """)

    # "Skin do'koni" — ramka va ism effektlari katalogi (bir martalik xarid)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cosmetic_items (
            id          TEXT PRIMARY KEY,
            category    TEXT NOT NULL,      -- 'frame' | 'color'
            name        TEXT NOT NULL,
            price       INTEGER NOT NULL,
            css_value   TEXT,               -- rang uchun gradient/hex, ramka uchun CSS klass kaliti
            icon        TEXT,               -- kartochkada ko'rinadigan emoji
            sort_order  INTEGER DEFAULT 0,
            active      INTEGER DEFAULT 1
        )
    """)

    # Foydalanuvchi sotib olgan skinlar (egalik yozuvi)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_cosmetics (
            user_id      BIGINT NOT NULL,
            item_id      TEXT NOT NULL,
            purchased_at BIGINT DEFAULT extract(epoch from now())::bigint,
            PRIMARY KEY (user_id, item_id)
        )
    """)

    conn.commit()

    # Eski bazalarda yo'q bo'lishi mumkin bo'lgan ustunlarni qo'shamiz
    if not _column_exists(conn, "users", "is_premium"):
        conn.execute("ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0")
    if not _column_exists(conn, "users", "premium_until"):
        conn.execute("ALTER TABLE users ADD COLUMN premium_until BIGINT DEFAULT NULL")
    if not _column_exists(conn, "users", "avatar_url"):
        conn.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT DEFAULT NULL")
    if not _column_exists(conn, "custom_tasks", "url"):
        conn.execute("ALTER TABLE custom_tasks ADD COLUMN url TEXT DEFAULT NULL")
    if not _column_exists(conn, "custom_tasks", "icon_url"):
        conn.execute("ALTER TABLE custom_tasks ADD COLUMN icon_url TEXT DEFAULT NULL")
    if not _column_exists(conn, "users", "equipped_frame"):
        conn.execute("ALTER TABLE users ADD COLUMN equipped_frame TEXT DEFAULT NULL")
    if not _column_exists(conn, "users", "equipped_color"):
        conn.execute("ALTER TABLE users ADD COLUMN equipped_color TEXT DEFAULT NULL")

    conn.commit()

    # "Skin do'koni" katalogini bir martalik urug'lantiramiz (agar hali
    # mavjud bo'lmasa) - narxlar keyinchalik admin panelidan yoki shu
    # yerdan osongina o'zgartirilishi mumkin.
    _seed_cosmetic_items(conn)

    conn.close()

def get_user(user_id: int):
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return user

def ensure_user(user_id: int, username: str = None, first_name: str = None, ref_by: int = None):
    """XAVFSIZLIK: avval SELECT bilan tekshirib keyin INSERT qilish o'rniga,
    bitta ATOMIK 'INSERT ... ON CONFLICT DO NOTHING' ishlatiladi. Aks holda
    bir xil yangi user_id uchun ikkita so'rov (masalan bot /start va webapp
    deyarli bir vaqtda) parallel kelsa, ikkalasi ham "bunday user yo'q" deb
    ko'rib, ikkalasi ham qo'shishga urinishi mumkin edi — bu referal sonini
    ikki marta oshirib yuborishi yoki xato berishi mumkin edi."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO users (user_id, username, first_name, ref_by) VALUES (?,?,?,?) "
        "ON CONFLICT (user_id) DO NOTHING RETURNING user_id",
        (user_id, username, first_name, ref_by)
    )
    inserted = cur.fetchone() is not None
    if inserted and ref_by:
        conn.execute("UPDATE users SET refs = refs + 1 WHERE user_id=?", (ref_by,))
    conn.commit()
    conn.close()

def get_all_user_ids() -> list:
    """Botga /start bosgan yoki webapp orqali kirgan (ensure_user chaqirilgan)
    barcha foydalanuvchilarning user_id larini qaytaradi. Broadcast (hammaga
    xabar yuborish) funksiyasi shundan foydalanadi."""
    conn = get_conn()
    rows = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    return [row["user_id"] for row in rows]

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
        WHERE user_id=? AND (to_timestamp(created_at) AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date
        """,
        (user_id,)
    ).fetchone()
    conn.close()
    return row["total"] if row else 0

def add_earn_tap(user_id: int, amount: int, cap: int = 500, boost_active: bool = False) -> dict:
    """Tanga bosishdan kelgan miqdorni qo'shadi. Kunlik limit HAMMA uchun
    bir xil (masalan 100). Faqat foydalanuvchida FAOL premium BOR va shu
    payt Boost x2 YOQILGAN bo'lsa, limit shu so'rov uchun 2 baravar
    (masalan 100 -> 200) qilib qo'llaniladi. Ya'ni Premium bo'lishning
    o'zi kifoya emas — kattaroq limitga yetish uchun Boost ham FAOL
    bo'lishi shart.

    XAVFSIZLIK: SQLite versiyasida bu yerda 'BEGIN IMMEDIATE' bilan yozish
    qulfi SELECT'dan OLDIN olinardi (parallel so'rovlar limitni "hali
    to'lmagan" deb ko'rib, cap'dan bir necha barobar ko'p yig'ib olmasligi
    uchun). Postgres'da xuddi shu maqsadda foydalanuvchining users
    jadvalidagi qatorini 'SELECT ... FOR UPDATE' bilan qulflaymiz — shu
    bilan bir xil user_id uchun parallel so'rovlar navbat bilan, bittalab
    qayta ishlanadi.
    """
    premium = is_premium_active(user_id)
    effective_cap = cap * 2 if (premium and boost_active) else cap

    conn = get_conn()
    try:
        conn.execute("SELECT balance FROM users WHERE user_id=? FOR UPDATE", (user_id,))

        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) as total FROM earn_log
            WHERE user_id=? AND (to_timestamp(created_at) AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date
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
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "added": actual_amount,
        "balance": new_balance,
        "earn_today": earned_today + actual_amount,
        "cap": effective_cap,
        "premium": premium
    }

def deduct_balance(user_id: int, amount: int) -> bool:
    """XAVFSIZLIK: balansni tekshirish va ayirish BITTA atomik SQL
    amalida bajariladi (WHERE balance >= amount). Avval SELECT bilan
    tekshirib keyin alohida UPDATE qilish — ikkisi orasida boshqa
    parallel so'rov ham xuddi shu balansni "yetarli" deb ko'rib,
    balансni manfiy qilib yuborishi mumkin edi (double-spend)."""
    if amount <= 0:
        return False
    conn = get_conn()
    cur = conn.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?",
        (amount, user_id, amount)
    )
    conn.commit()
    success = cur.rowcount > 0
    conn.close()
    return success

def create_topup(user_id: int, amount: int, method: str, msg_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO topup_requests (user_id, amount, method, msg_id) VALUES (?,?,?,?) RETURNING id",
        (user_id, amount, method, msg_id)
    )
    rid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return rid

def get_pending_topups():
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            t.id, t.user_id, t.amount, t.method,
            t.status, t.created_at,
            u.first_name, u.username
        FROM topup_requests t
        LEFT JOIN users u ON u.user_id = t.user_id
        WHERE t.status = 'pending'
        ORDER BY t.id DESC
    """).fetchall()
    conn.close()
    return rows

def get_recent_topups(limit: int = 50):
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            t.id, t.user_id, t.amount, t.method,
            t.status, t.created_at,
            u.first_name, u.username
        FROM topup_requests t
        LEFT JOIN users u ON u.user_id = t.user_id
        ORDER BY t.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return rows

def get_topup(request_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM topup_requests WHERE id=?", (request_id,)).fetchone()
    conn.close()
    return row

def reject_topup(request_id: int):
    """Pending topup so'rovini rad etadi (balansga hech narsa qo'shilmagani
    uchun qaytarib olish kerak emas — shunchaki statusni 'rejected' qiladi)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT status FROM topup_requests WHERE id=?", (request_id,)
    ).fetchone()
    if not row or row["status"] != "pending":
        conn.close()
        return False
    conn.execute(
        "UPDATE topup_requests SET status='rejected' WHERE id=?", (request_id,)
    )
    conn.commit()
    conn.close()
    return True


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
        "INSERT INTO uc_orders (user_id, pubg_id, uc_amount, price, msg_id) VALUES (?,?,?,?,?) RETURNING id",
        (user_id, pubg_id, uc_amount, price, msg_id)
    )
    oid = cur.fetchone()["id"]
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
    """Buyurtmani bekor qiladi VA pulni bir xil atomik tranzaksiyada
    qaytaradi. SQLite versiyasida 'BEGIN IMMEDIATE' bilan yozish qulfi
    olinardi; Postgres'da buning o'rniga shu order qatorini to'g'ridan-
    to'g'ri 'SELECT ... FOR UPDATE' bilan qulflaymiz — natija bir xil:
    bitta orderni ikki marta bekor qilib, pulni ikki marta qaytarish
    imkonsiz bo'ladi."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT user_id, price, status FROM uc_orders WHERE id=? FOR UPDATE", (order_id,)
        ).fetchone()
        if not row or row["status"] != "pending":
            conn.rollback()
            conn.close()
            return None
        conn.execute("UPDATE uc_orders SET status='cancelled' WHERE id=?", (order_id,))
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (row["price"], row["user_id"])
        )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
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
        "INSERT INTO vip_orders (user_id, vip_type, price, status) VALUES (?,?,?,'approved') RETURNING id",
        (user_id, vip_type, price)
    )
    oid = cur.fetchone()["id"]
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
        "INSERT INTO tasks (user_id, task_key, status) VALUES (?,?,'done') "
        "ON CONFLICT (user_id, task_key) DO UPDATE SET status='done'",
        (user_id, task_key)
    )
    conn.commit()
    conn.close()


# ── CUSTOM TASKS (vazifalar) ─────────────────────────────────
def create_custom_task(title: str, reward: int, url: str = None, icon_url: str = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO custom_tasks (title, reward, url, icon_url) VALUES (?,?,?,?) RETURNING id",
        (title, reward, url, icon_url)
    )
    tid = cur.fetchone()["id"]
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
        "INSERT INTO verify_requests (user_id, task_key, task_title, reward) VALUES (?,?,?,?) RETURNING id",
        (user_id, task_key, task_title, reward)
    )
    rid = cur.fetchone()["id"]
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
        "INSERT INTO tasks (user_id, task_key, status) VALUES (?,?,'done') "
        "ON CONFLICT (user_id, task_key) DO UPDATE SET status='done'",
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
    "3oy": 90 * 86400,
    "1yil": 365 * 86400,
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


# ── OMAD G'ILDIRAGI (Wheel of Fortune) ───────────────────────────────────
# 5 kishilik teng-shartli o'yin: hammasi bir xil miqdorda pul qo'yadi,
# xona to'lgach tasodifiy bittasi butun potni (5 x miqdor) yutib oladi.
# Turli miqdor qo'ygan o'yinchilar aralashmasligi uchun har bir miqdor
# (1000 / 5000 / 10000) o'z navbatida alohida xonalarda yig'iladi -
# xuddi PUBG'dagi kabi "teng darajadagilar" bir xonaga tushadi.

WHEEL_ROOM_SIZE = 5
WHEEL_BET_AMOUNTS = (1000, 5000, 10000)


def join_wheel_room(user_id: int, first_name: str, bet_amount: int) -> dict:
    """Foydalanuvchini shu miqdordagi navbatdagi ochiq xonaga qo'shadi
    (yoki yangisini ochadi), balансdan darhol pulni yechadi. Bularning
    barchasi bitta atomik tranzaksiyada bajariladi - shu bilan bir xil
    miqdorga bir vaqtning o'zida bir nechta odam kirmoqchi bo'lsa ham,
    xonaga hech qachon 5 tadan ortiq odam tushib qolmaydi (advisory lock
    shu pul miqdori uchun navbatni serializatsiya qiladi)."""
    if bet_amount not in WHEEL_BET_AMOUNTS:
        return {"error": "invalid_amount"}

    conn = get_conn()
    try:
        already = conn.execute(
            "SELECT wp.room_id FROM wheel_players wp "
            "JOIN wheel_rooms wr ON wr.id = wp.room_id "
            "WHERE wp.user_id=? AND wr.status IN ('waiting','ready') LIMIT 1",
            (user_id,)
        ).fetchone()
        if already:
            conn.rollback()
            conn.close()
            return {"error": "already_in_room", "room_id": already["room_id"]}

        # Shu pul miqdori uchun xona tanlash/ochishni serializatsiya qilamiz
        conn.execute("SELECT pg_advisory_xact_lock(?)", (bet_amount,))

        room = conn.execute(
            "SELECT id FROM wheel_rooms WHERE status='waiting' AND bet_amount=? "
            "ORDER BY created_at ASC LIMIT 1",
            (bet_amount,)
        ).fetchone()

        if room:
            room_id = room["id"]
        else:
            cur = conn.execute(
                "INSERT INTO wheel_rooms (bet_amount, status) VALUES (?, 'waiting') RETURNING id",
                (bet_amount,)
            )
            room_id = cur.fetchone()["id"]

        count_row = conn.execute(
            "SELECT COUNT(*) AS c FROM wheel_players WHERE room_id=?", (room_id,)
        ).fetchone()
        slot = count_row["c"] + 1

        deduct = conn.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?",
            (bet_amount, user_id, bet_amount)
        )
        if deduct.rowcount == 0:
            conn.rollback()
            conn.close()
            return {"error": "insufficient_balance"}

        conn.execute(
            "INSERT INTO wheel_players (room_id, user_id, first_name, slot) VALUES (?,?,?,?)",
            (room_id, user_id, first_name or "O'yinchi", slot)
        )

        room_full = slot >= WHEEL_ROOM_SIZE
        if room_full:
            conn.execute("UPDATE wheel_rooms SET status='ready' WHERE id=?", (room_id,))

        conn.commit()
        conn.close()
        return {"room_id": room_id, "slot": slot, "full": room_full}
    except Exception:
        conn.rollback()
        conn.close()
        raise


def get_wheel_room(room_id: int, user_id: int = None):
    """Xona holati va o'yinchilar ro'yxatini qaytaradi. Agar xona 'ready'
    holatda va hali aylantirilmagan bo'lsa - shu yerning o'zida g'olibni
    tasodifiy tanlab, potni g'olib hisobiga o'tkazadi (birinchi kim
    so'rasa o'sha spin natijasini "ochadi", shu bilan barcha 5 o'yinchi
    bir xil natijani ko'radi)."""
    conn = get_conn()
    room = conn.execute("SELECT * FROM wheel_rooms WHERE id=?", (room_id,)).fetchone()
    if not room:
        conn.close()
        return None

    if room["status"] == "ready":
        conn.execute("SELECT pg_advisory_xact_lock(?)", (room_id,))
        room = conn.execute("SELECT * FROM wheel_rooms WHERE id=?", (room_id,)).fetchone()
        if room["status"] == "ready":
            players = conn.execute(
                "SELECT user_id FROM wheel_players WHERE room_id=? ORDER BY slot ASC",
                (room_id,)
            ).fetchall()
            winner_id = random.choice([p["user_id"] for p in players])
            # DIQQAT: pot doim 5 kishilik deb hisoblanmaydi - admin xonani
            # 5 kishi to'lmasdan turib majburan boshlab yuborishi mumkin
            # (force_start_wheel_room), shu sabab pot haqiqatda qo'shilgan
            # (va pul to'lagan) o'yinchilar soniga qarab hisoblanadi.
            pot = room["bet_amount"] * len(players)
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id=?",
                (pot, winner_id)
            )
            conn.execute(
                "UPDATE wheel_rooms SET status='completed', winner_id=?, spun_at=extract(epoch from now())::bigint WHERE id=?",
                (winner_id, room_id)
            )
            conn.commit()
            room = conn.execute("SELECT * FROM wheel_rooms WHERE id=?", (room_id,)).fetchone()

    players = conn.execute(
        "SELECT wp.user_id, wp.first_name, wp.slot, u.avatar_url "
        "FROM wheel_players wp LEFT JOIN users u ON u.user_id = wp.user_id "
        "WHERE wp.room_id=? ORDER BY wp.slot ASC",
        (room_id,)
    ).fetchall()
    conn.close()

    return {
        "room_id": room["id"],
        "bet_amount": room["bet_amount"],
        "status": room["status"],
        "winner_id": room["winner_id"],
        "players": [dict(p) for p in players],
    }


def leave_wheel_room(user_id: int, room_id: int) -> dict:
    """Foydalanuvchi hali xona to'lmasdan turib chiqib ketmoqchi bo'lsa -
    o'yinchi ro'yxatidan olib tashlanadi va pul to'liq qaytariladi.
    Xona 'ready'/'completed' bo'lsa endi chiqib bo'lmaydi."""
    conn = get_conn()
    room = conn.execute("SELECT status, bet_amount FROM wheel_rooms WHERE id=?", (room_id,)).fetchone()
    if not room or room["status"] != "waiting":
        conn.rollback()
        conn.close()
        return {"error": "cannot_leave"}

    deleted = conn.execute(
        "DELETE FROM wheel_players WHERE room_id=? AND user_id=?", (room_id, user_id)
    )
    if deleted.rowcount == 0:
        conn.rollback()
        conn.close()
        return {"error": "not_in_room"}

    conn.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (room["bet_amount"], user_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}


def force_start_wheel_room(room_id: int) -> dict:
    """FAQAT admin uchun: xona hali 5 kishi bilan to'lmagan ('waiting')
    bo'lsa ham, hozirgi o'yinchilar bilan darhol 'ready' holatiga
    o'tkazadi - shu bilan charxpalak keyingi so'rovda (poll) darhol
    aylana boshlaydi. Kim admin ekanligini tekshirish butunlay chaqiruvchi
    tomonda (webapp_api.py, ADMIN_ID orqali) amalga oshiriladi - bu
    funksiya faqat holatni o'zgartiradi."""
    conn = get_conn()
    try:
        room = conn.execute(
            "SELECT status FROM wheel_rooms WHERE id=?", (room_id,)
        ).fetchone()
        if not room or room["status"] != "waiting":
            conn.rollback()
            conn.close()
            return {"error": "cannot_force"}

        count_row = conn.execute(
            "SELECT COUNT(*) AS c FROM wheel_players WHERE room_id=?", (room_id,)
        ).fetchone()
        if count_row["c"] < 1:
            conn.rollback()
            conn.close()
            return {"error": "no_players"}

        conn.execute("UPDATE wheel_rooms SET status='ready' WHERE id=?", (room_id,))
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception:
        conn.rollback()
        conn.close()
        raise


def _seed_cosmetic_items(conn):
    """'Skin do'koni' uchun boshlang'ich katalog - 6 ramka + 6 ism effekti.
    Narxlar boshlang'ich taxminiy qiymatlar, keyinchalik osongina
    o'zgartirilishi mumkin (shu jadvaldagi 'price' ustunini yangilash
    orqali). ON CONFLICT DO NOTHING - qayta ishga tushirilganda eski
    narxlar ustidan yozib yubormaydi."""
    frames = [
        ("frame_bronze",  "Bronza ramka",  3000,  "linear-gradient(135deg,#cd7f32,#8a5a2b)", "🥉", 1),
        ("frame_silver",  "Kumush ramka",  5000,  "linear-gradient(135deg,#e8e8e8,#9a9a9a)", "🥈", 2),
        ("frame_gold",    "Oltin ramka",   8000,  "linear-gradient(135deg,#ffd76a,#c9960c)", "🥇", 3),
        ("frame_neon",    "Neon ramka",    10000, "linear-gradient(135deg,#00e5ff,#0077ff)", "💠", 4),
        ("frame_diamond", "Olmos ramka",   15000, "linear-gradient(135deg,#b6f2ff,#4fd3ff)", "💎", 5),
        ("frame_flame",   "Otashin ramka", 20000, "linear-gradient(135deg,#ff5f3c,#ff2a68)", "🔥", 6),
    ]
    colors = [
        ("color_blue",   "Ko'k neon",     3000,  "#00E5FF", "🔵", 1),
        ("color_red",    "Qizil olov",    3000,  "#FF3B5C", "🔴", 2),
        ("color_green",  "Yashil zumrad", 3000,  "#3EE08A", "🟢", 3),
        ("color_purple", "Binafsha",      3000,  "#B98CFF", "🟣", 4),
        ("color_gold",   "Tilla gradient",8000,  "linear-gradient(90deg,#ffd76a,#ff9d3c)", "🟡", 5),
        ("color_rainbow","Kamalak",       12000, "rainbow", "🌈", 6),
    ]
    for item_id, name, price, css_value, icon, order in frames:
        conn.execute(
            "INSERT INTO cosmetic_items (id, category, name, price, css_value, icon, sort_order) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
            (item_id, "frame", name, price, css_value, icon, order)
        )
    for item_id, name, price, css_value, icon, order in colors:
        conn.execute(
            "INSERT INTO cosmetic_items (id, category, name, price, css_value, icon, sort_order) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
            (item_id, "color", name, price, css_value, icon, order)
        )
    conn.commit()


def get_cosmetic_catalog(user_id: int) -> dict:
    """Barcha faol skin narsalarini, ularning 'sotib olinganmi' va
    'kiyilganmi' holatini birga qaytaradi - frontend bitta so'rov bilan
    to'liq do'kon oynasini chiza oladi."""
    conn = get_conn()
    items = conn.execute(
        "SELECT id, category, name, price, css_value, icon FROM cosmetic_items "
        "WHERE active=1 ORDER BY category, sort_order ASC"
    ).fetchall()
    owned_rows = conn.execute(
        "SELECT item_id FROM user_cosmetics WHERE user_id=?", (user_id,)
    ).fetchall()
    owned_ids = {r["item_id"] for r in owned_rows}
    user = conn.execute(
        "SELECT equipped_frame, equipped_color FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()

    equipped_frame = user["equipped_frame"] if user else None
    equipped_color = user["equipped_color"] if user else None

    result = []
    for it in items:
        d = dict(it)
        d["owned"] = it["id"] in owned_ids
        d["equipped"] = (it["id"] == equipped_frame) or (it["id"] == equipped_color)
        result.append(d)
    return {"items": result}


def buy_cosmetic(user_id: int, item_id: str) -> dict:
    """Bitta skin narsasini bir martalik sotib oladi - balансdan yechadi
    va egalik yozuvini qo'shadi. Bitta atomik tranzaksiyada."""
    conn = get_conn()
    try:
        item = conn.execute(
            "SELECT price FROM cosmetic_items WHERE id=? AND active=1", (item_id,)
        ).fetchone()
        if not item:
            conn.rollback(); conn.close()
            return {"error": "not_found"}

        already = conn.execute(
            "SELECT 1 FROM user_cosmetics WHERE user_id=? AND item_id=?", (user_id, item_id)
        ).fetchone()
        if already:
            conn.rollback(); conn.close()
            return {"error": "already_owned"}

        deduct = conn.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?",
            (item["price"], user_id, item["price"])
        )
        if deduct.rowcount == 0:
            conn.rollback(); conn.close()
            return {"error": "insufficient_balance"}

        conn.execute(
            "INSERT INTO user_cosmetics (user_id, item_id) VALUES (?,?)",
            (user_id, item_id)
        )
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception:
        conn.rollback(); conn.close()
        raise


def equip_cosmetic(user_id: int, item_id) -> dict:
    """Sotib olingan skinni 'kiyadi' (yoki item_id=None bo'lsa - o'sha
    turdagi skinni yechadi). Faqat egalik qilingan narsa kiyilishi
    mumkin. Ramka va rang alohida-alohida kiyiladi (ikkisini bir vaqtda
    tanlash mumkin)."""
    conn = get_conn()
    try:
        if item_id is not None:
            owned = conn.execute(
                "SELECT ci.category FROM user_cosmetics uc "
                "JOIN cosmetic_items ci ON ci.id = uc.item_id "
                "WHERE uc.user_id=? AND uc.item_id=?", (user_id, item_id)
            ).fetchone()
            if not owned:
                conn.rollback(); conn.close()
                return {"error": "not_owned"}
            category = owned["category"]
            column = "equipped_frame" if category == "frame" else "equipped_color"
            conn.execute(f"UPDATE users SET {column} = ? WHERE user_id=?", (item_id, user_id))
        else:
            # item_id yo'q - qaysi kategoriyani yechish kerakligi alohida berilishi kerak,
            # shu sabab bu holatda hech narsa qilmaymiz (frontend to'g'ridan-to'g'ri
            # bo'sh qiymat bilan chaqirmasligi kerak; equip_category ishlatiladi).
            conn.rollback(); conn.close()
            return {"error": "invalid_call"}
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception:
        conn.rollback(); conn.close()
        raise


def unequip_cosmetic_category(user_id: int, category: str) -> dict:
    """Berilgan kategoriyadagi (frame/color) skinni yechadi."""
    if category not in ("frame", "color"):
        return {"error": "invalid_category"}
    column = "equipped_frame" if category == "frame" else "equipped_color"
    conn = get_conn()
    conn.execute(f"UPDATE users SET {column} = NULL WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


def get_wheel_waiting_counts() -> dict:
    """Har bir tikish miqdori uchun eng eski 'waiting' xonada hozir nechta
    o'yinchi borligini qaytaradi (masalan {1000: 2, 5000: 0, 10000: 4}).
    Faqat 'waiting' holatidagi xonalar hisobga olinadi - foydalanuvchi
    hali qo'shilmasdan turib qaysi summada odam ko'proq ekanini ko'rishi
    uchun ishlatiladi."""
    conn = get_conn()
    counts = {amt: 0 for amt in WHEEL_BET_AMOUNTS}
    for amt in WHEEL_BET_AMOUNTS:
        room = conn.execute(
            "SELECT id FROM wheel_rooms WHERE status='waiting' AND bet_amount=? "
            "ORDER BY created_at ASC LIMIT 1",
            (amt,)
        ).fetchone()
        if room:
            count_row = conn.execute(
                "SELECT COUNT(*) AS c FROM wheel_players WHERE room_id=?", (room["id"],)
            ).fetchone()
            counts[amt] = count_row["c"]
    conn.close()
    return counts


def get_user_active_wheel_room(user_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT wp.room_id FROM wheel_players wp "
        "JOIN wheel_rooms wr ON wr.id = wp.room_id "
        "WHERE wp.user_id=? AND wr.status IN ('waiting','ready') LIMIT 1",
        (user_id,)
    ).fetchone()
    conn.close()
    return row["room_id"] if row else None
