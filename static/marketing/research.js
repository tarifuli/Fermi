'use strict';
function calculateScenario(kind, first, second) {
  if (![first, second].every(Number.isFinite) || first < 0 || second < 0) return null;
  if (kind === 'inventory') return first <= 10000 && second <= 1000000 && Number.isInteger(first) && Number.isInteger(second) ? first * second * 30 : null;
  if (kind === 'refund') return first <= 100000000 && second <= 100000000 && Number.isInteger(first) && Number.isInteger(second) ? first - second : null;
  if (kind === 'time') return first <= 1440 && second <= 31 && Number.isInteger(first) && Number.isInteger(second) ? first * second / 60 : null;
  return null;
}
if (typeof module !== 'undefined' && module.exports) module.exports = {calculateScenario};
if (typeof document !== 'undefined') {
  const first = document.querySelector('#scenario-first');
  const second = document.querySelector('#scenario-second');
  const update = () => {
    const kind = document.body.dataset.scenario;
    const value = first.value.trim() === '' || second.value.trim() === '' ? null : calculateScenario(kind, Number(first.value), Number(second.value));
    document.querySelector('#scenario-error').hidden = value !== null;
    first.setAttribute('aria-invalid', String(!first.validity.valid || first.value === ''));
    second.setAttribute('aria-invalid', String(!second.validity.valid || second.value === ''));
    document.querySelector('#scenario-result').textContent = value === null ? 'Check inputs' : kind === 'time' ? value.toLocaleString('en-US', {maximumFractionDigits: 1}) + ' hours' : '৳' + value.toLocaleString('en-US', {maximumFractionDigits: 0});
  };
  if (first && second) {first.addEventListener('input', update); second.addEventListener('input', update); update();}
  document.querySelector('#journey-step')?.addEventListener('change', event => {
    const select = event.target;
    const option = select.options[select.selectedIndex];
    document.querySelector('#journey-position').textContent = `Stage ${select.selectedIndex + 1} of ${select.options.length}`;
    document.querySelector('#journey-title').textContent = option.textContent;
    document.querySelector('#journey-detail').textContent = option.dataset.detail;
  });
}
