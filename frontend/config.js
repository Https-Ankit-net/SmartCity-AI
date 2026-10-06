// Where the pages find the SmartCity API.
// Development: the FastAPI server on 127.0.0.1:8000 (pages opened from disk or any dev server).
// Docker/production: Nginx serves deployment/nginx/config.js in place of this file, with
// apiBase '' so every request goes to the same origin and Nginx proxies it to the backend.
window.SMARTCITY_CONFIG = window.SMARTCITY_CONFIG || { apiBase: 'http://127.0.0.1:8000' };

(function () {
  const api = window.SMARTCITY_CONFIG.apiBase || '';
  const origin = api || window.location.origin;
  window.SMARTCITY = {
    API_BASE: api,
    WS_BASE: origin.replace(/^http/, 'ws')
  };
})();
