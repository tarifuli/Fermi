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
app.config.update(PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=2 * 1024 * 1024)

@app.after_request
def security_response_headers(response):
    """Safe baseline browser headers without changing application routes."""
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    if response.mimetype == 'text/html' and response.status_code == 200:
        # Install the CSRF helper for standalone templates as well as base.html.
        html = response.get_data(as_text=True)
        if '<meta name="csrf-token"' not in html and '</head>' in html:
            from markupsafe import escape
            token = escape(session.get('_csrf_token', ''))
            html = html.replace('</head>', '<meta name="csrf-token" content="' + str(token) + '"><script src="/static/csrf.js" defer></script></head>', 1)
            response.set_data(html)
    if 'user_id' in session or request.endpoint in ('login', 'signup'):
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
    if os.environ.get('VERCEL') and request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    return response

@app.context_processor
def inject_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe(32)
    return {'csrf_token': session['_csrf_token']}

@app.before_request
def verify_csrf_token():
    if app.config.get('TESTING') and not app.config.get('ENFORCE_CSRF_IN_TESTS'):
        return None
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    expected = session.get('_csrf_token', '')
    supplied = request.headers.get('X-CSRF-Token') or request.form.get('_csrf_token', '')
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        return jsonify({'success': False, 'error': 'Security token expired or missing. Refresh the page and retry.'}), 403
    return None

