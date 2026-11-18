"""Private local insights and a closed, public-topic Gemini gateway.

No user text, database values, identifiers, cookies, or chat history may enter
the Gemini request. General prompts are compiled from this constant allowlist.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

TOPICS = {
    'marketing': ('Marketing basics', 'Explain five low-cost marketing practices for a small business.'),
    'pricing': ('Pricing basics', 'Explain cost-plus pricing, margin and markup with fictional examples.'),
    'inventory': ('Inventory planning', 'Explain reorder points and safety stock with a fictional example.'),
    'cashflow': ('Cash-flow planning', 'Explain how a small business can plan cash flow and manage receivables.'),
    'service': ('Customer service', 'Suggest general practices for customer service and complaint handling.'),
    'security': ('Business security', 'Explain basic cybersecurity practices for small businesses.'),
}
_lock = threading.Lock()
_cache = {}
_last_attempt = 0.0


class AdviceUnavailable(Exception):
    pass


def public_advice(topic, language='en'):
    """Only allowlisted literal content crosses the Google boundary."""
    if not isinstance(topic, str) or topic not in TOPICS or language not in ('en', 'bn'):
        raise ValueError('Choose an available topic and language.')
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        raise AdviceUnavailable('General advice is not connected yet. Private insights remain available.')
    # Pin model: no user-controlled URL, model, tool, or remote retrieval.
    cache_key = (topic, language)
    global _last_attempt
    with _lock:
        now = time.monotonic()
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < 900:
            return cached[1]
        if now - _last_attempt < 5:
            raise AdviceUnavailable('Please wait a few seconds before requesting another topic.')
        _last_attempt = now
        prompt = (TOPICS[topic][1] + ' Answer in ' +
                  ('Bengali.' if language == 'bn' else 'English.') +
                  ' Use plain text, under 350 words. This is general education, not personalized advice. '
                  'Do not claim access to private business records or live web information. '
                  'Do not invent current prices, laws, statistics or sources.')
        payload = {
            'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
            'generationConfig': {'maxOutputTokens': 1200,
                                 'thinkingConfig': {'thinkingBudget': 0}},
        }
        req = urllib.request.Request(
            'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'x-goog-api-key': key},
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=18) as response:
                result = json.loads(response.read(131072))
            candidates = result.get('candidates', [])
            candidate = candidates[0] if candidates else {}
            if candidate.get('finishReason') not in ('STOP', 'MAX_TOKENS'):
                raise ValueError('No usable answer')
            answer = '\n'.join(p['text'] for p in candidate.get('content', {}).get('parts', [])
                               if isinstance(p.get('text'), str) and not p.get('thought'))
            if not answer.strip():
                raise ValueError('Empty answer')
        except Exception:
            # Never log provider exceptions, prompts, keys, or response bodies.
            raise AdviceUnavailable('General advice is temporarily unavailable or its quota is exhausted. Try later.') from None
        answer = answer[:10000]
        _cache[cache_key] = (time.monotonic(), answer)
        return answer


def local_insight(message, business_id, get_db, dashboard_stats, sales_data):
    """Deterministic, tenant-scoped reporting. No model calls or external I/O."""
    text = message.casefold()
    if any(w in text for w in ('profit', 'profitable', 'লাভ')):
        return 'Profit cannot be calculated reliably from sales alone. Fermi needs complete cost and expense data for a profit report.'
    if any(w in text for w in ('customer', 'customers', 'গ্রাহক')):
        return 'Use Customers and customer history to review your business records. This assistant does not export customer details.'
    if any(w in text for w in ('stock', 'restock', 'inventory', 'স্টক', 'মজুত')):
        with get_db() as db:
            setting = db.execute('SELECT low_stock_threshold FROM inventory_settings WHERE business_id=?', (business_id,)).fetchone()
            threshold = setting['low_stock_threshold'] if setting else 20
            rows = db.execute('SELECT name,stock FROM products WHERE business_id=? AND stock<=? ORDER BY stock ASC LIMIT 20',
                              (business_id, threshold)).fetchall()
        if not rows:
            return 'No products are at or below your low-stock threshold.'
        return 'Stock at or below your threshold (up to 20 products):\n' + '\n'.join(
            str(r['name']) + ': ' + str(r['stock']) + ' units' for r in rows)
    if any(w in text for w in ('sold', 'seller', 'selling', 'product', 'পণ্য')):
        days = 30 if '30' in text else 7
        rows = sales_data(days, business_id)
        if not rows:
            return 'No paid sales recorded in the selected period.'
        top = max(rows, key=lambda r: r['qty'])
        return ('Best seller by units in the last ' + str(days) + ' days: ' +
                str(top['product_name']) + ' — ' + str(top['qty']) +
                ' units. Sales revenue before refunds: BDT ' + format(float(top['revenue']), ',.2f') +
                '. This measures sales, not profit.')
    if any(w in text for w in ('revenue', 'sales', 'summary', 'health', 'বিক্র', 'আয়')):
        stats = dashboard_stats(business_id)
        return ('Business overview — net revenue after recorded refunds:\nAll time: BDT ' +
                format(stats['total_revenue'], ',.2f') + '\nThis month: BDT ' +
                format(stats['month_revenue'], ',.2f') + '\nLast 7 calendar days: BDT ' +
                format(sum(d['revenue'] for d in stats['daily']), ',.2f') +
                '\nInvoices: ' + str(stats['total_invoices']) +
                '\nProducts: ' + str(stats['total_products']) +
                '\nLow stock (excludes out-of-stock products): ' + str(stats['low_stock']) +
                '\nSee Sales Analytics for period comparisons.')
    return ('I can show a business summary, revenue, best sellers and low-stock products locally. '
            'For app help: Inventory manages products, Point of Sale records sales, Invoices shows invoices, '
            'Purchase Orders manages supplier orders, and Returns & Refunds handles returns. '
            'For general guidance, choose a topic under General business advice. '
            'Your typed question is never sent to Gemini.')
