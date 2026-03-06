import os
import time
import json
import logging
from datetime import datetime, timedelta
from functools import wraps
from threading import Lock

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import telegram

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN", "7762911922:AAHdyGVZRwCkI_WtcGW1MPbIdhrcDBpKNvE")
ADMIN_ID = int(os.getenv("ADMIN_ID", 896706118))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///database.db")
CURRENCY_API_URL = "https://open.er-api.com/v6/latest/USD"
CURRENCY_CACHE_TIME = 600  # 10 минут

# Инициализация Flask
app = Flask(__name__)
CORS(app)  # Для разработки, можно убрать в production

# Глобальный кэш курсов валют
rates_cache = {"rates": {}, "last_update": 0}
cache_lock = Lock()

# Инициализация бота Telegram (для уведомлений)
bot = telegram.Bot(token=BOT_TOKEN)

# ========================
# Работа с БД (адаптер)
# ========================

def get_db_connection():
    """Возвращает соединение с БД (PostgreSQL или SQLite)."""
    if DATABASE_URL.startswith("postgres"):
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    else:
        # SQLite fallback
        import sqlite3
        conn = sqlite3.connect(DATABASE_URL.replace("sqlite:///", ""))
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    """Создание таблиц, если их нет."""
    conn = get_db_connection()
    cur = conn.cursor()

    if DATABASE_URL.startswith("postgres"):
        # PostgreSQL
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                username TEXT,
                balance_usd REAL DEFAULT 0,
                referrer_id BIGINT,
                banned BOOLEAN DEFAULT FALSE,
                daily_bonus_claimed TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                amount_usd REAL,
                uses_left INTEGER,
                created_by BIGINT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                type TEXT,
                amount_usd REAL,
                details TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT,
                referred_id BIGINT UNIQUE,
                bonus_paid BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
    else:
        # SQLite
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                username TEXT,
                balance_usd REAL DEFAULT 0,
                referrer_id INTEGER,
                banned INTEGER DEFAULT 0,
                daily_bonus_claimed INTEGER,
                created_at INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS promocodes(
                code TEXT PRIMARY KEY,
                amount_usd REAL,
                uses_left INTEGER,
                created_by INTEGER,
                created_at INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount_usd REAL,
                details TEXT,
                created_at INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER UNIQUE,
                bonus_paid INTEGER DEFAULT 0,
                created_at INTEGER
            )
        """)
    conn.commit()
    conn.close()
    logger.info("Database initialized")

# Инициализация при старте
init_db()

# ========================
# Вспомогательные функции
# ========================

def get_rates(force_refresh=False):
    """Возвращает актуальные курсы валют с кэшированием."""
    global rates_cache
    now = time.time()
    with cache_lock:
        if force_refresh or (now - rates_cache["last_update"] > CURRENCY_CACHE_TIME):
            try:
                resp = requests.get(CURRENCY_API_URL, timeout=5)
                data = resp.json()
                rates_cache["rates"] = data["rates"]
                rates_cache["last_update"] = now
                logger.info("Currency rates updated")
            except Exception as e:
                logger.error(f"Failed to fetch rates: {e}")
                # Если не получили, оставляем старые
    return rates_cache["rates"]

def send_admin_notification(text):
    """Отправляет уведомление админу через Telegram."""
    try:
        bot.send_message(chat_id=ADMIN_ID, text=text)
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

def admin_required(f):
    """Декоратор для проверки прав администратора."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_id = request.args.get("admin") or (request.json and request.json.get("admin"))
        if not admin_id or int(admin_id) != ADMIN_ID:
            return jsonify({"error": "Access denied"}), 403
        return f(*args, **kwargs)
    return decorated_function

