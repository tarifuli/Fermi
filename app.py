from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, session
import sqlite3, json, os, hashlib, secrets
from db_backend import get_db as connect_database
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import anthropic
import io
from decimal import Decimal

app = Flask(__name__)
if os.environ.get('VERCEL') and not os.environ.get('SECRET_KEY'):
    raise RuntimeError('SECRET_KEY must be set for production')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=bool(os.environ.get('VERCEL')))

DATABASE = os.environ.get('DATABASE_PATH', 'fermi.db')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', 'YOUR_API_KEY_HERE')

@app.context_processor
def inject_now():
    return {'now': datetime.now().strftime('%b %d, %Y')}

with app.app_context():
    pass

# ── AUTH HELPERS ─────────────────────────────────────────────────────────────
def hash_password(password):
    return generate_password_hash(password)

def verify_password(stored, password):
    if len(stored) == 64 and all(c in '0123456789abcdef' for c in stored):
        return secrets.compare_digest(stored, hashlib.sha256(password.encode()).hexdigest())
    return check_password_hash(stored, password)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ── DATABASE ─────────────────────────────────────────────────────────────────
def get_db():
    return connect_database()

def init_db():
    if os.environ.get('DATABASE_URL'):
        # PostgreSQL schema is applied separately, not on request/import.
        return
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS businesses (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                name TEXT NOT NULL,\n                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            );\n            CREATE TABLE IF NOT EXISTS business_memberships (\n                business_id INTEGER NOT NULL,\n                user_id INTEGER NOT NULL,\n                role TEXT NOT NULL DEFAULT 'owner',\n                PRIMARY KEY (business_id, user_id),\n                FOREIGN KEY (business_id) REFERENCES businesses(id),\n                FOREIGN KEY (user_id) REFERENCES users(id)\n            );\n            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sku TEXT UNIQUE NOT NULL,
                category TEXT,
                price REAL NOT NULL,
                stock INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT UNIQUE NOT NULL,
                customer_name TEXT NOT NULL,
                customer_email TEXT,
                customer_phone TEXT,
                subtotal REAL NOT NULL,
                tax REAL NOT NULL DEFAULT 0,
                total REAL NOT NULL,
                status TEXT DEFAULT 'paid',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS invoice_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                product_id INTEGER,
                product_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                total REAL NOT NULL,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id),
                FOREIGN KEY (product_id) REFERENCES products(id)
            );
        ''')
        db.execute('''CREATE TABLE IF NOT EXISTS customer_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL REFERENCES businesses(id),
            customer_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (business_id, customer_key)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS checkout_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            request_key TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            invoice_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (business_id, request_key)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS inventory_settings (
            business_id INTEGER PRIMARY KEY,
            low_stock_threshold INTEGER NOT NULL DEFAULT 20 CHECK (low_stock_threshold >= 0)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            product_id INTEGER,
            product_name TEXT NOT NULL,
            quantity_change INTEGER NOT NULL,
            stock_before INTEGER NOT NULL,
            stock_after INTEGER NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL,
            reference_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS invoice_refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            invoice_id INTEGER NOT NULL,
            amount NUMERIC NOT NULL CHECK (amount > 0),
            reason TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        for table in ('products', 'invoices'):
            columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
            if 'business_id' not in columns:
                db.execute(f'ALTER TABLE {table} ADD COLUMN business_id INTEGER REFERENCES businesses(id)')
            db.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_business ON {table}(business_id)')
        count = db.execute('SELECT COUNT(*) FROM products').fetchone()[0]
        if count == 0 and os.environ.get('FERMI_SEED_DEMO', '').lower() == 'true':
            db.executemany('INSERT INTO products (name, sku, category, price, stock) VALUES (?,?,?,?,?)', [
                ('Premium Leather Bag',  'PLB-001', 'Bags',        2500, 45),
                ('Classic Wallet',       'CW-002',  'Accessories', 850,  120),
                ('Business Card Holder', 'BCH-003', 'Accessories', 450,  80),
                ('Leather Belt',         'LB-004',  'Belts',       750,  60),
                ('Laptop Sleeve 15"',    'LS-005',  'Bags',        1800, 30),
                ('Key Organizer',        'KO-006',  'Accessories', 350,  150),
                ('Travel Duffle Bag',    'TDB-007', 'Bags',        3500, 20),
                ('Passport Holder',      'PH-008',  'Accessories', 600,  90),
            ])
            import random
            random.seed(42)
            customers = [
                ('Rahman Textiles',  'rahman@example.com',   '01711-111111'),
                ('Dhaka Traders',    'dhaka@example.com',    '01722-222222'),
                ('City Merchants',   'city@example.com',     '01733-333333'),
                ('BD Fashion House', 'bdfashion@example.com','01744-444444'),
            ]
            for i in range(1, 25):
                date = datetime.now() - timedelta(days=random.randint(0, 30))
                customer = random.choice(customers)
                inv_num = f'FRM-{1000+i}'
                subtotal = 0
                item_data = []
                for _ in range(random.randint(1, 3)):
                    prod_id = random.randint(1, 8)
                    qty = random.randint(1, 5)
                    prod = db.execute('SELECT * FROM products WHERE id=?', (prod_id,)).fetchone()
                    if prod:
                        t = qty * prod['price']
                        subtotal += t
                        item_data.append((prod_id, prod['name'], qty, prod['price'], t))
                tax   = round(subtotal * 0.05, 2)
                total = subtotal + tax
                db.execute(
                    'INSERT INTO invoices (invoice_number, customer_name, customer_email, customer_phone, subtotal, tax, total, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                    (inv_num, customer[0], customer[1], customer[2], subtotal, tax, total, 'paid', date.strftime('%Y-%m-%d %H:%M:%S'))
                )
                inv_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
                for it in item_data:
                    db.execute('INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price, total) VALUES (?,?,?,?,?,?)',
                               (inv_id, it[0], it[1], it[2], it[3], it[4]))

if not os.environ.get('VERCEL'):
    init_db()

# ── HELPERS ──────────────────────────────────────────────────────────────────
def next_invoice_number(business_id=None):
    # Globally unique across businesses, including pre-existing FRM-1001 records.
    # Do not trust a client-supplied invoice number.
    return f"FRM-{business_id}-{secrets.token_hex(6).upper()}"


def get_sales_data(days=30, business_id=None):
    with get_db() as db:
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        items = db.execute('''
            SELECT ii.product_name, SUM(ii.quantity) as qty, SUM(ii.total) as revenue
            FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
            WHERE DATE(i.created_at) >= ? AND i.status != 'cancelled' AND i.business_id=?
            GROUP BY ii.product_name ORDER BY qty DESC
        ''', (since, business_id)).fetchall()
        return [dict(r) for r in items]

def get_dashboard_stats(business_id=None):
    with get_db() as db:
        month_start = datetime.now().replace(day=1).strftime('%Y-%m-%d')
        gross = db.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid' AND business_id=?", (business_id,)).fetchone()[0]
        refunds = db.execute("SELECT COALESCE(SUM(r.amount),0) FROM invoice_refunds r JOIN invoices i ON i.id=r.invoice_id WHERE i.status='paid' AND r.business_id=?", (business_id,)).fetchone()[0]
        month_gross = db.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid' AND DATE(created_at)>=? AND business_id=?", (month_start, business_id)).fetchone()[0]
        month_refunds = db.execute("SELECT COALESCE(SUM(r.amount),0) FROM invoice_refunds r JOIN invoices i ON i.id=r.invoice_id WHERE i.status='paid' AND DATE(r.created_at)>=? AND r.business_id=?", (month_start, business_id)).fetchone()[0]
        total_invoices = db.execute("SELECT COUNT(*) FROM invoices WHERE business_id=?", (business_id,)).fetchone()[0]
        threshold = db.execute('SELECT low_stock_threshold FROM inventory_settings WHERE business_id=?', (business_id,)).fetchone()
        limit = threshold['low_stock_threshold'] if threshold else 20
        low_stock = db.execute("SELECT COUNT(*) FROM products WHERE stock>0 AND stock<=? AND business_id=?", (limit, business_id)).fetchone()[0]
        total_products = db.execute("SELECT COUNT(*) FROM products WHERE business_id=?", (business_id,)).fetchone()[0]
        daily = []
        for i in range(6, -1, -1):
            d = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            sales = db.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE DATE(created_at)=? AND status='paid' AND business_id=?", (d, business_id)).fetchone()[0]
            returned = db.execute("SELECT COALESCE(SUM(r.amount),0) FROM invoice_refunds r JOIN invoices i ON i.id=r.invoice_id WHERE DATE(r.created_at)=? AND i.status='paid' AND r.business_id=?", (d, business_id)).fetchone()[0]
            daily.append({'date': d, 'revenue': float(Decimal(str(sales))-Decimal(str(returned)))})
        return {'total_revenue': float(Decimal(str(gross))-Decimal(str(refunds))),
                'month_revenue': float(Decimal(str(month_gross))-Decimal(str(month_refunds))),
                'total_invoices': total_invoices, 'low_stock': low_stock,
                'total_products': total_products, 'daily': daily}


# ── AUTH ROUTES ──────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    error = None
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        with get_db() as db:
            user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if user and verify_password(user['password'], password):
            if len(user['password']) == 64 and all(c in '0123456789abcdef' for c in user['password']):
                with get_db() as db:
                    db.execute('UPDATE users SET password=? WHERE id=?', (hash_password(password), user['id']))
            session['user_id']   = user['id']
            session['user_name'] = user['name']
            session['user_email']= user['email']
            return redirect(url_for('dashboard'))
        else:
            error = 'Invalid email or password. Please try again.'
    return render_template('login.html', error=error)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    error = None
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')
        if not name or not email or not password:
            error = 'All fields are required.'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters.'
        elif password != confirm:
            error = 'Passwords do not match.'
        else:
            try:
                with get_db() as db:
                    db.execute('INSERT INTO users (name, email, password) VALUES (?,?,?)',
                               (name, email, hash_password(password)))
                    user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
                    session['user_id']    = user['id']
                    session['user_name']  = user['name']
                    session['user_email'] = user['email']
                return redirect(url_for('dashboard'))
            except Exception as e:
                if 'UNIQUE' in str(e):
                    error = 'An account with this email already exists. Please log in.'
                else:
                    error = 'Something went wrong. Please try again.'
    return render_template('signup.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── BUSINESS PROFILE FOUNDATION ─────────────────────────────────────────────
@app.route('/business/setup', methods=['GET', 'POST'])
@login_required
def business_setup():
    """Optional business setup; existing dashboard remains available after signup."""
    with get_db() as db:
        existing = db.execute(
            'SELECT b.id, b.name FROM businesses b JOIN business_memberships m '
            'ON m.business_id=b.id WHERE m.user_id=? ORDER BY b.id LIMIT 1',
            (session['user_id'],)
        ).fetchone()
    if existing:
        return redirect(url_for('business_profile'))
    error = None
    if request.method == 'POST':
        name = request.form.get('business_name', '').strip()
        if not 1 <= len(name) <= 120:
            error = 'Enter a business name of up to 120 characters.'
        else:
            with get_db() as db:
                cur = db.execute('INSERT INTO businesses (name) VALUES (?)' + (' RETURNING id' if os.environ.get('DATABASE_URL') else ''), (name,))
                db.execute(
                    'INSERT INTO business_memberships (business_id,user_id,role) VALUES (?,?,?)',
                    ((cur.fetchone()['id'] if os.environ.get('DATABASE_URL') else cur.lastrowid), session['user_id'], 'owner')
                )
            return redirect(url_for('business_profile'))
    return render_template('business_setup.html', error=error)


@app.route('/business/profile')
@login_required
def business_profile():
    with get_db() as db:
        business = db.execute(
            'SELECT b.id, b.name, b.created_at FROM businesses b '
            'JOIN business_memberships m ON m.business_id=b.id '
            'WHERE m.user_id=? ORDER BY b.id LIMIT 1',
            (session['user_id'],)
        ).fetchone()
    if not business:
        return redirect(url_for('business_setup'))
    return render_template('business_profile.html', business=dict(business))


def current_business_id():
    if 'user_id' not in session:
        return None
    with get_db() as db:
        row = db.execute('SELECT business_id FROM business_memberships WHERE user_id=? ORDER BY business_id LIMIT 1', (session['user_id'],)).fetchone()
    return row['business_id'] if row else None


def require_business():
    business_id = current_business_id()
    if business_id is None:
        return None, (jsonify({'success': False, 'error': 'Set up a business to use this feature.'}), 403)
    return business_id, None


# ── MAIN ROUTES (protected) ──────────────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    business_id = current_business_id()
    return render_template('dashboard.html', stats=get_dashboard_stats(business_id),
                           has_business=business_id is not None,
                           sales_7=get_sales_data(7, business_id)[:5], sales_30=get_sales_data(30, business_id)[:5])

@app.route('/analytics')
@login_required
def analytics():
    """Business-scoped sales report from persisted invoices, without expense/P&L tracking."""
    from datetime import date, timedelta
    from decimal import Decimal
    business_id = current_business_id()
    if business_id is None:
        return redirect(url_for('business_setup'))
    period = request.args.get('days', '30')
    if period not in ('7', '30', '90'):
        period = '30'
    days = int(period)
    today = date.today()
    first_day = today - timedelta(days=days - 1)
    with get_db() as db:
        invoices = db.execute(
            'SELECT id, created_at, total, status, customer_name FROM invoices WHERE business_id=? AND created_at>=? ORDER BY created_at',
            (business_id, first_day.isoformat())
        ).fetchall()
        product_rows = db.execute('''
            SELECT ii.product_name, ii.quantity, ii.total, i.created_at, i.status
            FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id
            WHERE i.business_id=? AND i.created_at>=?
        ''', (business_id, first_day.isoformat())).fetchall()
    daily = {str(first_day + timedelta(days=n)): Decimal('0') for n in range(days)}
    paid_revenue = Decimal('0')
    pending_amount = Decimal('0')
    paid_count = 0
    pending_count = 0
    customers = set()
    for inv in invoices:
        day = str(inv['created_at'])[:10]
        if day not in daily:
            continue
        amount = Decimal(str(inv['total']))
        if inv['status'] == 'paid':
            daily[day] += amount
            paid_revenue += amount
            paid_count += 1
            customers.add(inv['customer_name'].strip().lower())
        elif inv['status'] == 'pending':
            pending_amount += amount
            pending_count += 1
    with get_db() as db:
        refund_rows = db.execute('''SELECT r.amount,r.created_at FROM invoice_refunds r
            JOIN invoices i ON i.id=r.invoice_id
            WHERE r.business_id=? AND i.status='paid' AND r.created_at>=?''',
            (business_id, first_day.isoformat())).fetchall()
    refund_total = Decimal('0')
    for refund in refund_rows:
        day = str(refund['created_at'])[:10]
        if day in daily:
            amount = Decimal(str(refund['amount']))
            daily[day] -= amount
            refund_total += amount
    paid_revenue -= refund_total
    products = {}
    for item in product_rows:
        if item['status'] != 'paid' or str(item['created_at'])[:10] not in daily:
            continue
        name = item['product_name']
        entry = products.setdefault(name, {'name': name, 'units': 0, 'revenue': Decimal('0')})
        entry['units'] += int(item['quantity'])
        entry['revenue'] += Decimal(str(item['total']))
    top_products = sorted(products.values(), key=lambda p: (-p['units'], p['name']))[:8]
    chart = [{'date': day, 'revenue': float(amount)} for day, amount in daily.items()]
    return render_template('analytics.html', days=days, chart=chart,
                           paid_revenue=paid_revenue, refund_total=refund_total, pending_amount=pending_amount,
                           paid_count=paid_count, pending_count=pending_count,
                           unique_customers=len(customers), top_products=top_products,
                           avg_order=(paid_revenue / paid_count if paid_count else Decimal('0')))


def record_stock_movement(db, business_id, product_id, product_name, before, after, reason, source, reference_id=None):
    if before == after:
        return
    db.execute('''INSERT INTO stock_movements
        (business_id, product_id, product_name, quantity_change, stock_before, stock_after, reason, source, reference_id)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (business_id, product_id, product_name, after - before, before, after, reason, source, reference_id))


@app.route('/products')
@login_required
def products():
    business_id, error = require_business()
    if error:
        return error
    with get_db() as db:
        prods = [dict(p) for p in db.execute(
            'SELECT * FROM products WHERE business_id=? ORDER BY name', (business_id,)).fetchall()]
        setting = db.execute('SELECT low_stock_threshold FROM inventory_settings WHERE business_id=?',
                             (business_id,)).fetchone()
    threshold = setting['low_stock_threshold'] if setting else 20
    return render_template('products.html', products=prods, threshold=threshold,
                           low_count=sum(0 < p['stock'] <= threshold for p in prods),
                           out_count=sum(p['stock'] == 0 for p in prods))


@app.route('/products/threshold', methods=['POST'])
@login_required
def set_inventory_threshold():
    business_id, error = require_business()
    if error:
        return error
    d = request.get_json(silent=True) or {}
    threshold = d.get('threshold')
    if type(threshold) is not int or not 0 <= threshold <= 100000000:
        return jsonify({'success': False, 'error': 'Threshold must be a whole number from 0 to 100,000,000'}), 400
    with get_db() as db:
        db.execute('''INSERT INTO inventory_settings (business_id, low_stock_threshold)
            VALUES (?,?) ON CONFLICT (business_id) DO UPDATE SET low_stock_threshold=excluded.low_stock_threshold''',
            (business_id, threshold))
    return jsonify({'success': True, 'threshold': threshold})


@app.route('/products/history')
@login_required
def inventory_history():
    business_id, error = require_business()
    if error:
        return error
    with get_db() as db:
        movements = db.execute('''SELECT * FROM stock_movements WHERE business_id=?
            ORDER BY created_at DESC, id DESC LIMIT 200''', (business_id,)).fetchall()
    return render_template('inventory_history.html', movements=movements)


@app.route('/products/add', methods=['POST'])
@login_required
def add_product():
    d = request.json
    business_id, error = require_business()
    if error:
        return error
    try:
        with get_db() as db:
            initial_stock = int(d['stock'])
            if initial_stock < 0 or initial_stock > 100000000:
                return jsonify({'success': False, 'error': 'Invalid starting stock'}), 400
            db.execute('INSERT INTO products (name, sku, category, price, stock, business_id) VALUES (?,?,?,?,?,?)',
                       (d['name'], d['sku'], d['category'], float(d['price']), initial_stock, business_id))
            product_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            record_stock_movement(db, business_id, product_id, d['name'], 0, initial_stock,
                                  'Initial stock', 'product_creation')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/products/edit/<int:pid>', methods=['POST'])
@login_required
def edit_product(pid):
    d = request.json
    business_id, error = require_business()
    if error:
        return error
    try:
        with get_db() as db:
            existing = db.execute('SELECT name,stock FROM products WHERE id=? AND business_id=?',
                                  (pid, business_id)).fetchone()
            if existing is None:
                return jsonify({'success': False, 'error': 'Product not found'}), 404
            new_stock = int(d['stock'])
            if new_stock < 0 or new_stock > 100000000:
                return jsonify({'success': False, 'error': 'Invalid stock'}), 400
            db.execute('UPDATE products SET name=?, sku=?, category=?, price=?, stock=? WHERE id=? AND business_id=?',
                       (d['name'], d['sku'], d['category'], float(d['price']), new_stock, pid, business_id))
            record_stock_movement(db, business_id, pid, d['name'], existing['stock'], new_stock,
                                  'Stock changed in product editor', 'product_edit')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/products/adjust/<int:pid>', methods=['POST'])
@login_required
def adjust_product_stock(pid):
    """Atomic, business-scoped stock correction; refuses negative stock."""
    business_id, error = require_business()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        raw_delta = data.get('delta')
        if isinstance(raw_delta, bool) or not isinstance(raw_delta, (int, str)) or not str(raw_delta).lstrip('-').isdigit():
            raise ValueError()
        delta = int(raw_delta)
        if abs(delta) > 100000 or delta == 0:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Enter a non-zero whole-number adjustment.'}), 400
    reason = data.get('reason', '')
    if not isinstance(reason, str) or len(reason.strip()) > 500:
        return jsonify({'success': False, 'error': 'Invalid adjustment reason'}), 400
    reason = reason.strip() or ('Restock' if delta > 0 else 'Manual stock reduction')
    with get_db() as db:
        result = db.execute(
            'UPDATE products SET stock=stock+? WHERE id=? AND business_id=? AND stock+?>=0',
            (delta, pid, business_id, delta)
        )
        if result.rowcount != 1:
            return jsonify({'success': False, 'error': 'Product unavailable or insufficient stock.'}), 400
        row = db.execute('SELECT name,stock FROM products WHERE id=? AND business_id=?', (pid, business_id)).fetchone()
        record_stock_movement(db, business_id, pid, row['name'], row['stock'] - delta,
                              row['stock'], reason, 'manual_adjustment')
    return jsonify({'success': True, 'stock': row['stock']})


@app.route('/products/return/<int:pid>', methods=['POST'])
@login_required
def return_product_stock(pid):
    """Receive a returned item back into sellable inventory atomically."""
    business_id, error = require_business()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    quantity = data.get('quantity')
    if type(quantity) is not int or not 1 <= quantity <= 100000:
        return jsonify({'success': False, 'error': 'Return quantity must be a positive whole number (max 100,000).'}), 400
    reason = data.get('reason', '')
    if not isinstance(reason, str) or len(reason.strip()) > 500:
        return jsonify({'success': False, 'error': 'Invalid return reason.'}), 400
    reason = reason.strip() or 'Customer return — restocked'
    with get_db() as db:
        updated = db.execute('''UPDATE products SET stock=stock+?
            WHERE id=? AND business_id=? AND stock+?<=100000000''',
            (quantity, pid, business_id, quantity))
        if updated.rowcount != 1:
            return jsonify({'success': False, 'error': 'Product unavailable or return exceeds stock limit.'}), 400
        product = db.execute('SELECT name,stock FROM products WHERE id=? AND business_id=?',
                             (pid, business_id)).fetchone()
        record_stock_movement(db, business_id, pid, product['name'],
                              product['stock'] - quantity, product['stock'], reason, 'customer_return')
    return jsonify({'success': True, 'stock': product['stock']})


@app.route('/products/delete/<int:pid>', methods=['POST'])
@login_required
def delete_product(pid):
    business_id, error = require_business()
    if error:
        return error
    with get_db() as db:
        result = db.execute('DELETE FROM products WHERE id=? AND business_id=?', (pid, business_id))
    return jsonify({'success': result.rowcount == 1})

@app.route('/api/products')
@login_required
def api_products():
    with get_db() as db:
        prods = db.execute('SELECT * FROM products WHERE business_id=? ORDER BY name', (current_business_id(),)).fetchall()
    return jsonify([dict(p) for p in prods])

# Customer directory is derived from existing invoices, requiring no schema change.
# Every query is scoped to the signed-in user's business.
@app.route('/customers')
@login_required
def customers():
    business_id = current_business_id()
    if business_id is None:
        return redirect(url_for('business_setup'))
    with get_db() as db:
        rows = db.execute('''
            SELECT LOWER(TRIM(customer_name)) AS customer_key,
                   MIN(customer_name) AS customer_name,
                   COUNT(*) AS invoice_count,
                   COALESCE(SUM(CASE WHEN status = 'paid' THEN total ELSE 0 END), 0) AS paid_total,
                   MAX(created_at) AS last_invoice
            FROM invoices
            WHERE business_id=? AND TRIM(customer_name) != ''
            GROUP BY LOWER(TRIM(customer_name))
            ORDER BY MAX(created_at) DESC
        ''', (business_id,)).fetchall()
    directory = [dict(row) for row in rows]
    returning = sum(1 for row in directory if row['invoice_count'] > 1)
    paid_customers = sum(1 for row in directory if row['paid_total'] > 0)
    return render_template('customers.html', customers=directory,
                           returning_customers=returning, paid_customers=paid_customers)


@app.route('/customers/profile', methods=['GET', 'POST'])
@login_required
def customer_profile():
    business_id = current_business_id()
    if business_id is None:
        return redirect(url_for('business_setup'))
    key = request.values.get('name', '').strip().lower()
    if not key or len(key) > 200:
        return redirect(url_for('customers'))
    with get_db() as db:
        historical = db.execute('''
            SELECT customer_name, customer_email, customer_phone
            FROM invoices WHERE business_id=? AND LOWER(TRIM(customer_name))=?
            ORDER BY created_at DESC, id DESC LIMIT 1
        ''', (business_id, key)).fetchone()
        profile = db.execute('SELECT * FROM customer_profiles WHERE business_id=? AND customer_key=?',
                             (business_id, key)).fetchone()
        if historical is None and profile is None:
            return redirect(url_for('customers'))
        if request.method == 'POST':
            display_name = request.form.get('display_name', '').strip()
            email = request.form.get('email', '').strip()
            phone = request.form.get('phone', '').strip()
            notes = request.form.get('notes', '').strip()
            if not (1 <= len(display_name) <= 200 and len(email) <= 254 and len(phone) <= 40 and len(notes) <= 2000):
                return 'Invalid customer profile fields', 400
            if email and ('@' not in email or email.startswith('@') or email.endswith('@')):
                return 'Enter a valid email address', 400
            if profile:
                db.execute('''UPDATE customer_profiles SET display_name=?,email=?,phone=?,notes=?,
                              updated_at=CURRENT_TIMESTAMP WHERE id=? AND business_id=?''',
                           (display_name, email, phone, notes, profile['id'], business_id))
            else:
                db.execute('''INSERT INTO customer_profiles
                              (business_id,customer_key,display_name,email,phone,notes)
                              VALUES (?,?,?,?,?,?)''',
                           (business_id, key, display_name, email, phone, notes))
            return redirect(url_for('customer_profile', name=key, saved='1'))
    values = dict(profile) if profile else {
        'display_name': historical['customer_name'],
        'email': historical['customer_email'] or '',
        'phone': historical['customer_phone'] or '',
        'notes': ''
    }
    return render_template('customer_profile.html', profile=values, customer_key=key,
                           saved=request.args.get('saved') == '1')


@app.route('/api/customers/lookup')
@login_required
def customer_lookup():
    business_id = current_business_id()
    if business_id is None:
        return jsonify({'customers': []}), 403
    with get_db() as db:
        rows = db.execute('''
            SELECT LOWER(TRIM(customer_name)) AS customer_key,
                   MIN(customer_name) AS historical_name,
                   MAX(customer_email) AS historical_email,
                   MAX(customer_phone) AS historical_phone
            FROM invoices
            WHERE business_id=? AND TRIM(customer_name)!=''
            GROUP BY LOWER(TRIM(customer_name))
            ORDER BY MIN(customer_name)
        ''', (business_id,)).fetchall()
        profiles = db.execute('SELECT customer_key,display_name,email,phone FROM customer_profiles WHERE business_id=?',
                              (business_id,)).fetchall()
    result = {}
    for row in rows:
        key = row['customer_key']
        result[key] = {'key': key, 'name': row['historical_name'],
                       'email': row['historical_email'] or '', 'phone': row['historical_phone'] or ''}
    for row in profiles:
        key = row['customer_key']
        result[key] = {'key': key, 'name': row['display_name'],
                       'email': row['email'], 'phone': row['phone']}
    return jsonify({'customers': sorted(result.values(), key=lambda item: item['name'].lower())})


@app.route('/customers/history')
@login_required
def customer_history():
    business_id = current_business_id()
    if business_id is None:
        return redirect(url_for('business_setup'))
    customer_key = request.args.get('name', '').strip().lower()
    if not customer_key or len(customer_key) > 200:
        return redirect(url_for('customers'))
    with get_db() as db:
        rows = db.execute('''
            SELECT id, invoice_number, customer_name, customer_email,
                   customer_phone, created_at, total, status
            FROM invoices
            WHERE business_id=? AND LOWER(TRIM(customer_name))=?
            ORDER BY created_at DESC, id DESC
        ''', (business_id, customer_key)).fetchall()
    if not rows:
        return redirect(url_for('customers'))
    invoices_for_customer = [dict(row) for row in rows]
    paid_total = sum((row['total'] for row in invoices_for_customer if row['status'] == 'paid'), 0)
    with get_db() as db:
        product_rows = db.execute('''
            SELECT ii.product_name, SUM(ii.quantity) AS units,
                   SUM(ii.total) AS revenue
            FROM invoice_items ii
            JOIN invoices i ON i.id=ii.invoice_id
            WHERE i.business_id=? AND LOWER(TRIM(i.customer_name))=? AND i.status='paid'
            GROUP BY ii.product_name
            ORDER BY SUM(ii.quantity) DESC, ii.product_name
            LIMIT 5
        ''', (business_id, customer_key)).fetchall()
    paid_invoices = sum(1 for row in invoices_for_customer if row['status'] == 'paid')
    pending_total = sum((row['total'] for row in invoices_for_customer if row['status'] == 'pending'), 0)
    return render_template('customer_history.html',
                           customer_name=invoices_for_customer[0]['customer_name'],
                           customer_email=next((row['customer_email'] for row in invoices_for_customer if row['customer_email']), ''),
                           customer_phone=next((row['customer_phone'] for row in invoices_for_customer if row['customer_phone']), ''),
                           paid_total=paid_total, paid_invoices=paid_invoices,
                           pending_total=pending_total,
                           favorite_products=[dict(row) for row in product_rows],
                           invoices=invoices_for_customer)


@app.route('/invoices')
@login_required
def invoices():
    with get_db() as db:
        invs = db.execute('''SELECT i.*, COALESCE((SELECT SUM(r.amount) FROM invoice_refunds r
            WHERE r.invoice_id=i.id AND r.business_id=i.business_id),0) AS refunded
            FROM invoices i WHERE i.business_id=? ORDER BY i.created_at DESC''', (current_business_id(),)).fetchall()
    return render_template('invoices.html', invoices=[dict(i) for i in invs])

@app.route('/invoices/<int:inv_id>/refund', methods=['POST'])
@login_required
def record_invoice_refund(inv_id):
    from decimal import Decimal, InvalidOperation
    business_id, error = require_business()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    raw_amount = data.get('amount')
    if isinstance(raw_amount, bool) or not isinstance(raw_amount, (str, int, float)):
        return jsonify({'success': False, 'error': 'Enter a valid refund amount.'}), 400
    try:
        amount = Decimal(str(raw_amount))
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2 or amount > 100000000:
            raise ValueError()
    except (InvalidOperation, ValueError):
        return jsonify({'success': False, 'error': 'Refund must be positive with at most two decimal places.'}), 400
    reason = data.get('reason', '')
    if not isinstance(reason, str) or len(reason.strip()) > 500:
        return jsonify({'success': False, 'error': 'Invalid refund reason.'}), 400
    with get_db() as db:
        query = 'SELECT id,total,status FROM invoices WHERE id=? AND business_id=?'
        if os.environ.get('DATABASE_URL'):
            query += ' FOR UPDATE'
        invoice = db.execute(query, (inv_id, business_id)).fetchone()
        if invoice is None:
            return jsonify({'success': False, 'error': 'Invoice not found.'}), 404
        if invoice['status'] != 'paid':
            return jsonify({'success': False, 'error': 'Only paid invoices can be refunded.'}), 400
        prior = db.execute('SELECT COALESCE(SUM(amount),0) FROM invoice_refunds WHERE invoice_id=? AND business_id=?',
                           (inv_id, business_id)).fetchone()[0]
        remaining = Decimal(str(invoice['total'])) - Decimal(str(prior))
        if amount > remaining:
            return jsonify({'success': False, 'error': 'Refund exceeds remaining paid amount.'}), 400
        db.execute('INSERT INTO invoice_refunds (business_id,invoice_id,amount,reason) VALUES (?,?,?,?)',
                   (business_id, inv_id, str(amount), reason.strip()))
    return jsonify({'success': True, 'amount': float(amount), 'remaining': float(remaining - amount)})


@app.route('/pos')
@login_required
def pos():
    business_id = current_business_id()
    if business_id is None:
        return redirect(url_for('business_setup'))
    with get_db() as db:
        rows = db.execute(
            'SELECT id, name, sku, category, price, stock FROM products WHERE business_id=? ORDER BY name',
            (business_id,)
        ).fetchall()
    # Convert database Decimal prices for browser JSON serialization.
    catalog = [{**dict(row), 'price': float(row['price'])} for row in rows]
    return render_template('pos.html', products=catalog)


@app.route('/invoices/new')
@login_required
def new_invoice():
    with get_db() as db:
        prods = db.execute('SELECT * FROM products WHERE business_id=? ORDER BY name', (current_business_id(),)).fetchall()
    return render_template('new_invoice.html', invoice_number=next_invoice_number(current_business_id()), products=[dict(p) for p in prods])

@app.route('/invoices/create', methods=['POST'])
@login_required
def create_invoice():
    d = request.get_json(silent=True) or {}
    business_id, error = require_business()
    if error:
        return error
    try:
        items = d.get('items')
        if not isinstance(items, list) or not items or not isinstance(d.get('customer_name'), str) or not d['customer_name'].strip():
            return jsonify({'success': False, 'error': 'Customer and items are required'}), 400
        if len(items) > 100:
            return jsonify({'success': False, 'error': 'Maximum 100 invoice lines'}), 400
        if any(not isinstance(it, dict) or type(it.get('quantity')) is not int
               or not 1 <= it['quantity'] <= 100000
               or (it.get('product_id') is not None and
                   (type(it['product_id']) is not int or it['product_id'] <= 0))
               for it in items):
            return jsonify({'success': False, 'error': 'Invalid item or quantity'}), 400
        requested_stock = {}
        for item in items:
            pid = item.get('product_id')
            if pid is not None:
                requested_stock[pid] = requested_stock.get(pid, 0) + item['quantity']
        from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
        import hashlib
        import json
        import re
        request_key = d.get('idempotency_key')
        if request_key is not None and (
            not isinstance(request_key, str) or
            not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', request_key)
        ):
            return jsonify({'success': False, 'error': 'Invalid checkout request key'}), 400
        payload_hash = hashlib.sha256(json.dumps(
            {k: v for k, v in d.items() if k != 'idempotency_key'},
            sort_keys=True, separators=(',', ':'), ensure_ascii=False
        ).encode('utf-8')).hexdigest()
        selected_key = d.get('customer_key')
        if selected_key is not None and (not isinstance(selected_key, str) or not selected_key.strip() or len(selected_key) > 200):
            return jsonify({'success': False, 'error': 'Invalid customer selection'}), 400
        with get_db() as db:
            if request_key:
                claimed = db.execute('''INSERT INTO checkout_requests
                    (business_id, request_key, payload_hash) VALUES (?,?,?)
                    ON CONFLICT (business_id, request_key) DO NOTHING''',
                    (business_id, request_key, payload_hash))
                if claimed.rowcount == 0:
                    previous = db.execute('''SELECT payload_hash, invoice_id
                        FROM checkout_requests WHERE business_id=? AND request_key=?''',
                        (business_id, request_key)).fetchone()
                    if previous['payload_hash'] != payload_hash:
                        return jsonify({'success': False, 'error': 'Checkout key already used for another sale'}), 409
                    if previous['invoice_id'] is None:
                        return jsonify({'success': False, 'error': 'Checkout is still processing; retry shortly'}), 409
                    return jsonify({'success': True, 'invoice_id': previous['invoice_id'], 'replayed': True})
            if selected_key is not None:
                key = selected_key.strip().lower()
                profile = db.execute('SELECT display_name,email,phone FROM customer_profiles WHERE business_id=? AND customer_key=?',
                                     (business_id, key)).fetchone()
                if profile is not None:
                    customer_name, customer_email, customer_phone = profile['display_name'], profile['email'], profile['phone']
                else:
                    historical = db.execute('''SELECT customer_name,customer_email,customer_phone FROM invoices
                        WHERE business_id=? AND LOWER(TRIM(customer_name))=?
                        ORDER BY created_at DESC,id DESC LIMIT 1''', (business_id, key)).fetchone()
                    if historical is None:
                        return jsonify({'success': False, 'error': 'Selected customer was not found'}), 400
                    customer_name, customer_email, customer_phone = historical['customer_name'], historical['customer_email'], historical['customer_phone']
            else:
                customer_name = d['customer_name'].strip()
                customer_email = d.get('customer_email', '')
                customer_phone = d.get('customer_phone', '')
            if not isinstance(customer_name, str) or not customer_name.strip() or len(customer_name) > 200:
                return jsonify({'success': False, 'error': 'Invalid customer name'}), 400
            if not isinstance(customer_email, str) or len(customer_email) > 254 or not isinstance(customer_phone, str) or len(customer_phone) > 40:
                return jsonify({'success': False, 'error': 'Invalid customer contact information'}), 400
            line_items = []
            subtotal = Decimal('0.00')
            for item in items:
                product_id = item.get('product_id')
                if product_id is not None:
                    product = db.execute('SELECT id, name, price, stock FROM products WHERE id=? AND business_id=?',
                                         (product_id, business_id)).fetchone()
                    if product is None or product['stock'] < requested_stock[product_id]:
                        return jsonify({'success': False, 'error': 'Product unavailable or insufficient stock'}), 400
                    name = product['name']
                    price = Decimal(str(product['price']))
                else:
                    product = None
                    name = item.get('product_name', '')
                    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
                        return jsonify({'success': False, 'error': 'Enter a custom item name (up to 200 characters)'}), 400
                    name = name.strip()
                    try:
                        price = Decimal(str(item['unit_price']))
                    except (InvalidOperation, KeyError, TypeError, ValueError):
                        return jsonify({'success': False, 'error': 'Invalid custom item price'}), 400
                    if price.as_tuple().exponent < -2:
                        return jsonify({'success': False, 'error': 'Price may have at most two decimal places'}), 400
                if not price.is_finite() or price <= 0 or price > Decimal('1000000000'):
                    return jsonify({'success': False, 'error': 'Invalid item price'}), 400
                line_total = (price * item['quantity']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                subtotal += line_total
                line_items.append((product_id, name, item['quantity'], price, line_total))
            subtotal = subtotal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            tax = (subtotal * Decimal('0.05')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            total = subtotal + tax
            status = d.get('status', 'paid')
            if status not in ('paid', 'pending'):
                return jsonify({'success': False, 'error': 'Invalid invoice status'}), 400
            notes = d.get('notes', '')
            if not isinstance(notes, str) or len(notes) > 5000:
                return jsonify({'success': False, 'error': 'Invalid invoice notes'}), 400
            if subtotal > Decimal('100000000000'):
                return jsonify({'success': False, 'error': 'Invoice total exceeds limit'}), 400
            invoice_number = next_invoice_number(business_id)
            db.execute('INSERT INTO invoices (invoice_number, customer_name, customer_email, customer_phone, subtotal, tax, total, status, notes, business_id) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (invoice_number, customer_name, customer_email,
                        customer_phone, float(subtotal), float(tax), float(total),
                        status, notes, business_id))
            inv_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            for product_id, name, quantity, price, line_total in line_items:
                if product_id is not None:
                    updated = db.execute('UPDATE products SET stock=stock-? WHERE id=? AND business_id=? AND stock>=?',
                                         (quantity, product_id, business_id, quantity))
                    if updated.rowcount != 1:
                        raise ValueError('Product unavailable or insufficient stock')
                    after = db.execute('SELECT stock FROM products WHERE id=? AND business_id=?',
                                       (product_id, business_id)).fetchone()['stock']
                    record_stock_movement(db, business_id, product_id, name,
                                          after + quantity, after, 'Invoice sale', 'pos_sale', inv_id)
                db.execute('INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price, total) VALUES (?,?,?,?,?,?)',
                           (inv_id, product_id, name, quantity, float(price), float(line_total)))
            if request_key:
                db.execute('''UPDATE checkout_requests SET invoice_id=?
                    WHERE business_id=? AND request_key=?''', (inv_id, business_id, request_key))
        return jsonify({'success': True, 'invoice_id': inv_id})
    except (ValueError, TypeError, KeyError) as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Invoice creation failed')
        return jsonify({'success': False, 'error': 'Unable to create invoice'}), 500

@app.route('/invoices/<int:inv_id>')
@login_required
def view_invoice(inv_id):
    with get_db() as db:
        inv   = db.execute('SELECT * FROM invoices WHERE id=? AND business_id=?', (inv_id, current_business_id())).fetchone()
        items = db.execute('SELECT * FROM invoice_items WHERE invoice_id=?', (inv_id,)).fetchall()
        refunds = db.execute('SELECT * FROM invoice_refunds WHERE invoice_id=? AND business_id=? ORDER BY created_at DESC, id DESC',
                             (inv_id, current_business_id())).fetchall()
    if not inv: return redirect(url_for('invoices'))
    return render_template('view_invoice.html', invoice=dict(inv), items=[dict(i) for i in items], refunds=[dict(r) for r in refunds], refunded=sum(Decimal(str(r['amount'])) for r in refunds))

@app.route('/invoices/<int:inv_id>/pdf')
@login_required
def download_pdf(inv_id):
    with get_db() as db:
        row = db.execute('SELECT * FROM invoices WHERE id=? AND business_id=?', (inv_id, current_business_id())).fetchone()
        if row is None:
            return 'Invoice not found', 404
        inv = dict(row)
        items = [dict(i) for i in db.execute('SELECT * FROM invoice_items WHERE invoice_id=?', (inv_id,)).fetchall()]
    # The PDF mirrors templates/view_invoice.html, using the same persisted invoice data.
    # A fixed content width prevents the ReportLab tables from overflowing A4.
    from xml.sax.saxutils import escape
    from reportlab.platypus import KeepTogether, HRFlowable
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

    navy = colors.HexColor('#1A3A5C')
    blue = colors.HexColor('#2E6DA4')
    muted = colors.HexColor('#6B7A8D')
    paper = colors.HexColor('#F4F6F9')
    sky = colors.HexColor('#D6E8F7')
    border = colors.HexColor('#DDE3EA')
    green = colors.HexColor('#0F6E56')
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=42, leftMargin=42,
        topMargin=45, bottomMargin=45, title='Invoice ' + str(inv['invoice_number']),
        author='Fermi'
    )
    width = A4[0] - 84
    story = []
    def style(name, size=10, color=navy, bold=False, align=TA_LEFT, leading=None):
        return ParagraphStyle(
            name, fontName='Helvetica-Bold' if bold else 'Helvetica',
            fontSize=size, leading=leading or size * 1.45,
            textColor=color, alignment=align, spaceAfter=0
        )
    def p(value, st):
        return Paragraph(escape(str(value or '')), st)
    def money(value):
        return f"{float(value):,.2f}"
    def small_gap(points):
        story.append(Spacer(1, points))

    brand_title = style('brand', 24, navy, True)
    brand_sub = style('brand-sub', 9, muted)
    invoice_title = style('invoice-title', 19, blue, True, TA_RIGHT)
    invoice_num = style('invoice-num', 12, navy, True, TA_RIGHT)
    invoice_meta = style('invoice-meta', 9.5, muted, False, TA_RIGHT)
    status_style = style('invoice-status', 9, green, True, TA_RIGHT)

    left_header = [
        p('Fermi', brand_title),
        p('AI-Powered SME Intelligence Platform', brand_sub),
        p('Dhaka, Bangladesh', brand_sub),
    ]
    right_header = [
        p('INVOICE', invoice_title),
        p('#' + str(inv['invoice_number']), invoice_num),
        p('Date: ' + str(inv['created_at'])[:10], invoice_meta),
        p(str(inv['status']).capitalize(), status_style),
    ]
    header = Table([[left_header, right_header]], colWidths=[width * .51, width * .49])
    header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    small_gap(30)

    label_style = style('party-label', 8, muted, True)
    party_name = style('party-name', 11, navy, True)
    party_sub = style('party-sub', 9, muted)
    customer_contact = (inv.get('customer_email') or '')
    if inv.get('customer_phone'):
        customer_contact += (' · ' if customer_contact else '') + str(inv['customer_phone'])
    parties = Table([[
        [p('FROM', label_style), Spacer(1, 4), p('Fermi Platform', party_name),
         Spacer(1, 4), p('tarif@fermi.io', party_sub)],
        [p('BILL TO', label_style), Spacer(1, 4), p(inv['customer_name'], party_name),
         Spacer(1, 4), p(customer_contact, party_sub)]
    ]], colWidths=[width / 2, width / 2])
    parties.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), paper),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 16),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 16),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 16),
    ]))
    story.append(parties)
    small_gap(22)

    item_name_style = style('item-name', 9.5, navy)
    cell_style = style('item-cell', 9.5, navy)
    right_cell = style('item-right', 9.5, navy, False, TA_RIGHT)
    center_cell = style('item-center', 9.5, navy, False, TA_CENTER)
    head_style = style('item-head', 8, colors.white, True)
    head_center = style('item-head-center', 8, colors.white, True, TA_CENTER)
    head_right = style('item-head-right', 8, colors.white, True, TA_RIGHT)
    data = [[p('#', head_style), p('PRODUCT / DESCRIPTION', head_style),
             p('QTY', head_center), p('UNIT PRICE', head_right), p('TOTAL', head_right)]]
    for index, item in enumerate(items, 1):
        data.append([
            p(index, cell_style), p(item['product_name'], item_name_style),
            p(item['quantity'], center_cell),
            p('BDT ' + money(item['unit_price']), right_cell),
            p('BDT ' + money(item['total']), right_cell),
        ])
    table = Table(data, colWidths=[width * .07, width * .39, width * .10, width * .22, width * .22], repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), navy),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('TOPPADDING', (0, 1), (-1, -1), 11),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 11),
        ('LINEBELOW', (0, 1), (-1, -1), .4, border),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(table)
    small_gap(17)

    total_label = style('total-label', 10, muted)
    total_number = style('total-number', 10, navy, False, TA_RIGHT)
    grand_label = style('grand-label', 11, navy, True)
    grand_number = style('grand-number', 11, navy, True, TA_RIGHT)
    totals = Table([
        [p('Subtotal', total_label), p('BDT ' + money(inv['subtotal']), total_number)],
        [p('Tax (5%)', total_label), p('BDT ' + money(inv['tax']), total_number)],
        [p('Total (BDT)', grand_label), p('BDT ' + money(inv['total']), grand_number)],
    ], colWidths=[width * .70, width * .30])
    totals.setStyle(TableStyle([
        ('BACKGROUND', (0, 2), (-1, 2), sky),
        ('LINEBELOW', (0, 0), (-1, 1), .5, border),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(totals)
    if inv.get('notes'):
        small_gap(20)
        notes = Table([[[p('NOTES:', label_style), Spacer(1, 4),
                         p(inv['notes'], style('notes-text', 10, navy))]]], colWidths=[width])
        notes.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), paper),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ]))
        story.append(notes)
    small_gap(28)
    story.append(HRFlowable(width='100%', thickness=.6, color=border))
    small_gap(14)
    story.append(p('Thank you for your business! — Fermi Platform, Dhaka, Bangladesh',
                   style('footer', 9, muted, False, TA_CENTER)))
    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True,
                     download_name=f"{inv['invoice_number']}.pdf",
                     mimetype='application/pdf')

@app.route('/chatbot')
@login_required
def chatbot():
    return render_template('chatbot.html')

@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    user_message = request.json.get('message', '')
    business_id, error = require_business()
    if error:
        return error
    stats    = get_dashboard_stats(business_id)
    sales_7  = get_sales_data(7, business_id)
    sales_30 = get_sales_data(30, business_id)
    with get_db() as db:
        all_products    = [dict(p) for p in db.execute('SELECT * FROM products WHERE business_id=? ORDER BY stock ASC', (business_id,)).fetchall()]
        recent_invoices = [dict(i) for i in db.execute('SELECT * FROM invoices WHERE business_id=? ORDER BY created_at DESC LIMIT 10', (business_id,)).fetchall()]
    low_stock = [p for p in all_products if p['stock'] < 20]
    context = f"""You are Fermi AI, a business intelligence assistant for an SME in Bangladesh.
You have access to REAL-TIME business data. Always answer based on this data.

CURRENT BUSINESS DATA:
Total Revenue (all time): BDT {stats['total_revenue']:,}
Revenue This Month: BDT {stats['month_revenue']:,}
Total Invoices: {stats['total_invoices']}
Total Products: {stats['total_products']}
Low Stock Alerts: {stats['low_stock']} products below 20 units

TOP SELLING PRODUCTS (Last 7 Days): {json.dumps(sales_7, indent=2)}
TOP SELLING PRODUCTS (Last 30 Days): {json.dumps(sales_30, indent=2)}
DAILY REVENUE (Last 7 Days): {json.dumps(stats['daily'], indent=2)}
LOW STOCK PRODUCTS: {json.dumps(low_stock, indent=2)}
ALL PRODUCTS: {json.dumps(all_products, indent=2)}
RECENT INVOICES: {json.dumps(recent_invoices, indent=2)}

Instructions: Answer in a friendly professional tone. Cite specific numbers. Give actionable recommendations.
Compare 7-day vs 30-day data when asked about trends. Respond in the same language the user uses."""
    try:
        client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=800,
                                          system=context, messages=[{"role":"user","content":user_message}])
        return jsonify({'reply': response.content[0].text, 'success': True})
    except Exception as e:
        return jsonify({'reply': f'Error: {str(e)}.', 'success': False})

if __name__ == '__main__':
    init_db()
    print("\n" + "="*50)
    print("🚀  Fermi is running!")
    print("    Open: http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, port=5000)
