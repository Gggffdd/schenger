import sqlite3

DB = "universal_exchange.db"

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_user(user_id):

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    u = cur.fetchone()

    if not u:

        cur.execute("""
        INSERT INTO users
        (user_id,balance_rub,balance_usdt,balance_ton)
        VALUES (?,0,0,0)
        """, (user_id,))

        conn.commit()

        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        u = cur.fetchone()

    return u


def get_all_users():

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT user_id,balance_rub,balance_usdt,balance_ton FROM users")

    return cur.fetchall()