def get_or_create_user(uid, username=None, referrer=None):
    """Получает пользователя из БД или создаёт нового."""
    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL.startswith("postgres"):
        cur.execute("SELECT * FROM users WHERE id = %s", (uid,))
    else:
        cur.execute("SELECT * FROM users WHERE id = ?", (uid,))
    user = cur.fetchone()
    if not user:
        # Создаём нового
        now = int(time.time()) if not DATABASE_URL.startswith("postgres") else datetime.now()
        if DATABASE_URL.startswith("postgres"):
            cur.execute("""
                INSERT INTO users (id, username, referrer_id, created_at)
                VALUES (%s, %s, %s, %s)
            """, (uid, username, referrer, now))
        else:
            cur.execute("""
                INSERT INTO users (id, username, referrer_id, created_at)
                VALUES (?, ?, ?, ?)
            """, (uid, username, referrer, now))
        conn.commit()
        # Если есть реферер, начисляем бонус
        if referrer:
            # Проверяем, что реферер существует и не забанен
            if DATABASE_URL.startswith("postgres"):
                cur.execute("SELECT id FROM users WHERE id = %s AND banned = FALSE", (referrer,))
            else:
                cur.execute("SELECT id FROM users WHERE id = ? AND banned = 0", (referrer,))
            if cur.fetchone():
                # Начисляем бонус рефереру
                bonus = 5.0  # Например, 5 USD
                if DATABASE_URL.startswith("postgres"):
                    cur.execute("UPDATE users SET balance_usd = balance_usd + %s WHERE id = %s", (bonus, referrer))
                    cur.execute("""
                        INSERT INTO referrals (referrer_id, referred_id, bonus_paid)
                        VALUES (%s, %s, TRUE)
                    """, (referrer, uid))
                else:
                    cur.execute("UPDATE users SET balance_usd = balance_usd + ? WHERE id = ?", (bonus, referrer))
                    cur.execute("""
                        INSERT INTO referrals (referrer_id, referred_id, bonus_paid)
                        VALUES (?, ?, 1)
                    """, (referrer, uid))
                # Запись в историю
                if DATABASE_URL.startswith("postgres"):
                    cur.execute("""
                        INSERT INTO history (user_id, type, amount_usd, details)
                        VALUES (%s, 'referral_bonus', %s, %s)
                    """, (referrer, bonus, f"Referred user {uid}"))
                else:
                    cur.execute("""
                        INSERT INTO history (user_id, type, amount_usd, details, created_at)
                        VALUES (?, 'referral_bonus', ?, ?, ?)
                    """, (referrer, bonus, f"Referred user {uid}", int(time.time())))
                conn.commit()
                send_admin_notification(f"🎉 Новый реферал! {uid} приглашён пользователем {referrer}")
        # Отправляем уведомление админу о новом пользователе
        send_admin_notification(f"🆕 Новый пользователь: @{username} (ID: {uid})")
        # Получаем созданного пользователя
        if DATABASE_URL.startswith("postgres"):
            cur.execute("SELECT * FROM users WHERE id = %s", (uid,))
        else:
            cur.execute("SELECT * FROM users WHERE id = ?", (uid,))
        user = cur.fetchone()
    conn.close()
    return user

# ========================
# Эндпоинты API
# ========================

@app.route("/api/user", methods=["GET"])
def api_user():
    """Возвращает информацию о пользователе (баланс, username, реферальная ссылка)."""
    uid = request.args.get("id", type=int)
    username = request.args.get("username", "")
    referrer = request.args.get("ref", type=int)  # реферальный параметр при первом заходе

    if not uid:
        return jsonify({"error": "Missing id"}), 400

    user = get_or_create_user(uid, username, referrer)

    # Формируем ответ
    result = {
        "id": user["id"],
        "username": user["username"],
        "balance_usd": user["balance_usd"],
        "banned": user["banned"] if DATABASE_URL.startswith("postgres") else bool(user["banned"]),
        "referral_link": f"https://t.me/your_bot?start={uid}"  # можно заменить
    }

    # Проверка ежедневного бонуса (доступен ли сегодня)
    if DATABASE_URL.startswith("postgres"):
        last_bonus = user.get("daily_bonus_claimed")
        if last_bonus:
            today = datetime.now().date()
            last_bonus_date = last_bonus.date()
            result["daily_bonus_available"] = (today > last_bonus_date)
        else:
            result["daily_bonus_available"] = True
    else:
        last_bonus = user.get("daily_bonus_claimed")
        if last_bonus:
            last_bonus_date = datetime.fromtimestamp(last_bonus).date()
            today = datetime.now().date()
            result["daily_bonus_available"] = (today > last_bonus_date)
        else:
            result["daily_bonus_available"] = True

    return jsonify(result)

