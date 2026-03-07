import os
import json
import time
import sqlite3
from datetime import datetime
from functools import wraps
import threading

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

# Конфигурация
BOT_TOKEN = "7762911922:AAHdyGVZRwCkI_WtcGW1MPbIdhrcDBpKNvE"
ADMIN_ID = 896706118
DB_PATH = "/tmp/database.db"

app = Flask(__name__)
CORS(app)

# ========================
# КЭШ КУРСОВ ВАЛЮТ (в памяти)
# ========================
rates_cache = {
    'rates': {},
    'last_update': 0
}
rates_lock = threading.Lock()

def get_rates():
    """Быстрое получение курсов с кэшированием"""
    global rates_cache
    now = time.time()
    
    # Если кэш свежий (менее 1 часа), возвращаем его
    with rates_lock:
        if rates_cache['rates'] and (now - rates_cache['last_update'] < 3600):
            return rates_cache['rates']
    
    # Иначе пробуем обновить
    try:
        response = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=3  # Уменьшаем таймаут до 3 секунд
        )
        data = response.json()
        new_rates = data['rates']
        
        with rates_lock:
            rates_cache['rates'] = new_rates
            rates_cache['last_update'] = now
        
        return new_rates
    except:
        # Если не удалось обновить, возвращаем старый кэш или пустой словарь
        return rates_cache['rates'] or {}

# ========================
# БАЗА ДАННЫХ (SQLite с оптимизациями)
# ========================

def get_db():
    """Быстрое соединение с БД"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Оптимизация для скорости
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    """Инициализация таблиц"""
    conn = get_db()
    c = conn.cursor()
    
    # Таблица пользователей с индексами
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY,
                  username TEXT,
                  balance REAL DEFAULT 0,
                  referrer INTEGER,
                  banned INTEGER DEFAULT 0,
                  last_bonus INTEGER,
                  created_at INTEGER)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
    
    # Таблица промокодов
    c.execute('''CREATE TABLE IF NOT EXISTS promocodes
                 (code TEXT PRIMARY KEY,
                  amount REAL,
                  uses INTEGER,
                  created_by INTEGER,
                  created_at INTEGER)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_promocodes_uses ON promocodes(uses)')
    
    # Таблица истории
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  type TEXT,
                  amount REAL,
                  currency TEXT,
                  details TEXT,
                  created_at INTEGER)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at)')
    
    # Таблица рефералов
    c.execute('''CREATE TABLE IF NOT EXISTS referrals
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  referrer_id INTEGER,
                  referred_id INTEGER UNIQUE,
                  bonus_paid INTEGER DEFAULT 0,
                  created_at INTEGER)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)')
    
    conn.commit()
    conn.close()

# Создаём таблицы при запуске
init_db()

# ========================
# БЫСТРЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С БД
# ========================

def get_user_fast(uid):
    """Максимально быстрый запрос пользователя"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id = ?", (uid,))
    user = c.fetchone()
    conn.close()
    return user

def create_user_fast(uid, username='', referrer=None):
    """Быстрое создание пользователя"""
    now = int(time.time())
    conn = get_db()
    c = conn.cursor()
    
    c.execute(
        "INSERT INTO users (id, username, referrer, created_at) VALUES (?, ?, ?, ?)",
        (uid, username, referrer, now)
    )
    
    # Если есть реферер, начисляем бонус асинхронно (в отдельном потоке)
    if referrer and referrer != uid:
        def give_referral_bonus():
            time.sleep(0.1)  # Небольшая задержка, чтобы не блокировать ответ
            try:
                conn2 = get_db()
                c2 = conn2.cursor()
                # Проверяем существование реферера
                c2.execute("SELECT id FROM users WHERE id = ? AND banned = 0", (referrer,))
                if c2.fetchone():
                    bonus = 5.0
                    c2.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (bonus, referrer))
                    c2.execute(
                        "INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                        (referrer, uid, now)
                    )
                    c2.execute(
                        "INSERT INTO history (user_id, type, amount, details, created_at) VALUES (?, ?, ?, ?, ?)",
                        (referrer, 'referral_bonus', bonus, f'За реферала {uid}', now)
                    )
                    conn2.commit()
                conn2.close()
            except:
                pass
        
        threading.Thread(target=give_referral_bonus).start()
    
    conn.commit()
    conn.close()

