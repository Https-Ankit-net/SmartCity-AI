# Nginx: serves the static frontend and reverse-proxies the API, WebSockets and uploads.
#   docker build -f deployment/nginx/Dockerfile -t smartcity-web .
FROM nginx:1.27-alpine

RUN rm -f /etc/nginx/conf.d/default.conf
COPY deployment/nginx/smartcity.conf /etc/nginx/conf.d/smartcity.conf

# Only the pages the app uses; the empty Vite scaffold (src/, node_modules/) is not shipped.
COPY frontend/index.html frontend/map.html frontend/citizen_dashboard.html frontend/admin_dashboard.html /usr/share/nginx/html/
# Same-origin API config replaces the development config.js.
COPY deployment/nginx/config.js /usr/share/nginx/html/config.js

HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1/healthz"]
