from fastapi import FastAPI
import sqlite3

app = FastAPI()

DB = "universal_exchange.db"


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/user/{user_id}")
def get_user(user_id: int):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT user_id, username,
    balance_rub,
    balance_usdt,
    balance_ton
    FROM users
    WHERE user_id=?
    """, (user_id,))

    u = cur.fetchone()

    return {
        "id": u["user_id"],
        "username": u["username"],
        "rub": u["balance_rub"],
        "usdt": u["balance_usdt"],
        "ton": u["balance_ton"]
    }


@app.get("/rates")
def rates():

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT rub_usdt, rub_ton, usdt_ton FROM rates LIMIT 1")
    r = cur.fetchone()

    return {
        "rub_usdt": r["rub_usdt"],
        "rub_ton": r["rub_ton"],
        "usdt_ton": r["usdt_ton"]
    }


@app.post("/exchange")
def exchange(data: dict):

    user_id = data["user_id"]
    from_cur = data["from"]
    to_cur = data["to"]
    amount = float(data["amount"])

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT rub_usdt, rub_ton, usdt_ton FROM rates LIMIT 1")
    r = cur.fetchone()

    rate_map = {
        ("rub","usdt"): r["rub_usdt"],
        ("rub","ton"): r["rub_ton"],
        ("usdt","ton"): r["usdt_ton"],
        ("usdt","rub"): 1/r["rub_usdt"],
        ("ton","rub"): 1/r["rub_ton"],
        ("ton","usdt"): 1/r["usdt_ton"]
    }

    rate = rate_map[(from_cur,to_cur)]

    received = amount * rate

    cur.execute(
        f"UPDATE users SET balance_{from_cur}=balance_{from_cur}-? WHERE user_id=?",
        (amount,user_id)
    )

    cur.execute(
        f"UPDATE users SET balance_{to_cur}=balance_{to_cur}+? WHERE user_id=?",
        (received,user_id)
    )

    conn.commit()

    return {"received": received}
