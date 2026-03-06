from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from database import get_user, get_all_users
from exchange import rates, exchange
from admin import add_promocode, get_promocodes, ADMIN_ID

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.get("/api/user/{uid}")
def user(uid:int):

    u = get_user(uid)

    return {
        "rub":u["balance_rub"],
        "usdt":u["balance_usdt"],
        "ton":u["balance_ton"]
    }


@app.get("/api/rates")
def get_rates():

    return rates()


@app.post("/api/exchange")
async def do_exchange(request:Request):

    d = await request.json()

    received = exchange(
        d["user_id"],
        d["from"],
        d["to"],
        float(d["amount"])
    )

    return {"received":received}


@app.get("/api/admin/users/{uid}")
def admin_users(uid:int):

    if uid != ADMIN_ID:
        return {"error":"access denied"}

    return get_all_users()


@app.post("/api/admin/addpromo")
async def addpromo(request:Request):

    d = await request.json()

    if d["admin"] != ADMIN_ID:
        return {"error":"access denied"}

    add_promocode(d["code"], d["amount"])

    return {"ok":True}


@app.get("/api/admin/promos/{uid}")
def promos(uid:int):

    if uid != ADMIN_ID:
        return {"error":"access denied"}

    return get_promocodes()