@app.before_request
def reject_cross_site_writes():
    """Reject explicitly cross-site unsafe requests using Fetch Metadata/Origin.

    Missing headers are allowed for legacy clients; this is defense in depth,
    not a replacement for a full CSRF-token implementation.
    """
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return jsonify({'success': False, 'error': 'Cross-site requests are not allowed.'}), 403
    from urllib.parse import urlsplit
    origin = request.headers.get('Origin')
    if origin:
        parsed = urlsplit(origin)
        host = request.host.lower()
        if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() != host:
            return jsonify({'success': False, 'error': 'Invalid request origin.'}), 403
    return None


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
        db.execute('''CREATE TABLE IF NOT EXISTS auth_login_limits (
            key TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0,
            window_start TEXT NOT NULL
        )''')
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
        db.execute('''CREATE TABLE IF NOT EXISTS sales_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL REFERENCES businesses(id),
            invoice_id INTEGER NOT NULL REFERENCES invoices(id),
            request_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            refund_amount NUMERIC NOT NULL DEFAULT 0 CHECK(refund_amount>=0),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(business_id,request_key)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS sales_return_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id INTEGER NOT NULL REFERENCES sales_returns(id),
            invoice_item_id INTEGER NOT NULL REFERENCES invoice_items(id),
            quantity INTEGER NOT NULL CHECK(quantity>0),
            restock INTEGER NOT NULL DEFAULT 0,
            UNIQUE(return_id,invoice_item_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS business_details (
            business_id INTEGER PRIMARY KEY REFERENCES businesses(id),
            contact_email TEXT NOT NULL DEFAULT '',
            contact_phone TEXT NOT NULL DEFAULT '',
            address TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            contact_name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(id,business_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            supplier_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            notes TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ordered_at TIMESTAMP,
            FOREIGN KEY (supplier_id,business_id) REFERENCES suppliers(id,business_id),
            UNIQUE(id,business_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS purchase_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            purchase_order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity_ordered INTEGER NOT NULL CHECK(quantity_ordered BETWEEN 1 AND 100000),
            quantity_received INTEGER NOT NULL DEFAULT 0 CHECK(quantity_received BETWEEN 0 AND quantity_ordered),
            unit_cost NUMERIC NOT NULL CHECK(unit_cost >= 0),
            UNIQUE(purchase_order_id,product_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS purchase_order_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            purchase_order_id INTEGER NOT NULL,
            request_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(business_id,request_key)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS purchase_order_receipt_items (
            receipt_id INTEGER NOT NULL,
            po_item_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity>0),
            PRIMARY KEY(receipt_id,po_item_id)
        )''')
        invoice_columns = {row['name'] for row in db.execute('PRAGMA table_info(invoices)')}
        if 'discount' not in invoice_columns:
            db.execute('ALTER TABLE invoices ADD COLUMN discount NUMERIC NOT NULL DEFAULT 0')
        if 'payment_method' not in invoice_columns:
            db.execute("ALTER TABLE invoices ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'unspecified'")
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
            WHERE DATE(i.created_at) >= ? AND i.status = 'paid' AND i.business_id=?
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
        # Aggregate once per table instead of 14 separate day-level database round trips.
        start_day = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d')
        sales_by_day = {r['day']: r['amount'] for r in db.execute(
            """SELECT DATE(created_at) AS day, COALESCE(SUM(total),0) AS amount
               FROM invoices WHERE business_id=? AND status='paid' AND created_at>=?
               GROUP BY DATE(created_at)""", (business_id, start_day)).fetchall()}
        refunds_by_day = {r['day']: r['amount'] for r in db.execute(
            """SELECT DATE(r.created_at) AS day, COALESCE(SUM(r.amount),0) AS amount
               FROM invoice_refunds r JOIN invoices i ON i.id=r.invoice_id
               WHERE r.business_id=? AND i.status='paid' AND r.created_at>=?
               GROUP BY DATE(r.created_at)""", (business_id, start_day)).fetchall()}
        daily = []
        for offset in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=offset)).strftime('%Y-%m-%d')
            net = Decimal(str(sales_by_day.get(day, 0))) - Decimal(str(refunds_by_day.get(day, 0)))
            daily.append({'date': day, 'revenue': float(net)})
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
        from auth_limits import is_limited, record_failure, clear_failures
        # Use the actual connecting peer; do not trust user-supplied forwarding headers.
        peer = request.remote_addr or 'unknown'
        with get_db() as db:
            limited = is_limited(db, email, peer)
            user = None if limited else db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if limited:
            return render_template('login.html', error='Too many login attempts. Please try again in 15 minutes.'), 429
        if user and verify_password(user['password'], password):
            with get_db() as db:
                clear_failures(db, email, peer)
            if len(user['password']) == 64 and all(c in '0123456789abcdef' for c in user['password']):
                with get_db() as db:
                    db.execute('UPDATE users SET password=? WHERE id=?', (hash_password(password), user['id']))
            session.clear()
            session.permanent = True
            session['user_id']   = user['id']
            session['user_name'] = user['name']
            session['user_email']= user['email']
            return redirect(url_for('dashboard'))
        else:
            with get_db() as db:
                record_failure(db, email, peer)
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
                    session.clear()
                    session.permanent = True
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
    from onboarding import onboarding_status
    with get_db() as db:
        progress = onboarding_status(db,business['id'])
    return render_template('business_profile.html', business=dict(business), onboarding=progress)


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


# ── BUILD 16: MEMBERSHIP PERMISSIONS ────────────────────────────────────────
def current_business_role():
    business_id = current_business_id()
    if business_id is None:
        return None
    with get_db() as db:
        row = db.execute('SELECT role FROM business_memberships WHERE business_id=? AND user_id=?',
                         (business_id, session['user_id'])).fetchone()
    return row['role'] if row else None


@app.before_request
def enforce_business_permissions():
    if 'user_id' not in session:
        return None
    # Enforce read-only membership centrally, including existing JSON APIs.
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and request.endpoint not in (
            'login', 'signup', 'logout', 'account_password'):
        if current_business_role() == 'viewer':
            return jsonify({'success': False, 'error': 'Viewer access is read-only.'}), 403
    return None


