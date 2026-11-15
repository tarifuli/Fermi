(function () {
  const originalFetch = window.fetch;
  window.fetch = function (input, init) {
    init = init || {};
    const method = (init.method || (input && input.method) || 'GET').toUpperCase();
    if (['POST','PUT','PATCH','DELETE'].includes(method)) {
      const target = typeof input === 'string' ? input : input.url;
      const url = new URL(target, window.location.href);
      if (url.origin === window.location.origin) {
        const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
        headers.set('X-CSRF-Token', document.querySelector('meta[name="csrf-token"]').content);
        init = Object.assign({}, init, {headers: headers});
      }
    }
    return originalFetch.call(this, input, init);
  };
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('form').forEach(function (form) {
      const method = (form.getAttribute('method') || 'GET').toUpperCase();
      if (method !== 'POST' || form.querySelector('input[name="_csrf_token"]')) return;
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = '_csrf_token';
      input.value = document.querySelector('meta[name="csrf-token"]').content;
      form.appendChild(input);
    });
  });
})();