def execute_read(query, params=()):
    """Быстрое чтение из БД"""
    conn = get_db()
    c = conn.cursor()
    c.execute(query, params)
    result = c.fetchall()
    conn.close()
    return result

def execute_write(query, params=()):
    """Быстрая запись в БД"""
    conn = get_db()
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    last_id = c.lastrowid
    conn.close()
    return last_id

# ========================
# API ЭНДПОИНТЫ (максимально быстрые)
# ========================

@app.route('/api/user', methods=['GET'])
def api_user():
    """Мгновенный ответ с информацией о пользователе"""
    try:
        uid = request.args.get('id', type=int)
        username = request.args.get('username', '')
        referrer = request.args.get('ref', type=int)
        
        if not uid:
            return jsonify({'error': 'No user ID'}), 400
        
        # Пытаемся получить пользователя
        user = get_user_fast(uid)
        
        # Если пользователя нет, создаём
        if not user:
            create_user_fast(uid, username, referrer)
            user = get_user_fast(uid)
        
        if not user:
            return jsonify({'error': 'Failed to create user'}), 500
        
        # Проверка бонуса
        now = int(time.time())
        today_start = now - (now % 86400)
        last_bonus = user['last_bonus'] or 0
        bonus_available = last_bonus < today_start
        
        return jsonify({
            'id': user['id'],
            'username': user['username'],
            'balance': user['balance'],
            'banned': bool(user['banned']),
            'bonus_available': bonus_available,
            'referral_link': f'https://t.me/your_bot?start={uid}'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/rates', methods=['GET'])
def api_rates():
    """Мгновенный ответ с курсами валют"""
    try:
        rates = get_rates()
        # Возвращаем только основные валюты для скорости
        main_currencies = ['USD', 'EUR', 'GBP', 'JPY', 'RUB', 'CNY', 'CHF', 'CAD']
        filtered_rates = {c: rates.get(c, 1) for c in main_currencies}
        return jsonify(filtered_rates)
    except:
        # Если совсем плохо, возвращаем заглушку
        return jsonify({
            'USD': 1, 'EUR': 0.92, 'GBP': 0.79, 
            'JPY': 150.5, 'RUB': 90.5, 'CNY': 7.2,
            'CHF': 0.88, 'CAD': 1.35
        })

@app.route('/api/convert', methods=['POST'])
def api_convert():
    """Быстрая конвертация"""
    try:
        data = request.json
        from_curr = data.get('from', 'USD')
        to_curr = data.get('to', 'USD')
        amount = float(data.get('amount', 1))
        
        rates = get_rates()
        
        if from_curr not in rates or to_curr not in rates:
            return jsonify({'error': 'Unsupported currency'}), 400
        
        # Конвертация
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

@app.route('/api/exchange', methods=['POST'])
def api_exchange():
    """Быстрый обмен"""
    try:
        data = request.json
        uid = data.get('id')
        from_curr = data.get('from', 'USD')
        to_curr = data.get('to', 'USD')
        amount = float(data.get('amount', 0))
        
        if not uid or amount <= 0:
            return jsonify({'error': 'Invalid data'}), 400
        
        # Получаем пользователя
        user = get_user_fast(uid)
        if not user or user['banned']:
            return jsonify({'error': 'User not found or banned'}), 404
        
        rates = get_rates()
        
        # Конвертируем в USD для списания
        if from_curr == 'USD':
            amount_usd = amount
        else:
            amount_usd = amount / rates.get(from_curr, 1)
        
        if user['balance'] < amount_usd:
            return jsonify({'error': 'Insufficient balance'}), 400
        
        # Обновляем баланс
        new_balance = user['balance'] - amount_usd
        now = int(time.time())
        
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, uid))
        c.execute(
            "INSERT INTO history (user_id, type, amount, currency, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, 'exchange', amount_usd, to_curr, f'{amount} {from_curr} → {to_curr}', now)
        )
        conn.commit()
        conn.close()
        
        return jsonify({
            'ok': True,
            'new_balance': new_balance
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/promo', methods=['POST'])
def api_promo():
    """Быстрая активация промокода"""
    try:
        data = request.json
        uid = data.get('id')
        code = data.get('code', '').strip().upper()
        
        if not uid or not code:
            return jsonify({'error': 'Missing data'}), 400
        
        conn = get_db()
        c = conn.cursor()
        
        # Проверяем промокод
        c.execute("SELECT * FROM promocodes WHERE code = ? AND uses > 0", (code,))
        promo = c.fetchone()
        
        if not promo:
            conn.close()
            return jsonify({'error': 'Invalid or expired promo code'}), 404
        
        # Проверяем, не использовал ли пользователь
        c.execute(
            "SELECT id FROM history WHERE user_id = ? AND details LIKE ?",
            (uid, f'%{code}%')
        )
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'Promo code already used'}), 400
        
        amount = promo['amount']
        now = int(time.time())
        
        # Начисляем бонус
        c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, uid))
        c.execute("UPDATE promocodes SET uses = uses - 1 WHERE code = ?", (code,))
        c.execute(
            "INSERT INTO history (user_id, type, amount, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, 'promo', amount, f'Промокод {code}', now)
        )
        
        conn.commit()
        conn.close()
        
        return jsonify({'ok': True, 'amount': amount})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/history', methods=['GET'])