@app.route("/api/rates", methods=["GET"])
def api_rates():
    """Возвращает текущие курсы валют."""
    rates = get_rates()
    return jsonify(rates)

@app.route("/api/convert", methods=["POST"])
def api_convert():
    """Конвертирует сумму из одной валюты в другую."""
    data = request.json
    from_curr = data.get("from", "USD").upper()
    to_curr = data.get("to", "USD").upper()
    amount = data.get("amount", 1)

    rates = get_rates()
    if from_curr not in rates or to_curr not in rates:
        return jsonify({"error": "Unsupported currency"}), 400

    # Конвертация через USD как базовую
    if from_curr == "USD":
        result = amount * rates[to_curr]
    elif to_curr == "USD":
        result = amount / rates[from_curr]
    else:
        result = amount / rates[from_curr] * rates[to_curr]

    return jsonify({"from": from_curr, "to": to_curr, "amount": amount, "result": round(result, 2)})

@app.route("/api/exchange", methods=["POST"])
def api_exchange():
    """Обмен валюты (списание USD, запись в историю)."""
    data = request.json
    uid = data.get("id")
    from_curr = data.get("from", "USD").upper()
    to_curr = data.get("to", "USD").upper()
    amount = data.get("amount", 0)

    if not uid or amount <= 0:
        return jsonify({"error": "Invalid data"}), 400

    # Проверяем баланс пользователя
    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL.startswith("postgres"):
        cur.execute("SELECT balance_usd, banned FROM users WHERE id = %s", (uid,))
    else:
        cur.execute("SELECT balance_usd, banned FROM users WHERE id = ?", (uid,))
    user = cur.fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "User not found"}), 404
    if user["banned"]:
        conn.close()
        return jsonify({"error": "User is banned"}), 403

    balance = user["balance_usd"]
    # Конвертируем amount в USD для списания
    rates = get_rates()
    if from_curr != "USD":
        if from_curr not in rates:
            conn.close()
            return jsonify({"error": "Unsupported currency"}), 400
        amount_usd = amount / rates[from_curr]
    else:
        amount_usd = amount

    if balance < amount_usd:
        conn.close()
        return jsonify({"error": "Insufficient balance"}), 400

    # Обновляем баланс
    if DATABASE_URL.startswith("postgres"):
        cur.execute("UPDATE users SET balance_usd = balance_usd - %s WHERE id = %s", (amount_usd, uid))
    else:
        cur.execute("UPDATE users SET balance_usd = balance_usd - ? WHERE id = ?", (amount_usd, uid))

    # Запись в историю
    details = f"Exchanged {amount} {from_curr} to {to_curr}"
    if DATABASE_URL.startswith("postgres"):
        cur.execute("""
            INSERT INTO history (user_id, type, amount_usd, details)
            VALUES (%s, 'exchange', %s, %s)
        """, (uid, amount_usd, details))
    else:
        cur.execute("""
            INSERT INTO history (user_id, type, amount_usd, details, created_at)
            VALUES (?, 'exchange', ?, ?, ?)
        """, (uid, amount_usd, details, int(time.time())))

    conn.commit()
    conn.close()

    return jsonify({"ok": True, "new_balance": balance - amount_usd})

