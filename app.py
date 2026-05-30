from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
import sqlite3, json, os
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import anthropic
import io

app = Flask(__name__)

DATABASE = 'fermi.db'
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', 'YOUR_API_KEY_HERE')

@app.context_processor
def inject_now():
    return {'now': datetime.now().strftime('%b %d, %Y')}

# Run on startup — works with both gunicorn and python app.py
with app.app_context():
    pass

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS products (
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

# Initialize database immediately — runs under both gunicorn and python app.py
init_db()

def next_invoice_number():
    with get_db() as db:
        last = db.execute("SELECT invoice_number FROM invoices ORDER BY id DESC LIMIT 1").fetchone()
        if last:
            try: num = int(last['invoice_number'].split('-')[1]) + 1
            except: num = 1001
        else: num = 1001
        return f"FRM-{num}"

def get_sales_data(days=30):
    with get_db() as db:
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        items = db.execute('''
            SELECT ii.product_name, SUM(ii.quantity) as qty, SUM(ii.total) as revenue
            FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
            WHERE DATE(i.created_at) >= ? AND i.status != 'cancelled'
            GROUP BY ii.product_name ORDER BY qty DESC
        ''', (since,)).fetchall()
        return [dict(r) for r in items]

def get_dashboard_stats():
    with get_db() as db:
        month_start   = datetime.now().replace(day=1).strftime('%Y-%m-%d')
        total_revenue = db.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid'").fetchone()[0]
        month_revenue = db.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid' AND DATE(created_at)>=?", (month_start,)).fetchone()[0]
        total_invoices= db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        low_stock     = db.execute("SELECT COUNT(*) FROM products WHERE stock < 20").fetchone()[0]
        total_products= db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        daily = []
        for i in range(6, -1, -1):
            d   = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            rev = db.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE DATE(created_at)=? AND status='paid'", (d,)).fetchone()[0]
            daily.append({'date': d, 'revenue': round(rev, 2)})
        return {'total_revenue': round(total_revenue,2), 'month_revenue': round(month_revenue,2),
                'total_invoices': total_invoices, 'low_stock': low_stock,
                'total_products': total_products, 'daily': daily}

@app.route('/')
def dashboard():
    return render_template('dashboard.html', stats=get_dashboard_stats(),
                           sales_7=get_sales_data(7)[:5], sales_30=get_sales_data(30)[:5])

@app.route('/products')
def products():
    with get_db() as db:
        prods = db.execute('SELECT * FROM products ORDER BY name').fetchall()
    return render_template('products.html', products=[dict(p) for p in prods])

@app.route('/products/add', methods=['POST'])
def add_product():
    d = request.json
    try:
        with get_db() as db:
            db.execute('INSERT INTO products (name, sku, category, price, stock) VALUES (?,?,?,?,?)',
                       (d['name'], d['sku'], d['category'], float(d['price']), int(d['stock'])))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/products/edit/<int:pid>', methods=['POST'])
def edit_product(pid):
    d = request.json
    try:
        with get_db() as db:
            db.execute('UPDATE products SET name=?, sku=?, category=?, price=?, stock=? WHERE id=?',
                       (d['name'], d['sku'], d['category'], float(d['price']), int(d['stock']), pid))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/products/delete/<int:pid>', methods=['POST'])
def delete_product(pid):
    with get_db() as db:
        db.execute('DELETE FROM products WHERE id=?', (pid,))
    return jsonify({'success': True})

@app.route('/api/products')
def api_products():
    with get_db() as db:
        prods = db.execute('SELECT * FROM products ORDER BY name').fetchall()
    return jsonify([dict(p) for p in prods])

@app.route('/invoices')
def invoices():
    with get_db() as db:
        invs = db.execute('SELECT * FROM invoices ORDER BY created_at DESC').fetchall()
    return render_template('invoices.html', invoices=[dict(i) for i in invs])

@app.route('/invoices/new')
def new_invoice():
    with get_db() as db:
        prods = db.execute('SELECT * FROM products ORDER BY name').fetchall()
    return render_template('new_invoice.html', invoice_number=next_invoice_number(), products=[dict(p) for p in prods])

@app.route('/invoices/create', methods=['POST'])
def create_invoice():
    d = request.json
    try:
        with get_db() as db:
            db.execute('INSERT INTO invoices (invoice_number, customer_name, customer_email, customer_phone, subtotal, tax, total, status, notes) VALUES (?,?,?,?,?,?,?,?,?)',
                       (d['invoice_number'], d['customer_name'], d.get('customer_email',''),
                        d.get('customer_phone',''), d['subtotal'], d['tax'], d['total'],
                        d.get('status','paid'), d.get('notes','')))
            inv_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            for item in d['items']:
                db.execute('INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price, total) VALUES (?,?,?,?,?,?)',
                           (inv_id, item.get('product_id'), item['product_name'],
                            item['quantity'], item['unit_price'], item['total']))
                if item.get('product_id'):
                    db.execute('UPDATE products SET stock = stock - ? WHERE id=?',
                               (item['quantity'], item['product_id']))
        return jsonify({'success': True, 'invoice_id': inv_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/invoices/<int:inv_id>')
def view_invoice(inv_id):
    with get_db() as db:
        inv   = db.execute('SELECT * FROM invoices WHERE id=?', (inv_id,)).fetchone()
        items = db.execute('SELECT * FROM invoice_items WHERE invoice_id=?', (inv_id,)).fetchall()
    if not inv: return redirect(url_for('invoices'))
    return render_template('view_invoice.html', invoice=dict(inv), items=[dict(i) for i in items])

@app.route('/invoices/<int:inv_id>/pdf')
def download_pdf(inv_id):
    with get_db() as db:
        inv   = dict(db.execute('SELECT * FROM invoices WHERE id=?', (inv_id,)).fetchone())
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
def chatbot():
    return render_template('chatbot.html')

@app.route('/api/chat', methods=['POST'])
def api_chat():
    user_message = request.json.get('message', '')
    stats    = get_dashboard_stats()
    sales_7  = get_sales_data(7)
    sales_30 = get_sales_data(30)
    with get_db() as db:
        all_products    = [dict(p) for p in db.execute('SELECT * FROM products ORDER BY stock ASC').fetchall()]
        recent_invoices = [dict(i) for i in db.execute('SELECT * FROM invoices ORDER BY created_at DESC LIMIT 10').fetchall()]
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
        return jsonify({'reply': f'Error: {str(e)}. Please set your ANTHROPIC_API_KEY.', 'success': False})

if __name__ == '__main__':
    init_db()
    print("\n" + "="*50)
    print("🚀  Fermi is running!")
    print("    Open: http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, port=5000)
