import os
import json
import time
import sqlite3
from datetime import datetime
from functools import wraps
from http.cookies import SimpleCookie

import requests
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

# Конфигурация
BOT_TOKEN = "7762911922:AAHdyGVZRwCkI_WtcGW1MPbIdhrcDBpKNvE"
ADMIN_ID = 896706118
DB_PATH = "/tmp/database.db"  # Vercel имеет доступ только к /tmp для записи

app = Flask(__name__)
CORS(app)  # Разрешаем CORS для всех маршрутов

# ========================
# ИНИЦИАЛИЗАЦИЯ БД
# ========================

def init_db():
    """Создаёт таблицы, если их нет"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Пользователи
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY,
                  username TEXT,
                  balance REAL DEFAULT 0,
                  referrer INTEGER,
                  banned INTEGER DEFAULT 0,
                  last_bonus INTEGER,
                  created_at INTEGER)''')
    
    # Промокоды
    c.execute('''CREATE TABLE IF NOT EXISTS promocodes
                 (code TEXT PRIMARY KEY,
                  amount REAL,
                  uses INTEGER,
                  created_by INTEGER,
                  created_at INTEGER)''')
    
    # История операций
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  type TEXT,
                  amount REAL,
                  currency TEXT,
                  details TEXT,
                  created_at INTEGER)''')
    
    # Рефералы
    c.execute('''CREATE TABLE IF NOT EXISTS referrals
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  referrer_id INTEGER,
                  referred_id INTEGER UNIQUE,
                  bonus_paid INTEGER DEFAULT 0,
                  created_at INTEGER)''')
    
    # Курсы валют (кэш)
    c.execute('''CREATE TABLE IF NOT EXISTS rates
                 (base TEXT PRIMARY KEY,
                  rates TEXT,
                  updated_at INTEGER)''')
    
    conn.commit()
    conn.close()

# Создаём таблицы при первом запуске
init_db()

# ========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ========================

def get_db():
    """Возвращает соединение с БД"""
    return sqlite3.connect(DB_PATH)

def execute_query(query, params=(), fetchone=False, fetchall=False):
    """Упрощённое выполнение запросов с автоматическим закрытием соединения"""
    conn = get_db()
    c = conn.cursor()
    c.execute(query, params)
    
    result = None
    if fetchone:
        result = c.fetchone()
    elif fetchall:
        result = c.fetchall()
    else:
        conn.commit()
        result = c.lastrowid
    
    conn.close()
    return result

def get_or_create_user(uid, username='', referrer=None):
    """Получает пользователя или создаёт нового"""
    user = execute_query(
        "SELECT * FROM users WHERE id = ?", 
        (uid,), 
        fetchone=True
    )
    
    if not user:
        now = int(time.time())
        execute_query(
            "INSERT INTO users (id, username, referrer, created_at) VALUES (?, ?, ?, ?)",
            (uid, username, referrer, now)
        )
        
        # Если есть реферер, начисляем бонус
        if referrer and referrer != uid:
            # Проверяем, существует ли реферер
            ref_user = execute_query(
                "SELECT id FROM users WHERE id = ? AND banned = 0",
                (referrer,),
                fetchone=True
            )
            if ref_user:
                # Начисляем бонус
                bonus = 5.0
                execute_query(
                    "UPDATE users SET balance = balance + ? WHERE id = ?",
                    (bonus, referrer)
                )
                execute_query(
                    "INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                    (referrer, uid, now)
                )
                execute_query(
                    "INSERT INTO history (user_id, type, amount, details, created_at) VALUES (?, ?, ?, ?, ?)",
                    (referrer, 'referral_bonus', bonus, f'За приглашение пользователя {uid}', now)
                )
        
        # Получаем созданного пользователя
        user = execute_query(
            "SELECT * FROM users WHERE id = ?", 
            (uid,), 
            fetchone=True
        )
    
    return user

def get_rates(force_refresh=False):
    """Получает курсы валют с кэшированием"""
    now = int(time.time())
    
    # Пробуем получить из кэша
    cached = execute_query(
        "SELECT rates, updated_at FROM rates WHERE base = 'USD'",
        fetchone=True
    )
    
    if cached and not force_refresh and (now - cached[1] < 3600):  # 1 час кэш
        return json.loads(cached[0])
    
    # Получаем свежие курсы
    try:
        response = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=5
        )
        data = response.json()
        rates = data['rates']
        
        # Сохраняем в кэш
        rates_json = json.dumps(rates)
        if cached:
            execute_query(
                "UPDATE rates SET rates = ?, updated_at = ? WHERE base = 'USD'",
                (rates_json, now)
            )
        else:
            execute_query(
                "INSERT INTO rates (base, rates, updated_at) VALUES (?, ?, ?)",
                ('USD', rates_json, now)
            )
        
        return rates
    except Exception as e:
        # Если не удалось получить, возвращаем кэш или пустой словарь
        return json.loads(cached[0]) if cached else {}

# ========================
# API ЭНДПОИНТЫ
# ========================

@app.route('/api/user', methods=['GET', 'OPTIONS'])
def api_user():
    """Информация о пользователе"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        uid = request.args.get('id', type=int)
        username = request.args.get('username', '')
        referrer = request.args.get('ref', type=int)
        
        if not uid:
            return jsonify({'error': 'Missing user ID'}), 400
        
        user = get_or_create_user(uid, username, referrer)
        
        # Проверка ежедневного бонуса
        now = int(time.time())
        today_start = now - (now % 86400)  # Начало текущего дня
        last_bonus = user[5] if len(user) > 5 else 0  # last_bonus индекс
        
        bonus_available = last_bonus < today_start
        
        return jsonify({
            'id': user[0],
            'username': user[1],
            'balance': user[2],
            'banned': bool(user[4]),
            'bonus_available': bonus_available,
            'referral_link': f'https://t.me/your_bot?start={uid}'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/rates', methods=['GET', 'OPTIONS'])
def api_rates():
    """Курсы валют"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        rates = get_rates()
        return jsonify(rates)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/convert', methods=['POST', 'OPTIONS'])
def api_convert():
    """Конвертация валют"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.json
        from_curr = data.get('from', 'USD')
        to_curr = data.get('to', 'USD')
        amount = float(data.get('amount', 1))
        
        rates = get_rates()
        
        if from_curr not in rates or to_curr not in rates:
            return jsonify({'error': 'Unsupported currency'}), 400
        
        # Конвертация через USD
        if from_curr == 'USD':
            result = amount * rates[to_curr]
        elif to_curr == 'USD':
            result = amount / rates[from_curr]
        else:
            result = amount / rates[from_curr] * rates[to_curr]
        
        return jsonify({
            'from': from_curr,
            'to': to_curr,
            'amount': amount,
            'result': round(result, 2)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/exchange', methods=['POST', 'OPTIONS'])
def api_exchange():
    """Выполнение обмена"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.json
        uid = data.get('id')
        from_curr = data.get('from', 'USD')
        to_curr = data.get('to', 'USD')
        amount = float(data.get('amount', 0))
        
        if not uid or amount <= 0:
            return jsonify({'error': 'Invalid data'}), 400
        
        # Получаем пользователя
        user = execute_query(
            "SELECT * FROM users WHERE id = ? AND banned = 0",
            (uid,),
            fetchone=True
        )
        
        if not user:
            return jsonify({'error': 'User not found or banned'}), 404
        
        balance = user[2]
        rates = get_rates()
        
        # Конвертируем сумму в USD для списания
        if from_curr != 'USD':
            if from_curr not in rates:
                return jsonify({'error': 'Unsupported currency'}), 400
            amount_usd = amount / rates[from_curr]
        else:
            amount_usd = amount
        
        if balance < amount_usd:
            return jsonify({'error': 'Insufficient balance'}), 400
        
        # Обновляем баланс
        new_balance = balance - amount_usd
        execute_query(
            "UPDATE users SET balance = ? WHERE id = ?",
            (new_balance, uid)
        )
        
        # Запись в историю
        now = int(time.time())
        execute_query(
            """INSERT INTO history 
               (user_id, type, amount, currency, details, created_at) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, 'exchange', amount_usd, to_curr, 
             f'Обмен {amount} {from_curr} → {to_curr}', now)
        )
        
        return jsonify({
            'ok': True,
            'new_balance': new_balance
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/promo', methods=['POST', 'OPTIONS'])
def api_promo():
    """Активация промокода"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.json
        uid = data.get('id')
        code = data.get('code', '').strip().upper()
        
        if not uid or not code:
            return jsonify({'error': 'Missing data'}), 400
        
        # Проверяем промокод
        promo = execute_query(
            "SELECT * FROM promocodes WHERE code = ? AND uses > 0",
            (code,),
            fetchone=True
        )
        
        if not promo:
            return jsonify({'error': 'Invalid or expired promo code'}), 404
        
        # Проверяем, не использовал ли пользователь этот код
        used = execute_query(
            "SELECT id FROM history WHERE user_id = ? AND details LIKE ?",
            (uid, f'%{code}%'),
            fetchone=True
        )
        
        if used:
            return jsonify({'error': 'Promo code already used'}), 400
        
        amount = promo[1]
        now = int(time.time())
        
        # Начисляем бонус
        execute_query(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (amount, uid)
        )
        
        # Уменьшаем количество использований
        execute_query(
            "UPDATE promocodes SET uses = uses - 1 WHERE code = ?",
            (code,)
        )
        
        # Запись в историю
        execute_query(
            """INSERT INTO history 
               (user_id, type, amount, details, created_at) 
               VALUES (?, ?, ?, ?, ?)""",
            (uid, 'promo', amount, f'Промокод {code}', now)
        )
        
        return jsonify({'ok': True, 'amount': amount})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/history', methods=['GET', 'OPTIONS'])
def api_history():
    """История операций"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        uid = request.args.get('id', type=int)
        limit = request.args.get('limit', 20, type=int)
        
        if not uid:
            return jsonify({'error': 'Missing user ID'}), 400
        
        history = execute_query(
            """SELECT type, amount, currency, details, created_at 
               FROM history 
               WHERE user_id = ? 
               ORDER BY created_at DESC 
               LIMIT ?""",
            (uid, limit),
            fetchall=True
        )
        
        result = []
        for h in history:
            result.append({
                'type': h[0],
                'amount': h[1],
                'currency': h[2] or 'USD',
                'details': h[3],
                'date': datetime.fromtimestamp(h[4]).isoformat()
            })
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/referrals', methods=['GET', 'OPTIONS'])
def api_referrals():
    """Статистика рефералов"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        uid = request.args.get('id', type=int)
        
        if not uid:
            return jsonify({'error': 'Missing user ID'}), 400
        
        # Количество рефералов
        count = execute_query(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?",
            (uid,),
            fetchone=True
        )[0]
        
        # Сумма бонусов
        total = execute_query(
            "SELECT SUM(amount) FROM history WHERE user_id = ? AND type = 'referral_bonus'",
            (uid,),
            fetchone=True
        )[0] or 0
        
        return jsonify({
            'count': count,
            'total_bonus': total
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/daily_bonus', methods=['POST', 'OPTIONS'])
def api_daily_bonus():
    """Ежедневный бонус"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.json
        uid = data.get('id')
        
        if not uid:
            return jsonify({'error': 'Missing user ID'}), 400
        
        user = execute_query(
            "SELECT * FROM users WHERE id = ?",
            (uid,),
            fetchone=True
        )
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        now = int(time.time())
        today_start = now - (now % 86400)
        last_bonus = user[5] if len(user) > 5 else 0
        
        if last_bonus >= today_start:
            return jsonify({'error': 'Already claimed today'}), 400
        
        bonus = 1.0
        
        # Начисляем бонус
        execute_query(
            "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE id = ?",
            (bonus, now, uid)
        )
        
        # Запись в историю
        execute_query(
            """INSERT INTO history 
               (user_id, type, amount, details, created_at) 
               VALUES (?, ?, ?, ?, ?)""",
            (uid, 'daily_bonus', bonus, 'Ежедневный бонус', now)
        )
        
        return jsonify({'ok': True, 'amount': bonus})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========================
# АДМИН ЭНДПОИНТЫ
# ========================

def admin_required(f):
    """Декоратор для проверки прав администратора"""
    @wraps(f)
    def decorated(*args, **kwargs):
        admin_id = request.args.get('admin')
        if not admin_id or int(admin_id) != ADMIN_ID:
            return jsonify({'error': 'Access denied'}), 403
        return f(*args, **kwargs)
    return decorated

@app.route('/api/admin/stats', methods=['GET', 'OPTIONS'])
@admin_required
def admin_stats():
    """Статистика для админа"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        total_users = execute_query(
            "SELECT COUNT(*) FROM users",
            fetchone=True
        )[0]
        
        total_banned = execute_query(
            "SELECT COUNT(*) FROM users WHERE banned = 1",
            fetchone=True
        )[0]
        
        total_balance = execute_query(
            "SELECT SUM(balance) FROM users",
            fetchone=True
        )[0] or 0
        
        total_promos = execute_query(
            "SELECT COUNT(*) FROM promocodes",
            fetchone=True
        )[0]
        
        return jsonify({
            'total_users': total_users,
            'total_banned': total_banned,
            'total_balance': total_balance,
            'total_promos': total_promos
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/users', methods=['GET', 'OPTIONS'])
@admin_required
def admin_users():
    """Список пользователей"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        offset = (page - 1) * per_page
        
        users = execute_query(
            """SELECT id, username, balance, banned, created_at 
               FROM users 
               ORDER BY id 
               LIMIT ? OFFSET ?""",
            (per_page, offset),
            fetchall=True
        )
        
        total = execute_query(
            "SELECT COUNT(*) FROM users",
            fetchone=True
        )[0]
        
        result = []
        for u in users:
            result.append({
                'id': u[0],
                'username': u[1],
                'balance': u[2],
                'banned': bool(u[3]),
                'created_at': datetime.fromtimestamp(u[4]).isoformat() if u[4] else None
            })
        
        return jsonify({
            'users': result,
            'total': total,
            'page': page,
            'per_page': per_page
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/user/<int:user_id>', methods=['POST', 'OPTIONS'])
@admin_required
def admin_update_user(user_id):
    """Обновление пользователя"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.json
        action = data.get('action')
        value = data.get('value')
        
        if action == 'set_balance':
            execute_query(
                "UPDATE users SET balance = ? WHERE id = ?",
                (float(value), user_id)
            )
        elif action == 'add_balance':
            execute_query(
                "UPDATE users SET balance = balance + ? WHERE id = ?",
                (float(value), user_id)
            )
        elif action == 'ban':
            execute_query(
                "UPDATE users SET banned = 1 WHERE id = ?",
                (user_id,)
            )
        elif action == 'unban':
            execute_query(
                "UPDATE users SET banned = 0 WHERE id = ?",
                (user_id,)
            )
        else:
            return jsonify({'error': 'Invalid action'}), 400
        
        return jsonify({'ok': True})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/promocode', methods=['POST', 'OPTIONS'])
@admin_required
def admin_create_promo():
    """Создание промокода"""
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.json
        code = data.get('code', '').strip().upper()
        amount = float(data.get('amount', 0))
        uses = int(data.get('uses', 1))
        admin_id = data.get('admin')
        
        if not code or amount <= 0 or uses <= 0:
            return jsonify({'error': 'Invalid data'}), 400
        
        now = int(time.time())
        
        execute_query(
            """INSERT INTO promocodes 
               (code, amount, uses, created_by, created_at) 
               VALUES (?, ?, ?, ?, ?)""",
            (code, amount, uses, admin_id, now)
        )
        
        return jsonify({'ok': True})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Для Vercel serverless
def handler(request):
    return app(request)

# Для локального запуска
if __name__ == '__main__':
    app.run(debug=True, port=5000)
