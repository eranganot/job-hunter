// Runs in the page's own world: reports what every non-GET request got back, so
// the extension can read the site's verdict (Comeet answers 2xx or 423).
(() => {
  if (window.__jhNet) return; window.__jhNet = true;
  const tell = (method, url, status) => {
    try { window.postMessage({__jh: 'net', method, url: String(url), status}, '*'); } catch (e) { /* page gone */ }
  };
  const of = window.fetch;
  if (of) window.fetch = function (input, init) {
    const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const p = of.apply(this, arguments);
    if (method !== 'GET') p.then(r => tell(method, url, r.status), () => tell(method, url, 0));
    return p;
  };
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__jh = [String(m).toUpperCase(), u]; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    const x = this;
    if (x.__jh && x.__jh[0] !== 'GET') x.addEventListener('loadend', () => tell(x.__jh[0], x.__jh[1], x.status));
    return os.apply(this, arguments);
  };
})();
