// Production config served by Nginx in place of frontend/config.js.
// apiBase '' = same origin: Nginx proxies /api, /ws, /uploads and the docs to the backend container.
window.SMARTCITY_CONFIG = { apiBase: '' };

(function () {
  const api = window.SMARTCITY_CONFIG.apiBase || '';
  const origin = api || window.location.origin;
  window.SMARTCITY = {
    API_BASE: api,
    WS_BASE: origin.replace(/^http/, 'ws')
  };
})();