@app.route('/business/team')
@login_required
def business_team():
    business_id, error = require_business()
    if error:
        return error
    role = current_business_role()
    with get_db() as db:
        business = db.execute('SELECT name FROM businesses WHERE id=?', (business_id,)).fetchone()
        members = db.execute("""SELECT u.id,u.name,u.email,m.role FROM business_memberships m
            JOIN users u ON u.id=m.user_id WHERE m.business_id=?
            ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'staff' THEN 1 ELSE 2 END,u.name""",
            (business_id,)).fetchall()
    return render_template('business_team.html', business=business, members=members,
                           role=role, current_user_id=session['user_id'])


@app.route('/business/team/members', methods=['POST'])
@login_required
def add_business_member():
    business_id, error = require_business()
    if error:
        return error
    if current_business_role() != 'owner':
        return jsonify({'success': False, 'error': 'Only owners can manage members.'}), 403
    data = request.get_json(silent=True) or {}
    email = data.get('email', '')
    role = data.get('role')
    if not isinstance(email, str) or not 3 <= len(email.strip()) <= 254 or role not in ('staff', 'viewer'):
        return jsonify({'success': False, 'error': 'Enter a registered email and a valid role.'}), 400
    with get_db() as db:
        user = db.execute('SELECT id FROM users WHERE email=?', (email.strip().lower(),)).fetchone()
        if not user:
            return jsonify({'success': False, 'error': 'User must register before being added.'}), 404
        existing = db.execute('SELECT role FROM business_memberships WHERE business_id=? AND user_id=?',
                              (business_id, user['id'])).fetchone()
        if existing:
            return jsonify({'success': False, 'error': 'This user is already a member.'}), 409
        db.execute('INSERT INTO business_memberships (business_id,user_id,role) VALUES (?,?,?)',
                   (business_id, user['id'], role))
    return jsonify({'success': True})


@app.route('/business/team/members/<int:user_id>', methods=['PATCH', 'DELETE'])
@login_required
def change_business_member(user_id):
    business_id, error = require_business()
    if error:
        return error
    if current_business_role() != 'owner':
        return jsonify({'success': False, 'error': 'Only owners can manage members.'}), 403
    data = request.get_json(silent=True) or {}
    new_role = data.get('role')
    if request.method == 'PATCH' and new_role not in ('staff', 'viewer', 'owner'):
        return jsonify({'success': False, 'error': 'Invalid role.'}), 400
    with get_db() as db:
        membership = db.execute('SELECT role FROM business_memberships WHERE business_id=? AND user_id=?',
                                (business_id, user_id)).fetchone()
        if not membership:
            return jsonify({'success': False, 'error': 'Member not found.'}), 404
        if membership['role'] == 'owner' and (request.method == 'DELETE' or new_role != 'owner'):
            count = db.execute("SELECT COUNT(*) FROM business_memberships WHERE business_id=? AND role='owner'",
                               (business_id,)).fetchone()[0]
            if count <= 1:
                return jsonify({'success': False, 'error': 'Cannot remove the last owner.'}), 409
        if request.method == 'DELETE':
            db.execute('DELETE FROM business_memberships WHERE business_id=? AND user_id=?',
                       (business_id, user_id))
        else:
            db.execute('UPDATE business_memberships SET role=? WHERE business_id=? AND user_id=?',
                       (new_role, business_id, user_id))
    return jsonify({'success': True})


@app.route('/account/password', methods=['POST'])
@login_required
def account_password():
    data = request.get_json(silent=True) or {}
    old, new = data.get('current_password'), data.get('new_password')
    if not isinstance(old, str) or not isinstance(new, str) or not 12 <= len(new) <= 256:
        return jsonify({'success': False, 'error': 'New password must be 12–256 characters.'}), 400
    with get_db() as db:
        user = db.execute('SELECT password FROM users WHERE id=?', (session['user_id'],)).fetchone()
        if not user or not verify_password(user['password'], old):
            return jsonify({'success': False, 'error': 'Current password is incorrect.'}), 403
        db.execute('UPDATE users SET password=? WHERE id=?', (hash_password(new), session['user_id']))
    session.clear()
    return jsonify({'success': True, 'message': 'Password changed. Sign in again.'})


