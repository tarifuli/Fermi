'use strict';
const menuButton = document.querySelector('.menu-button');
const nav = document.querySelector('#primary-nav');
menuButton?.addEventListener('click', () => {
  const open = menuButton.getAttribute('aria-expanded') !== 'true';
  menuButton.setAttribute('aria-expanded', String(open)); nav.classList.toggle('open', open);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && menuButton?.getAttribute('aria-expanded') === 'true') {
    menuButton.setAttribute('aria-expanded', 'false'); nav.classList.remove('open'); menuButton.focus();
  }
});
if ('IntersectionObserver' in window && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
  document.documentElement.classList.add('js-motion');
  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) { entry.target.classList.add('visible'); observer.unobserve(entry.target); }
  }), {threshold: 0.06});
  document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
}
const flows = {
  stock: ['A CONNECTED START', 'Give your products a home.', 'Add product names, prices and stock quantities so the next checkout starts with an organised catalogue.', ['Product catalogue', 'Stock quantities', 'Low-stock threshold']],
  sell: ['AT THE COUNTER', 'Turn a cart into a clear record.', 'Search your catalogue, choose quantities and complete a sale. Keep the invoice and recorded stock connected to the transaction.', ['Point of sale', 'Invoice history', 'Updated stock']],
  review: ['THE NEXT DECISION', 'Look back. Plan ahead.', 'Review recorded sales and stock alerts. Use product performance and supplier records when planning the next working day.', ['Sales summaries', 'Stock alerts', 'Purchase planning']]
};
const flowTabs = [...document.querySelectorAll('[data-flow]')];
flowTabs.forEach((button, index) => {
  button.addEventListener('click', () => {
    flowTabs.forEach(b => {b.setAttribute('aria-selected', String(b === button)); b.tabIndex = b === button ? 0 : -1;});
    const [label, title, copy, tags] = flows[button.dataset.flow];
    const panel = document.querySelector('#workflow-panel');
    panel.setAttribute('aria-labelledby', button.id);
    panel.querySelector('.demo-kicker').textContent = label;
    document.querySelector('#flow-title').textContent = title;
    document.querySelector('#flow-copy').textContent = copy;
    document.querySelector('#flow-items').replaceChildren(...tags.map(tag => {const el = document.createElement('span'); el.textContent = tag; return el;}));
  });
  button.addEventListener('keydown', e => {
    let next;
    if (e.key === 'ArrowRight') next = (index + 1) % flowTabs.length;
    if (e.key === 'ArrowLeft') next = (index + flowTabs.length - 1) % flowTabs.length;
    if (e.key === 'Home') next = 0;
    if (e.key === 'End') next = flowTabs.length - 1;
    if (next !== undefined) {e.preventDefault(); flowTabs[next].focus(); flowTabs[next].click();}
  });
});
document.querySelectorAll('[data-compare]').forEach(button => button.addEventListener('click', () => {
  const after = button.dataset.compare === 'after';
  document.querySelectorAll('[data-compare]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
  document.querySelector('#compare-label').textContent = after ? 'RECORDS IN ONE WORKFLOW' : 'INFORMATION IN SEPARATE PLACES';
  document.querySelector('#compare-title').textContent = after ? 'One workspace. A clearer picture.' : 'One question. Several records.';
  const items = after ? ['Review recorded sales in your dashboard', 'Check stock in the same platform', 'Open the invoice from your history'] : ['Check a notebook for the last sale', 'Find a separate stock sheet', 'Look for an invoice copy'];
  document.querySelector('#compare-list').replaceChildren(...items.map(text => {const li = document.createElement('li'); li.textContent = text; return li;}));
}));
document.querySelector('#stock-filter')?.addEventListener('change', e => {
  let count = 0;
  document.querySelectorAll('[data-stock]').forEach(row => {const stock = Number(row.dataset.stock); row.hidden = !(e.target.value === 'all' || (e.target.value === 'low' && stock <= 5) || (e.target.value === 'out' && stock === 0)); if (!row.hidden) count++;});
  document.querySelector('#stock-count').textContent = `Showing ${count} sample product${count === 1 ? '' : 's'}`;
});
let quantity = 1;
function updateCart(delta) {
  quantity = Math.max(1, Math.min(20, quantity + delta));
  document.querySelector('#qty-value').textContent = quantity;
  document.querySelector('#cart-total').textContent = '৳' + (quantity * 150).toLocaleString('en-US');
  document.querySelector('#qty-minus').disabled = quantity === 1;
  document.querySelector('#qty-plus').disabled = quantity === 20;
  document.querySelector('#sample-invoice').hidden = true;
}
document.querySelector('#qty-minus')?.addEventListener('click', () => updateCart(-1));
document.querySelector('#qty-plus')?.addEventListener('click', () => updateCart(1));
if (document.querySelector('#qty-minus')) updateCart(0);
document.querySelector('#demo-invoice')?.addEventListener('click', () => {
  const invoice = document.querySelector('#sample-invoice'); invoice.hidden = false;
  document.querySelector('#invoice-line').textContent = `${quantity} × Everyday notebook · Total ৳${(quantity * 150).toLocaleString('en-US')}`;
  invoice.setAttribute('role', 'status');
});
document.querySelector('#period')?.addEventListener('change', e => {
  const month = e.target.value === 'month';
  document.querySelector('#insight-total').textContent = month ? '৳51,900' : '৳12,450';
  const values = month ? [168, 120, 76] : [42, 28, 16];
  const widths = month ? [88, 63, 40] : [82, 55, 31];
  document.querySelectorAll('#insight-bars>div').forEach((row,i) => {row.querySelector('b').textContent = values[i]; row.querySelector('i').style.setProperty('--w', widths[i]+'%');});
});
