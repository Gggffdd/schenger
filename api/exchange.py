from database import db

def rates():

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT rub_usdt,rub_ton,usdt_ton FROM rates LIMIT 1")

    r = cur.fetchone()

    return {
        "rub_usdt": r["rub_usdt"],
        "rub_ton": r["rub_ton"],
        "usdt_ton": r["usdt_ton"]
    }


def exchange(user_id, from_cur, to_cur, amount):

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT rub_usdt,rub_ton,usdt_ton FROM rates LIMIT 1")
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

    return received