# ── MAIN ROUTES (protected) ──────────────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    business_id = current_business_id()
    from onboarding import onboarding_status
    with get_db() as db:
        onboarding = onboarding_status(db,business_id)
    return render_template('dashboard.html', stats=get_dashboard_stats(business_id),
                           onboarding=onboarding, has_business=business_id is not None,
                           sales_7=get_sales_data(7, business_id)[:5], sales_30=get_sales_data(30, business_id)[:5],
                           action_alerts=business_action_alerts(business_id))

def business_action_alerts(business_id):
    """Live, business-scoped operational alerts; no background jobs or stored notifications."""
    if business_id is None:
        return []
    with get_db() as db:
        setting = db.execute('SELECT low_stock_threshold FROM inventory_settings WHERE business_id=?',
                             (business_id,)).fetchone()
        threshold = int(setting['low_stock_threshold']) if setting else 20
        out_count = db.execute('SELECT COUNT(*) FROM products WHERE business_id=? AND stock=0',
                               (business_id,)).fetchone()[0]
        low_count = db.execute('SELECT COUNT(*) FROM products WHERE business_id=? AND stock>0 AND stock<=?',
                               (business_id, threshold)).fetchone()[0]
        out_preview = db.execute('SELECT name FROM products WHERE business_id=? AND stock=0 ORDER BY name,id LIMIT 3',
                                 (business_id,)).fetchall()
        low_preview = db.execute('SELECT name FROM products WHERE business_id=? AND stock>0 AND stock<=? ORDER BY name,id LIMIT 3',
                                 (business_id, threshold)).fetchall()
        pending = db.execute("SELECT COUNT(*) AS count FROM invoices WHERE business_id=? AND status='pending'",
                             (business_id,)).fetchone()['count']
        orders = db.execute("""SELECT COUNT(*) AS count FROM purchase_orders
            WHERE business_id=? AND status IN ('ordered','partially_received')""",
                            (business_id,)).fetchone()['count']
    alerts = []
    if out_count:
        alerts.append({'kind': 'out_of_stock', 'severity': 'critical', 'count': out_count,
                       'title': 'Products out of stock', 'detail': ', '.join(p['name'] for p in out_preview),
                       'action': 'Plan replenishment', 'url': '/inventory/replenishment/plan'})
    if low_count:
        alerts.append({'kind': 'low_stock', 'severity': 'warning', 'count': low_count,
                       'title': 'Products running low', 'detail': ', '.join(p['name'] for p in low_preview),
                       'action': 'Review inventory', 'url': '/products'})
    if pending:
        alerts.append({'kind': 'pending_invoices', 'severity': 'info', 'count': int(pending),
                       'title': 'Pending invoices', 'detail': 'Invoices awaiting payment or review',
                       'action': 'Review invoices', 'url': '/invoices'})
    if orders:
        alerts.append({'kind': 'incoming_orders', 'severity': 'info', 'count': int(orders),
                       'title': 'Purchase orders awaiting receipt', 'detail': 'Ordered or partially received stock',
                       'action': 'Review purchase orders', 'url': '/purchase-orders'})
    return alerts