@app.route("/api/promo", methods=["POST"])
def api_promo():
    """Активация промокода."""
    data = request.json
    uid = data.get("id")
    code = data.get("code", "").strip().upper()

    if not uid or not code:
        return jsonify({"error": "Missing data"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    # Проверяем промокод
    if DATABASE_URL.startswith("postgres"):
        cur.execute("SELECT amount_usd, uses_left FROM promocodes WHERE code = %s", (code,))
    else:
        cur.execute("SELECT amount_usd, uses_left FROM promocodes WHERE code = ?", (code,))
    promo = cur.fetchone()
    if not promo:
        conn.close()
        return jsonify({"error": "Invalid promo code"}), 404
    if promo["uses_left"] <= 0:
        conn.close()
        return jsonify({"error": "Promo code expired"}), 400

    # Проверяем, не активировал ли пользователь уже этот код (по истории)
    if DATABASE_URL.startswith("postgres"):
        cur.execute("SELECT id FROM history WHERE user_id = %s AND details LIKE %s", (uid, f"%{code}%"))
    else:
        cur.execute("SELECT id FROM history WHERE user_id = ? AND details LIKE ?", (uid, f"%{code}%"))
    if cur.fetchone():
        conn.close()
        return jsonify({"error": "Promo already used"}), 400

    # Начисляем бонус
    amount = promo["amount_usd"]
    if DATABASE_URL.startswith("postgres"):
        cur.execute("UPDATE users SET balance_usd = balance_usd + %s WHERE id = %s", (amount, uid))
        cur.execute("UPDATE promocodes SET uses_left = uses_left - 1 WHERE code = %s", (code,))
        cur.execute("""
            INSERT INTO history (user_id, type, amount_usd, details)
            VALUES (%s, 'promo', %s, %s)
        """, (uid, amount, f"Promo code {code}"))
    else:
        cur.execute("UPDATE users SET balance_usd = balance_usd + ? WHERE id = ?", (amount, uid))
        cur.execute("UPDATE promocodes SET uses_left = uses_left - 1 WHERE code = ?", (code,))
        cur.execute("""
            INSERT INTO history (user_id, type, amount_usd, details, created_at)
            VALUES (?, 'promo', ?, ?, ?)
        """, (uid, amount, f"Promo code {code}", int(time.time())))

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "amount": amount})

@app.route("/api/history", methods=["GET"])
def api_history():
    """Возвращает историю операций пользователя."""
    uid = request.args.get("id", type=int)
    limit = request.args.get("limit", 20, type=int)

    if not uid:
        return jsonify({"error": "Missing id"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL.startswith("postgres"):
        cur.execute("""
            SELECT type, amount_usd, details, created_at
            FROM history
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (uid, limit))
    else:
        cur.execute("""
            SELECT type, amount_usd, details, created_at
            FROM history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (uid, limit))
    rows = cur.fetchall()
    conn.close()

    history = []
    for row in rows:
        if DATABASE_URL.startswith("postgres"):
            created = row["created_at"].isoformat()
        else:
            created = datetime.fromtimestamp(row["created_at"]).isoformat()
        history.append({
            "type": row["type"],
            "amount": row["amount_usd"],
            "details": row["details"],
            "date": created
        })
    return jsonify(history)

@app.route("/api/daily_bonus", methods=["POST"])
def api_daily_bonus():
    """Получение ежедневного бонуса."""
    data = request.json
    uid = data.get("id")

    if not uid:
        return jsonify({"error": "Missing id"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    # Получаем пользователя и проверяем, доступен ли бонус
    if DATABASE_URL.startswith("postgres"):
        cur.execute("SELECT daily_bonus_claimed FROM users WHERE id = %s", (uid,))
    else:
        cur.execute("SELECT daily_bonus_claimed FROM users WHERE id = ?", (uid,))
    user = cur.fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "User not found"}), 404

    now = datetime.now()
    if DATABASE_URL.startswith("postgres"):
        last_claimed = user["daily_bonus_claimed"]
        if last_claimed and last_claimed.date() == now.date():
            conn.close()
            return jsonify({"error": "Already claimed today"}), 400
    else:
        last_claimed = user["daily_bonus_claimed"]
        if last_claimed and datetime.fromtimestamp(last_claimed).date() == now.date():
            conn.close()
            return jsonify({"error": "Already claimed today"}), 400

    # Начисляем бонус (например, 1 USD)
    bonus = 1.0
    if DATABASE_URL.startswith("postgres"):
        cur.execute("UPDATE users SET balance_usd = balance_usd + %s, daily_bonus_claimed = %s WHERE id = %s",
                    (bonus, now, uid))
        cur.execute("""
            INSERT INTO history (user_id, type, amount_usd, details)
            VALUES (%s, 'daily_bonus', %s, %s)
        """, (uid, bonus, "Daily bonus"))
    else:
        cur.execute("UPDATE users SET balance_usd = balance_usd + ?, daily_bonus_claimed = ? WHERE id = ?",
                    (bonus, int(now.timestamp()), uid))
        cur.execute("""
            INSERT INTO history (user_id, type, amount_usd, details, created_at)
            VALUES (?, 'daily_bonus', ?, ?, ?)
        """, (uid, bonus, "Daily bonus", int(now.timestamp())))

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "amount": bonus})

