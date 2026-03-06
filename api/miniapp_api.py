from flask import Flask, request, jsonify
import sqlite3
import requests
import time

app = Flask(__name__)

BOT_TOKEN = "7762911922:AAHdyGVZRwCkI_WtcGW1MPbIdhrcDBpKNvE"
ADMIN_ID = 896706118

DB = "database.db"


def db():
    return sqlite3.connect(DB)


def init():

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0,
        ref INTEGER,
        banned INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS promocodes(
        code TEXT PRIMARY KEY,
        amount REAL,
        uses INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user INTEGER,
        type TEXT,
        amount REAL,
        time INTEGER
    )
    """)

    conn.commit()
    conn.close()


init()


# ========================
# КУРСЫ ВАЛЮТ
# ========================

def get_rates():

    r = requests.get(
        "https://open.er-api.com/v6/latest/USD"
    ).json()

    return r["rates"]


# ========================
# USER
# ========================

@app.route("/api/user")
def user():

    uid = int(request.args.get("id"))
    username = request.args.get("username", "")

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE id=?", (uid,))
    u = cur.fetchone()

    if not u:

        cur.execute("""
        INSERT INTO users(id,username,balance)
        VALUES(?,?,0)
        """, (uid, username))

        conn.commit()

    cur.execute("SELECT balance FROM users WHERE id=?", (uid,))
    balance = cur.fetchone()[0]

    conn.close()

    return jsonify({
        "balance": balance
    })


# ========================
# EXCHANGE
# ========================

@app.route("/api/exchange", methods=["POST"])
def exchange():

    data = request.json

    uid = int(data["id"])
    amount = float(data["amount"])

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT balance FROM users WHERE id=?", (uid,))
    bal = cur.fetchone()[0]

    if bal < amount:
        return jsonify({"error": "balance"})

    cur.execute("""
    UPDATE users
    SET balance=balance-?
    WHERE id=?
    """, (amount, uid))

    cur.execute("""
    INSERT INTO history(user,type,amount,time)
    VALUES(?,?,?,?)
    """, (uid, "exchange", amount, int(time.time())))

    conn.commit()
    conn.close()

    return jsonify({"ok": True})


# ========================
# PROMO
# ========================

@app.route("/api/promo", methods=["POST"])
def promo():

    data = request.json
    uid = int(data["id"])
    code = data["code"]

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT amount,uses FROM promocodes WHERE code=?",
        (code,)
    )

    p = cur.fetchone()

    if not p:
        return jsonify({"error": "invalid"})

    amount, uses = p

    if uses <= 0:
        return jsonify({"error": "ended"})

    cur.execute("""
    UPDATE users
    SET balance=balance+?
    WHERE id=?
    """, (amount, uid))

    cur.execute("""
    UPDATE promocodes
    SET uses=uses-1
    WHERE code=?
    """, (code,))

    conn.commit()
    conn.close()

    return jsonify({"ok": True})


# ========================
# ADMIN
# ========================

@app.route("/api/admin/users")
def admin_users():

    admin = int(request.args.get("admin"))

    if admin != ADMIN_ID:
        return jsonify({"error": "access"})

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id,username,balance FROM users")

    users = cur.fetchall()

    conn.close()

    return jsonify(users)


@app.route("/api/admin/createpromo", methods=["POST"])
def create_promo():

    data = request.json

    if int(data["admin"]) != ADMIN_ID:
        return jsonify({"error": "access"})

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO promocodes
    VALUES(?,?,?)
    """, (
        data["code"],
        data["amount"],
        data["uses"]
    ))

    conn.commit()
    conn.close()

    return jsonify({"ok": True})
