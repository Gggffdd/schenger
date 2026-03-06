from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import os

app = FastAPI()

# разрешаем запросы от Mini App
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB = "universal_exchange.db"


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------
# FRONTEND FILES
# ------------------------

@app.get("/")
def root():
    return FileResponse("webapp.html")


@app.get("/webapp.html")
def webapp():
    return FileResponse("webapp.html")


@app.get("/webapp.js")
def webapp_js():
    return FileResponse("webapp.js")


# ------------------------
# USER PROFILE
# ------------------------

@app.get("/user/{user_id}")
def get_user(user_id: int):

    try:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
        SELECT user_id,
               username,
               balance_rub,
               balance_usdt,
               balance_ton
        FROM users
        WHERE user_id=?
        """, (user_id,))

        user = cur.fetchone()

        if not user:
            return JSONResponse(
                {"error": "User not found"},
                status_code=404
            )

        return {
            "id": user["user_id"],
            "username": user["username"],
            "rub": user["balance_rub"],
            "usdt": user["balance_usdt"],
            "ton": user["balance_ton"]
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ------------------------
# EXCHANGE RATES
# ------------------------

@app.get("/rates")
def get_rates():

    try:

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
        SELECT rub_usdt,
               rub_ton,
               usdt_ton
        FROM rates
        LIMIT 1
        """)

        r = cur.fetchone()

        if not r:
            return JSONResponse(
                {"error": "Rates not found"},
                status_code=404
            )

        return {
            "rub_usdt": r["rub_usdt"],
            "rub_ton": r["rub_ton"],
            "usdt_ton": r["usdt_ton"]
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ------------------------
# EXCHANGE
# ------------------------

@app.post("/exchange")
async def exchange(request: Request):

    try:

        data = await request.json()

        user_id = int(data["user_id"])
        from_cur = data["from"]
        to_cur = data["to"]
        amount = float(data["amount"])

        conn = get_db()
        cur = conn.cursor()

        # получаем курсы
        cur.execute("""
        SELECT rub_usdt,
               rub_ton,
               usdt_ton
        FROM rates
        LIMIT 1
        """)

        r = cur.fetchone()

        rate_map = {
            ("rub", "usdt"): r["rub_usdt"],
            ("rub", "ton"): r["rub_ton"],
            ("usdt", "ton"): r["usdt_ton"],
            ("usdt", "rub"): 1 / r["rub_usdt"],
            ("ton", "rub"): 1 / r["rub_ton"],
            ("ton", "usdt"): 1 / r["usdt_ton"]
        }

        pair = (from_cur, to_cur)

        if pair not in rate_map:
            return JSONResponse(
                {"error": "Currency pair not supported"},
                status_code=400
            )

        rate = rate_map[pair]

        received = amount * rate

        # списываем
        cur.execute(
            f"UPDATE users SET balance_{from_cur}=balance_{from_cur}-? WHERE user_id=?",
            (amount, user_id)
        )

        # начисляем
        cur.execute(
            f"UPDATE users SET balance_{to_cur}=balance_{to_cur}+? WHERE user_id=?",
            (received, user_id)
        )

        conn.commit()

        return {
            "success": True,
            "rate": rate,
            "received": received
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