@app.route("/api/referrals", methods=["GET"])
def api_referrals():
    """Возвращает статистику рефералов пользователя."""
    uid = request.args.get("id", type=int)

    if not uid:
        return jsonify({"error": "Missing id"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL.startswith("postgres"):
        cur.execute("""
            SELECT COUNT(*) as count FROM referrals WHERE referrer_id = %s
        """, (uid,))
        count = cur.fetchone()["count"]
        cur.execute("""
            SELECT SUM(amount_usd) as total FROM history
            WHERE user_id = %s AND type = 'referral_bonus'
        """, (uid,))
        total = cur.fetchone()["total"] or 0
    else:
        cur.execute("SELECT COUNT(*) as count FROM referrals WHERE referrer_id = ?", (uid,))
        count = cur.fetchone()["count"]
        cur.execute("SELECT SUM(amount_usd) as total FROM history WHERE user_id = ? AND type = 'referral_bonus'", (uid,))
        total = cur.fetchone()["total"] or 0

    conn.close()
    return jsonify({"count": count, "total_bonus": total})

# ========================
# Админские эндпоинты
# ========================

@app.route("/api/admin/users", methods=["GET"])
@admin_required
def admin_users():
    """Список пользователей с пагинацией."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    offset = (page - 1) * per_page

    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL.startswith("postgres"):
        cur.execute("SELECT id, username, balance_usd, banned, created_at FROM users ORDER BY id LIMIT %s OFFSET %s",
                    (per_page, offset))
        cur.execute("SELECT COUNT(*) as total FROM users")
        total = cur.fetchone()["total"]
    else:
        cur.execute("SELECT id, username, balance_usd, banned, created_at FROM users ORDER BY id LIMIT ? OFFSET ?",
                    (per_page, offset))
        cur.execute("SELECT COUNT(*) as total FROM users")
        total = cur.fetchone()["total"]

    users = cur.fetchall()
    conn.close()

    result = []
    for u in users:
        if DATABASE_URL.startswith("postgres"):
            created = u["created_at"].isoformat() if u["created_at"] else None
        else:
            created = datetime.fromtimestamp(u["created_at"]).isoformat() if u["created_at"] else None
        result.append({
            "id": u["id"],
            "username": u["username"],
            "balance": u["balance_usd"],
            "banned": bool(u["banned"]),
            "created_at": created
        })

    return jsonify({"users": result, "total": total, "page": page, "per_page": per_page})

@app.route("/api/admin/user/<int:uid>", methods=["POST"])
@admin_required
def admin_update_user(uid):
    """Изменение баланса пользователя или блокировка."""
    data = request.json
    action = data.get("action")
    value = data.get("value")

    if action not in ["set_balance", "add_balance", "ban", "unban"]:
        return jsonify({"error": "Invalid action"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    if action == "set_balance":
        if not isinstance(value, (int, float)):
            return jsonify({"error": "Value must be number"}), 400
        if DATABASE_URL.startswith("postgres"):
            cur.execute("UPDATE users SET balance_usd = %s WHERE id = %s", (value, uid))
        else:
            cur.execute("UPDATE users SET balance_usd = ? WHERE id = ?", (value, uid))
    elif action == "add_balance":
        if not isinstance(value, (int, float)):
            return jsonify({"error": "Value must be number"}), 400
        if DATABASE_URL.startswith("postgres"):
            cur.execute("UPDATE users SET balance_usd = balance_usd + %s WHERE id = %s", (value, uid))
        else:
            cur.execute("UPDATE users SET balance_usd = balance_usd + ? WHERE id = ?", (value, uid))
    elif action == "ban":
        if DATABASE_URL.startswith("postgres"):
            cur.execute("UPDATE users SET banned = TRUE WHERE id = %s", (uid,))
        else:
            cur.execute("UPDATE users SET banned = 1 WHERE id = ?", (uid,))
    elif action == "unban":
        if DATABASE_URL.startswith("postgres"):
            cur.execute("UPDATE users SET banned = FALSE WHERE id = %s", (uid,))
        else:
            cur.execute("UPDATE users SET banned = 0 WHERE id = ?", (uid,))

    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/admin/promocodes", methods=["GET"])
@admin_required
def admin_promocodes():
    """Список всех промокодов."""
    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL.startswith("postgres"):
        cur.execute("SELECT code, amount_usd, uses_left, created_at FROM promocodes ORDER BY created_at DESC")
    else:
        cur.execute("SELECT code, amount_usd, uses_left, created_at FROM promocodes ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()

    promos = []
    for row in rows:
        if DATABASE_URL.startswith("postgres"):
            created = row["created_at"].isoformat() if row["created_at"] else None
        else:
            created = datetime.fromtimestamp(row["created_at"]).isoformat() if row["created_at"] else None
        promos.append({
            "code": row["code"],
            "amount": row["amount_usd"],
            "uses_left": row["uses_left"],
            "created_at": created
        })
    return jsonify(promos)

@app.route("/api/admin/promocode", methods=["POST"])
@admin_required
def admin_create_promo():
    """Создание нового промокода."""
    data = request.json
    code = data.get("code", "").strip().upper()
    amount = data.get("amount", type=float)
    uses = data.get("uses", type=int)
    admin_id = data.get("admin")

    if not code or amount is None or uses is None:
        return jsonify({"error": "Missing fields"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL.startswith("postgres"):
        cur.execute("""
            INSERT INTO promocodes (code, amount_usd, uses_left, created_by)
            VALUES (%s, %s, %s, %s)
        """, (code, amount, uses, admin_id))
    else:
        cur.execute("""
            INSERT INTO promocodes (code, amount_usd, uses_left, created_by, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (code, amount, uses, admin_id, int(time.time())))

    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/admin/promocode/<code>", methods=["DELETE"])
@admin_required
def admin_delete_promo(code):
    """Удаление промокода."""
    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL.startswith("postgres"):
        cur.execute("DELETE FROM promocodes WHERE code = %s", (code,))
    else:
        cur.execute("DELETE FROM promocodes WHERE code = ?", (code,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def admin_stats():
    """Общая статистика."""
    conn = get_db_connection()
    cur = conn.cursor()
    if DATABASE_URL.startswith("postgres"):
        cur.execute("SELECT COUNT(*) as total_users FROM users")
        total_users = cur.fetchone()["total_users"]
        cur.execute("SELECT COUNT(*) as total_banned FROM users WHERE banned = TRUE")
        total_banned = cur.fetchone()["total_banned"]
        cur.execute("SELECT SUM(balance_usd) as total_balance FROM users")
        total_balance = cur.fetchone()["total_balance"] or 0
        cur.execute("SELECT COUNT(*) as total_promos FROM promocodes")
        total_promos = cur.fetchone()["total_promos"]
        cur.execute("SELECT COUNT(*) as total_history FROM history")
        total_history = cur.fetchone()["total_history"]
    else:
        cur.execute("SELECT COUNT(*) as total_users FROM users")
        total_users = cur.fetchone()["total_users"]
        cur.execute("SELECT COUNT(*) as total_banned FROM users WHERE banned = 1")
        total_banned = cur.fetchone()["total_banned"]
        cur.execute("SELECT SUM(balance_usd) as total_balance FROM users")
        total_balance = cur.fetchone()["total_balance"] or 0
        cur.execute("SELECT COUNT(*) as total_promos FROM promocodes")
        total_promos = cur.fetchone()["total_promos"]
        cur.execute("SELECT COUNT(*) as total_history FROM history")
        total_history = cur.fetchone()["total_history"]

    conn.close()
    return jsonify({
        "total_users": total_users,
        "total_banned": total_banned,
        "total_balance": total_balance,
        "total_promos": total_promos,
        "total_history": total_history
    })

# Для локального запуска
if __name__ == "__main__":
    app.run(debug=True, port=5000)
