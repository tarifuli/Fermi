from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, session
import sqlite3, json, os, hashlib, secrets
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import anthropic
import io

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

DATABASE = 'fermi.db'
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', 'YOUR_API_KEY_HERE')

@app.context_processor
def inject_now():
    return {'now': datetime.now().strftime('%b %d, %Y')}

with app.app_context():
    pass

# ── AUTH HELPERS ─────────────────────────────────────────────────────────────
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

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
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
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
        for table in ('products', 'invoices'):
            columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
            if 'business_id' not in columns:
                db.execute(f'ALTER TABLE {table} ADD COLUMN business_id INTEGER REFERENCES businesses(id)')
            db.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_business ON {table}(business_id)')
        count = db.execute('SELECT COUNT(*) FROM products').fetchone()[0]
        if count == 0:
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

init_db()

# ── HELPERS ──────────────────────────────────────────────────────────────────
def next_invoice_number(business_id=None):
    with get_db() as db:
        last = db.execute("SELECT invoice_number FROM invoices WHERE business_id=? ORDER BY id DESC LIMIT 1", (business_id,)).fetchone()
        if last:
            try: num = int(last['invoice_number'].split('-')[1]) + 1
            except: num = 1001
        else: num = 1001
        return f"FRM-{num}"

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
        month_start   = datetime.now().replace(day=1).strftime('%Y-%m-%d')
        total_revenue = db.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid' AND business_id=?", (business_id,)).fetchone()[0]
        month_revenue = db.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid' AND DATE(created_at)>=? AND business_id=?", (month_start, business_id)).fetchone()[0]
        total_invoices= db.execute("SELECT COUNT(*) FROM invoices WHERE business_id=?", (business_id,)).fetchone()[0]
        low_stock     = db.execute("SELECT COUNT(*) FROM products WHERE stock < 20 AND business_id=?", (business_id,)).fetchone()[0]
        total_products= db.execute("SELECT COUNT(*) FROM products WHERE business_id=?", (business_id,)).fetchone()[0]
        daily = []
        for i in range(6, -1, -1):
            d   = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            rev = db.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE DATE(created_at)=? AND status='paid' AND business_id=?", (d, business_id)).fetchone()[0]
            daily.append({'date': d, 'revenue': round(rev, 2)})
        return {'total_revenue': round(total_revenue,2), 'month_revenue': round(month_revenue,2),
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
        if user and user['password'] == hash_password(password):
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
                cur = db.execute('INSERT INTO businesses (name) VALUES (?)', (name,))
                db.execute(
                    'INSERT INTO business_memberships (business_id,user_id,role) VALUES (?,?,?)',
                    (cur.lastrowid, session['user_id'], 'owner')
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
                           sales_7=get_sales_data(7, business_id)[:5], sales_30=get_sales_data(30, business_id)[:5])

@app.route('/products')
@login_required
def products():
    with get_db() as db:
        prods = db.execute('SELECT * FROM products WHERE business_id=? ORDER BY name', (current_business_id(),)).fetchall()
    return render_template('products.html', products=[dict(p) for p in prods])

@app.route('/products/add', methods=['POST'])
@login_required
def add_product():
    d = request.json
    business_id, error = require_business()
    if error:
        return error
    try:
        with get_db() as db:
            db.execute('INSERT INTO products (name, sku, category, price, stock, business_id) VALUES (?,?,?,?,?,?)',
                       (d['name'], d['sku'], d['category'], float(d['price']), int(d['stock']), business_id))
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
            db.execute('UPDATE products SET name=?, sku=?, category=?, price=?, stock=? WHERE id=? AND business_id=?',
                       (d['name'], d['sku'], d['category'], float(d['price']), int(d['stock']), pid, business_id))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

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

@app.route('/invoices')
@login_required
def invoices():
    with get_db() as db:
        invs = db.execute('SELECT * FROM invoices WHERE business_id=? ORDER BY created_at DESC', (current_business_id(),)).fetchall()
    return render_template('invoices.html', invoices=[dict(i) for i in invs])

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
        if any(not isinstance(it, dict) or type(it.get('quantity')) is not int or it['quantity'] <= 0
               or type(it.get('product_id')) is not int for it in items):
            return jsonify({'success': False, 'error': 'Valid product and positive quantity required'}), 400
        from decimal import Decimal, ROUND_HALF_UP
        with get_db() as db:
            line_items = []
            subtotal = Decimal('0.00')
            for item in items:
                product = db.execute('SELECT id, name, price, stock FROM products WHERE id=? AND business_id=?',
                                     (item['product_id'], business_id)).fetchone()
                if product is None or product['stock'] < item['quantity']:
                    return jsonify({'success': False, 'error': 'Product unavailable or insufficient stock'}), 400
                price = Decimal(str(product['price']))
                if not price.is_finite() or price < 0:
                    return jsonify({'success': False, 'error': 'Invalid product price'}), 400
                line_total = (price * item['quantity']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                subtotal += line_total
                line_items.append((product, item['quantity'], price, line_total))
            subtotal = subtotal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            tax = (subtotal * Decimal('0.05')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            total = subtotal + tax
            invoice_number = next_invoice_number(business_id)
            db.execute('INSERT INTO invoices (invoice_number, customer_name, customer_email, customer_phone, subtotal, tax, total, status, notes, business_id) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (invoice_number, d['customer_name'].strip(), d.get('customer_email',''),
                        d.get('customer_phone',''), float(subtotal), float(tax), float(total),
                        'paid', d.get('notes',''), business_id))
            inv_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            for product, quantity, price, line_total in line_items:
                updated = db.execute('UPDATE products SET stock=stock-? WHERE id=? AND business_id=? AND stock>=?',
                                     (quantity, product['id'], business_id, quantity))
                if updated.rowcount != 1:
                    raise ValueError('Product unavailable or insufficient stock')
                db.execute('INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price, total) VALUES (?,?,?,?,?,?)',
                           (inv_id, product['id'], product['name'], quantity, float(price), float(line_total)))
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
    if not inv: return redirect(url_for('invoices'))
    return render_template('view_invoice.html', invoice=dict(inv), items=[dict(i) for i in items])

@app.route('/invoices/<int:inv_id>/pdf')
@login_required
def download_pdf(inv_id):
    with get_db() as db:
        row = db.execute('SELECT * FROM invoices WHERE id=? AND business_id=?', (inv_id, current_business_id())).fetchone()
        if row is None:
            return 'Invoice not found', 404
        inv = dict(row)
        items = [dict(i) for i in db.execute('SELECT * FROM invoice_items WHERE invoice_id=?', (inv_id,)).fetchall()]
    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    story  = []
    ts = ParagraphStyle('t', fontSize=24, fontName='Helvetica-Bold', textColor=colors.HexColor('#1A3A5C'), spaceAfter=4)
    ss = ParagraphStyle('s', fontSize=10, fontName='Helvetica', textColor=colors.HexColor('#555'), spaceAfter=2)
    ls = ParagraphStyle('l', fontSize=9,  fontName='Helvetica-Bold', textColor=colors.HexColor('#1A3A5C'))
    vs = ParagraphStyle('v', fontSize=9,  fontName='Helvetica', textColor=colors.HexColor('#333'))
    story.append(Paragraph("FERMI", ts))
    story.append(Paragraph("AI-Powered SME Intelligence Platform — Dhaka, Bangladesh", ss))
    story.append(Spacer(1, 0.2*inch))
    story.append(Table([[Paragraph('INVOICE', ParagraphStyle('h',fontSize=16,fontName='Helvetica-Bold',textColor=colors.HexColor('#2E6DA4'))),'',Paragraph(f'Invoice #: {inv["invoice_number"]}',ls),''],[  '','',Paragraph(f'Date: {inv["created_at"][:10]}',vs),''],[  '','',Paragraph(f'Status: {inv["status"].upper()}',vs),'']],colWidths=[2.5*inch,1.5*inch,2*inch,1.5*inch]))
    story.append(Spacer(1,0.15*inch))
    bt = Table([[Paragraph('BILL TO:',ls)],[Paragraph(inv['customer_name'],ParagraphStyle('cn',fontSize=11,fontName='Helvetica-Bold'))],[Paragraph(inv.get('customer_email') or '',vs)],[Paragraph(inv.get('customer_phone') or '',vs)]],colWidths=[7.5*inch])
    bt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#D6E8F7')),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4),('LEFTPADDING',(0,0),(-1,-1),8)]))
    story.append(bt); story.append(Spacer(1,0.15*inch))
    idata = [['#','Product','Qty','Unit Price (BDT)','Total (BDT)']]+[[str(n+1),it['product_name'],str(it['quantity']),f"{it['unit_price']:,.2f}",f"{it['total']:,.2f}"] for n,it in enumerate(items)]
    it2 = Table(idata, colWidths=[0.4*inch,3.2*inch,0.8*inch,1.6*inch,1.5*inch])
    it2.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1A3A5C')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),('ALIGN',(2,0),(-1,-1),'RIGHT'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F5F5F3')]),('GRID',(0,0),(-1,-1),0.5,colors.HexColor('#CCC')),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LEFTPADDING',(0,0),(-1,-1),8)]))
    story.append(it2); story.append(Spacer(1,0.15*inch))
    tot = Table([['','Subtotal:',f"BDT {inv['subtotal']:,.2f}"],['','Tax (5%):',f"BDT {inv['tax']:,.2f}"],['','TOTAL:',f"BDT {inv['total']:,.2f}"]],colWidths=[4.5*inch,1.5*inch,1.5*inch])
    tot.setStyle(TableStyle([('ALIGN',(1,0),(-1,-1),'RIGHT'),('FONTNAME',(1,2),(-1,2),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),('BACKGROUND',(0,2),(-1,2),colors.HexColor('#D6E8F7')),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
    story.append(tot)
    if inv.get('notes'):
        story.append(Spacer(1,0.2*inch)); story.append(Paragraph(f"Notes: {inv['notes']}",vs))
    story.append(Spacer(1,0.3*inch)); story.append(Paragraph("Thank you for your business. — Fermi Platform",ss))
    doc.build(story); buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"{inv['invoice_number']}.pdf", mimetype='application/pdf')

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