@app.route('/notifications')
@login_required
def business_notifications():
    business_id, error = require_business()
    if error:
        return error
    alerts = business_action_alerts(business_id)
    response = jsonify({'success': True, 'count': len(alerts), 'alerts': alerts})
    response.headers['Cache-Control'] = 'no-store'
    return response


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
    last_day = today
    custom_start = request.args.get('start', '')
    custom_end = request.args.get('end', '')
    if custom_start or custom_end:
        try:
            if not custom_start or not custom_end:
                raise ValueError('Both dates required')
            first_day = date.fromisoformat(custom_start)
            last_day = date.fromisoformat(custom_end)
            if first_day > last_day or last_day > today or (last_day - first_day).days >= 366:
                raise ValueError('Invalid date range')
            days = (last_day - first_day).days + 1
        except ValueError:
            return jsonify({'success': False, 'error': 'Select a valid date range of up to 366 days ending today or earlier.'}), 400
    previous_start = first_day - timedelta(days=days)
    next_day = last_day + timedelta(days=1)
    with get_db() as db:
        invoices = db.execute(
            'SELECT id, created_at, total, status, customer_name FROM invoices WHERE business_id=? AND created_at>=? ORDER BY created_at',
            (business_id, previous_start.isoformat())
        ).fetchall()
        product_rows = db.execute('''
            SELECT ii.product_name, ii.quantity, ii.total, i.created_at, i.status
            FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id
            WHERE i.business_id=? AND i.created_at>=?
        ''', (business_id, first_day.isoformat())).fetchall()
    daily = {str(first_day + timedelta(days=n)): Decimal('0') for n in range(days)}
    previous_paid = Decimal('0')
    previous_count = 0
    for inv in invoices:
        day = str(inv['created_at'])[:10]
        if previous_start.isoformat() <= day < first_day.isoformat() and inv['status'] == 'paid':
            previous_paid += Decimal(str(inv['total']))
            previous_count += 1
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
            (business_id, previous_start.isoformat())).fetchall()
    refund_total = Decimal('0')
    for refund in refund_rows:
        day = str(refund['created_at'])[:10]
        amount = Decimal(str(refund['amount']))
        if day in daily:
            daily[day] -= amount
            refund_total += amount
        elif previous_start.isoformat() <= day < first_day.isoformat():
            previous_paid -= amount
    paid_revenue -= refund_total
    revenue_change = ((paid_revenue - previous_paid) / abs(previous_paid) * 100
                      if previous_paid else None)
    products = {}
    for item in product_rows:
        if item['status'] != 'paid' or str(item['created_at'])[:10] not in daily:
            continue
        name = item['product_name']
        entry = products.setdefault(name, {'name': name, 'units': 0, 'revenue': Decimal('0')})
        entry['units'] += int(item['quantity'])
        entry['revenue'] += Decimal(str(item['total']))
    top_products = sorted(products.values(), key=lambda p: (-p['units'], p['name']))[:8]
    with get_db() as db:
        stock_rows = db.execute('SELECT id, name, stock, price FROM products WHERE business_id=?',
                                (business_id,)).fetchall()
        setting = db.execute('SELECT low_stock_threshold FROM inventory_settings WHERE business_id=?',
                             (business_id,)).fetchone()
    threshold = int(setting['low_stock_threshold']) if setting else 20
    inventory_value = sum((Decimal(str(p['price'])) * int(p['stock']) for p in stock_rows), Decimal('0'))
    out_of_stock_count = sum(int(p['stock']) == 0 for p in stock_rows)
    low_stock_count = sum(0 < int(p['stock']) <= threshold for p in stock_rows)
    sales_units = {p['name']: p['units'] for p in products.values()}
    slow_moving = sorted(
        ({'name': p['name'], 'stock': int(p['stock']), 'units': sales_units.get(p['name'], 0)}
         for p in stock_rows if int(p['stock']) > 0),
        key=lambda p: (p['units'], -p['stock'], p['name'])
    )[:5]
    chart = [{'date': day, 'revenue': float(amount)} for day, amount in daily.items()]
    return render_template('analytics.html', days=days, chart=chart,
                           paid_revenue=paid_revenue, refund_total=refund_total, pending_amount=pending_amount,
                           paid_count=paid_count, pending_count=pending_count,
                           unique_customers=len(customers), top_products=top_products,
                           avg_order=(paid_revenue / paid_count if paid_count else Decimal('0')),
                           inventory_value=inventory_value, out_of_stock_count=out_of_stock_count,
                           low_stock_count=low_stock_count, slow_moving=slow_moving,
                           inventory_threshold=threshold, previous_paid=previous_paid,
                           previous_count=previous_count, revenue_change=revenue_change,
                           report_start=first_day.isoformat(), report_end=last_day.isoformat(),
                           is_custom=bool(custom_start or custom_end))



