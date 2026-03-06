from database import db

ADMIN_ID = 123456789  # сюда впиши свой Telegram ID


def add_promocode(code, amount):

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO promocodes (code,amount) VALUES (?,?)",
        (code,amount)
    )

    conn.commit()


def get_promocodes():

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM promocodes")

    return cur.fetchall()