def api_history():
    """Быстрая история операций"""
    try:
        uid = request.args.get('id', type=int)
        limit = request.args.get('limit', 20, type=int)
        
        if not uid:
            return jsonify({'error': 'Missing user ID'}), 400
        
        rows = execute_read(
            """SELECT type, amount, currency, details, created_at 
               FROM history 
               WHERE user_id = ? 
               ORDER BY created_at DESC 
               LIMIT ?""",
            (uid, limit)
        )
        
        result = []
        for row in rows:
            result.append({
                'type': row['type'],
                'amount': row['amount'],
                'currency': row['currency'] or 'USD',
                'details': row['details'],
                'date': datetime.fromtimestamp(row['created_at']).isoformat()
            })
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/referrals', methods=['GET'])
def api_referrals():
    """Быстрая статистика рефералов"""
    try:
        uid = request.args.get('id', type=int)
        
        if not uid:
            return jsonify({'error': 'Missing user ID'}), 400
        
        # Количество рефералов
        count_row = execute_read(
            "SELECT COUNT(*) as count FROM referrals WHERE referrer_id = ?",
            (uid,)
        )
        count = count_row[0]['count'] if count_row else 0
        
        # Сумма бонусов
        bonus_row = execute_read(
            "SELECT SUM(amount) as total FROM history WHERE user_id = ? AND type = 'referral_bonus'",
            (uid,)
        )
        total = bonus_row[0]['total'] or 0 if bonus_row else 0
        
        return jsonify({
            'count': count,
            'total_bonus': total
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/daily_bonus', methods=['POST'])
def api_daily_bonus():
    """Быстрое получение бонуса"""
    try:
        data = request.json
        uid = data.get('id')
        
        if not uid:
            return jsonify({'error': 'Missing user ID'}), 400
        
        conn = get_db()
        c = conn.cursor()
        
        c.execute("SELECT last_bonus FROM users WHERE id = ?", (uid,))
        user = c.fetchone()
        
        if not user:
            conn.close()
            return jsonify({'error': 'User not found'}), 404
        
        now = int(time.time())
        today_start = now - (now % 86400)
        last_bonus = user['last_bonus'] or 0
        
        if last_bonus >= today_start:
            conn.close()
            return jsonify({'error': 'Already claimed today'}), 400
        
        bonus = 1.0
        
        c.execute(
            "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE id = ?",
            (bonus, now, uid)
        )
        c.execute(
            "INSERT INTO history (user_id, type, amount, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, 'daily_bonus', bonus, 'Ежедневный бонус', now)
        )
        
        conn.commit()
        conn.close()
        
        return jsonify({'ok': True, 'amount': bonus})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========================
# АДМИН ЭНДПОИНТЫ
# ========================

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        admin_id = request.args.get('admin')
        if not admin_id or int(admin_id) != ADMIN_ID:
            return jsonify({'error': 'Access denied'}), 403
        return f(*args, **kwargs)
    return decorated

@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def admin_stats():
    """Быстрая статистика для админа"""
    try:
        total_users = execute_read("SELECT COUNT(*) as count FROM users")[0]['count']
        total_banned = execute_read("SELECT COUNT(*) as count FROM users WHERE banned = 1")[0]['count']
        total_balance = execute_read("SELECT SUM(balance) as total FROM users")[0]['total'] or 0
        total_promos = execute_read("SELECT COUNT(*) as count FROM promocodes")[0]['count']
        
        return jsonify({
            'total_users': total_users,
            'total_banned': total_banned,
            'total_balance': total_balance,
            'total_promos': total_promos
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_users():
    """Быстрый список пользователей"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        offset = (page - 1) * per_page
        
        rows = execute_read(
            """SELECT id, username, balance, banned, created_at 
               FROM users 
               ORDER BY id 
               LIMIT ? OFFSET ?""",
            (per_page, offset)
        )
        
        total = execute_read("SELECT COUNT(*) as count FROM users")[0]['count']
        
        result = []
        for row in rows:
            result.append({
                'id': row['id'],
                'username': row['username'],
                'balance': row['balance'],
                'banned': bool(row['banned']),
                'created_at': datetime.fromtimestamp(row['created_at']).isoformat() if row['created_at'] else None
            })
        
        return jsonify({
            'users': result,
            'total': total,
            'page': page,
            'per_page': per_page
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/user/<int:user_id>', methods=['POST'])
@admin_required
def admin_update_user(user_id):
    """Быстрое обновление пользователя"""
    try:
        data = request.json
        action = data.get('action')
        value = data.get('value')
        
        conn = get_db()
        c = conn.cursor()
        
        if action == 'set_balance':
            c.execute("UPDATE users SET balance = ? WHERE id = ?", (float(value), user_id))
        elif action == 'add_balance':
            c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (float(value), user_id))
        elif action == 'ban':
            c.execute("UPDATE users SET banned = 1 WHERE id = ?", (user_id,))
        elif action == 'unban':
            c.execute("UPDATE users SET banned = 0 WHERE id = ?", (user_id,))
        else:
            conn.close()
            return jsonify({'error': 'Invalid action'}), 400
        
        conn.commit()
        conn.close()
        
        return jsonify({'ok': True})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/promocode', methods=['POST'])
@admin_required
def admin_create_promo():
    """Быстрое создание промокода"""
    try:
        data = request.json
        code = data.get('code', '').strip().upper()
        amount = float(data.get('amount', 0))
        uses = int(data.get('uses', 1))
        admin_id = data.get('admin')
        
        if not code or amount <= 0 or uses <= 0:
            return jsonify({'error': 'Invalid data'}), 400
        
        now = int(time.time())
        
        execute_write(
            "INSERT INTO promocodes (code, amount, uses, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (code, amount, uses, admin_id, now)
        )
        
        return jsonify({'ok': True})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Для Vercel
def handler(request):
    return app(request)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