@app.route('/reports/export')
@login_required
def export_business_report():
    """Build 12: tenant-scoped, spreadsheet-friendly CSV exports."""
    import csv
    from datetime import date
    from flask import Response

    business_id, error = require_business()
    if error:
        return error
    kind = request.args.get('type', 'sales')
    if kind not in ('sales', 'inventory', 'refunds'):
        return jsonify({'success': False, 'error': 'Invalid report type'}), 400

    start = request.args.get('start', '')
    end = request.args.get('end', '')
    try:
        if start:
            start = date.fromisoformat(start).isoformat()
        if end:
            end = date.fromisoformat(end).isoformat()
    except ValueError:
        return jsonify({'success': False, 'error': 'Dates must be YYYY-MM-DD'}), 400
    if start and end and start > end:
        return jsonify({'success': False, 'error': 'Start date must not exceed end date'}), 400

    if kind == 'sales':
        headings = ['Invoice ID', 'Date', 'Customer', 'Status', 'Gross Total', 'Refunded', 'Net Total']
        query = """SELECT i.id, i.created_at, i.customer_name, i.status, i.total,
                    COALESCE((SELECT SUM(r.amount) FROM invoice_refunds r
                      WHERE r.invoice_id=i.id AND r.business_id=i.business_id),0) AS refunded
                    FROM invoices i WHERE i.business_id=?"""
        params = [business_id]
        if start:
            query += ' AND i.created_at>=?'
            params.append(start)
        if end:
            query += ' AND i.created_at<?'
            params.append((date.fromisoformat(end) + timedelta(days=1)).isoformat())
        query += ' ORDER BY i.created_at DESC, i.id DESC'
        with get_db() as db:
            rows = db.execute(query, tuple(params)).fetchall()
        records = []
        for r in rows:
            gross = Decimal(str(r['total']))
            refunded = Decimal(str(r['refunded']))
            records.append([r['id'], str(r['created_at']), r['customer_name'],
                            r['status'], str(gross), str(refunded), str(gross-refunded)])
    elif kind == 'refunds':
        headings = ['Refund ID', 'Invoice ID', 'Date', 'Amount', 'Reason']
        query = """SELECT r.id, r.invoice_id, r.created_at, r.amount, r.reason
                    FROM invoice_refunds r JOIN invoices i ON i.id=r.invoice_id
                    WHERE r.business_id=? AND i.business_id=?"""
        params = [business_id, business_id]
        if start:
            query += ' AND r.created_at>=?'
            params.append(start)
        if end:
            query += ' AND r.created_at<?'
            params.append((date.fromisoformat(end) + timedelta(days=1)).isoformat())
        query += ' ORDER BY r.created_at DESC, r.id DESC'
        with get_db() as db:
            rows = db.execute(query, tuple(params)).fetchall()
        records = [[r['id'], r['invoice_id'], str(r['created_at']), str(r['amount']), r['reason']] for r in rows]
    else:
        headings = ['Product ID', 'SKU', 'Product', 'Category', 'Unit Price', 'Stock', 'Inventory Value']
        with get_db() as db:
            rows = db.execute('SELECT id, sku, name, category, price, stock FROM products WHERE business_id=? ORDER BY name, id',
                              (business_id,)).fetchall()
        records = [[r['id'], r['sku'], r['name'], r['category'], str(r['price']), r['stock'],
                    str(Decimal(str(r['price']))*int(r['stock']))] for r in rows]

    def safe_cell(value):
        # Prevent spreadsheet programs from evaluating user-controlled CSV cells as formulas.
        value = '' if value is None else str(value)
        if value.lstrip().startswith(('=', '+', '-', '@', '\\t', '\\r', '\\n')):
            return "'" + value
        return value

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headings)
    writer.writerows([[safe_cell(cell) for cell in row] for row in records])
    filename = 'fermi-' + kind + '-report.csv'
    response = Response(output.getvalue(), mimetype='text/csv; charset=utf-8')
    response.headers['Content-Disposition'] = 'attachment; filename="' + filename + '"'
    response.headers['Cache-Control'] = 'no-store'
    return response


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


@app.route('/inventory/replenishment/plan')
@login_required
def replenishment_plan_page():
    """Interactive reorder planning; draft orders require explicit confirmation."""
    business_id, error = require_business()
    if error:
        return error
    with get_db() as db:
        suppliers = db.execute('SELECT id,name FROM suppliers WHERE business_id=? ORDER BY name,id',
                               (business_id,)).fetchall()
    return render_template('replenishment.html', suppliers=suppliers)


@app.route('/inventory/replenishment')
@login_required
def inventory_replenishment():
    """Read-only, business-scoped replenishment suggestions (Build 13)."""
    from datetime import date, timedelta
    business_id, error = require_business()
    if error:
        return error
    days_raw = request.args.get('days', '30')
    if days_raw not in ('7', '30', '90'):
        return jsonify({'success': False, 'error': 'Days must be 7, 30 or 90'}), 400
    days = int(days_raw)
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with get_db() as db:
        products = db.execute(
            'SELECT id, name, sku, stock, price FROM products WHERE business_id=? ORDER BY name, id',
            (business_id,)).fetchall()
        setting = db.execute('SELECT low_stock_threshold FROM inventory_settings WHERE business_id=?',
                             (business_id,)).fetchone()
        sales = db.execute("""SELECT ii.product_id, SUM(ii.quantity) AS units
            FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id
            WHERE i.business_id=? AND i.status='paid' AND i.created_at>=?
            AND ii.product_id IS NOT NULL
            GROUP BY ii.product_id""", (business_id, start)).fetchall()
    threshold = int(setting['low_stock_threshold']) if setting else 20
    sold = {int(r['product_id']): int(r['units']) for r in sales}
    recommendations = []
    for p in products:
        stock = int(p['stock'])
        units = sold.get(int(p['id']), 0)
        daily_rate = units / days
        coverage = round(stock / daily_rate, 1) if daily_rate else None
        # A minimum stock floor plus 14 days of observed demand.
        target = max(threshold + 1, (units * 14 + days - 1) // days)
        suggested = max(0, target - stock)
        if suggested == 0:
            continue
        priority = 'out_of_stock' if stock == 0 else ('low_stock' if stock <= threshold else 'demand_risk')
        recommendations.append({
            'product_id': p['id'], 'name': p['name'], 'sku': p['sku'],
            'stock': stock, 'units_sold': units, 'daily_units': round(daily_rate, 2),
            'estimated_coverage_days': coverage, 'target_stock': target,
            'suggested_order_quantity': suggested, 'priority': priority
        })
    order = {'out_of_stock': 0, 'low_stock': 1, 'demand_risk': 2}
    recommendations.sort(key=lambda x: (order[x['priority']], -x['suggested_order_quantity'], x['name']))
    response = jsonify({'success': True, 'days': days, 'threshold': threshold,
                        'planning_horizon_days': 14, 'recommendations': recommendations,
                        'note': 'Estimates use paid invoice units, not returns, lead times or seasonality. No purchase orders are created.'})
    response.headers['Cache-Control'] = 'no-store'
    return response


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
            discount_raw = d.get('discount_amount', '0')
            if isinstance(discount_raw, bool) or not isinstance(discount_raw, (int, float, str)):
                return jsonify({'success': False, 'error': 'Invalid discount amount'}), 400
            try:
                discount = Decimal(str(discount_raw))
            except InvalidOperation:
                return jsonify({'success': False, 'error': 'Invalid discount amount'}), 400
            if (not discount.is_finite() or discount < 0 or discount > subtotal
                    or discount.as_tuple().exponent < -2):
                return jsonify({'success': False, 'error': 'Discount must be between zero and subtotal, with at most two decimals'}), 400
            payment_method = d.get('payment_method', 'unspecified')
            if payment_method not in ('unspecified', 'cash', 'card', 'mobile_banking', 'bank_transfer', 'other'):
                return jsonify({'success': False, 'error': 'Invalid payment method'}), 400
            tax = ((subtotal - discount) * Decimal('0.05')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            total = subtotal - discount + tax
            status = d.get('status', 'paid')
            if status not in ('paid', 'pending'):
                return jsonify({'success': False, 'error': 'Invalid invoice status'}), 400
            notes = d.get('notes', '')
            if not isinstance(notes, str) or len(notes) > 5000:
                return jsonify({'success': False, 'error': 'Invalid invoice notes'}), 400
            if subtotal > Decimal('100000000000'):
                return jsonify({'success': False, 'error': 'Invoice total exceeds limit'}), 400
            invoice_number = next_invoice_number(business_id)
            db.execute('INSERT INTO invoices (invoice_number, customer_name, customer_email, customer_phone, subtotal, tax, total, status, notes, business_id, discount, payment_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (invoice_number, customer_name, customer_email,
                        customer_phone, float(subtotal), float(tax), float(total),
                        status, notes, business_id, float(discount), payment_method))
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
    invoice_data = dict(inv)
    invoice_data['total'] = float(invoice_data['total'])
    with get_db() as db:
        return_rows = db.execute("""SELECT ri.invoice_item_id,COALESCE(SUM(ri.quantity),0) AS returned
            FROM sales_return_items ri JOIN sales_returns r ON r.id=ri.return_id
            WHERE r.invoice_id=? AND r.business_id=? GROUP BY ri.invoice_item_id""",
            (inv_id,current_business_id())).fetchall()
    returned_by_item = {r['invoice_item_id']:r['returned'] for r in return_rows}
    invoice_items = [dict(i) for i in items]
    for item in invoice_items:
        item['returnable'] = max(0,item['quantity']-int(returned_by_item.get(item['id'],0)))
    return render_template('view_invoice.html', invoice=invoice_data, items=invoice_items, refunds=[dict(r) for r in refunds], refunded=float(sum(Decimal(str(r['amount'])) for r in refunds)))

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
    total_rows = [[p('Subtotal', total_label), p('BDT ' + money(inv['subtotal']), total_number)]]
    if inv.get('discount'):
        total_rows.append([p('Discount', total_label), p('-BDT ' + money(inv['discount']), total_number)])
    total_rows.extend([
        [p('Tax (5%) after discount', total_label), p('BDT ' + money(inv['tax']), total_number)],
        [p('Total (BDT)', grand_label), p('BDT ' + money(inv['total']), grand_number)]
    ])
    totals = Table(total_rows, colWidths=[width * .70, width * .30])
    last_row = len(total_rows) - 1
    totals.setStyle(TableStyle([
        ('BACKGROUND', (0, last_row), (-1, last_row), sky),
        ('LINEBELOW', (0, 0), (-1, last_row - 1), .5, border),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(p('Payment method: ' + str(inv.get('payment_method', 'unspecified')).replace('_', ' ').title(),
                   style('payment-method', 9, muted)))
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
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('message'), str) or not 1 <= len(data['message'].strip()) <= 4000:
        return jsonify({'success': False, 'error': 'Message must be between 1 and 4,000 characters.'}), 400
    user_message = data['message'].strip()
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
    except Exception:
        app.logger.exception('AI assistant request failed')
        return jsonify({'reply': 'The assistant is temporarily unavailable. Please try again.', 'success': False}), 503

from purchase_orders import po_blueprint
app.register_blueprint(po_blueprint)

# Register all routes before starting the local development server.
from sales_returns import returns_blueprint
app.register_blueprint(returns_blueprint)

from onboarding import onboarding_blueprint
app.register_blueprint(onboarding_blueprint)

if __name__ == '__main__':
    init_db()
    app.run(debug=os.environ.get('FERMI_DEBUG') == '1', port=int(os.environ.get('PORT', 5000)))
