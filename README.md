# SmartCity AI

A civic complaint platform. Citizens report problems such as potholes, overflowing garbage, broken streetlights, fallen trees or fires from a live map, with a photo and voice dictation. The backend classifies each report with YOLOv8 and text rules, scores its severity, merges duplicates, and routes it to the right city department. Department staff work through a portal with analytics, a status workflow and official responses, and citizens see progress live.

- **Citizen app**: live GIS map, report form (photo + voice), personal dashboard with a progress timeline and real-time notifications.
- **Staff portal**: city-wide (admin) or per-department analytics, complaint table with filters and sorting, status updates with internal notes and official responses, re-assignment, and an admin panel that approves department staff accounts.
- **AI**:
  - YOLOv8 image detection, with optional fine-tuned civic weights.
  - Severity scoring (1–10) that explains its score.
  - Duplicate detection within 50 m and 24 h.
  - spaCy text analysis (entities, issues, intent).
- **API docs**: Swagger UI at **`/docs`** (ReDoc at `/redoc`, schema at `/openapi.json`).

## Architecture

```mermaid
flowchart LR
    subgraph Browser
        C[Citizen pages<br/>index · map · citizen_dashboard]
        S[Staff portal<br/>admin_dashboard]
    end

    subgraph Docker["docker compose"]
        N[Nginx<br/>static pages + reverse proxy]
        subgraph B["FastAPI backend (N workers)"]
            API[REST API<br/>/api/*]
            WS[WebSockets<br/>/ws/*]
            AI[AI services<br/>YOLOv8 · severity · duplicates · spaCy]
        end
        PG[(PostgreSQL)]
        R[(Redis<br/>pub/sub)]
        V[(uploads volume)]
    end

    C & S -- HTTPS --> N
    N -- /api /uploads /docs --> API
    N -- /ws --> WS
    API --> AI
    API -- SQLAlchemy + Alembic --> PG
    API -- photos --> V
    API -- publish events --> R
    R -- fan-out to every worker --> WS
    WS -- live updates --> C & S
```

**How a report flows.** The citizen posts a complaint with an optional photo.
1. YOLO and the keyword rules pick the category and department.
2. The duplicate detector checks for an active complaint of the same category within 50 m from the last 24 h. If it finds one, the report becomes a **confirmation** of that complaint (HTTP 200) and no new row is created.
3. Otherwise the severity scorer combines the category, description keywords, the photo and nearby infrastructure into a 1–10 score, and the complaint is created (HTTP 201).
4. Events go through Redis to every backend worker, so the live map, the staff portal and the citizen's notification bell all update.

## Repository layout

```
backend/
  app/
    api/routes/       REST + WebSocket endpoints
    ai/               YOLO wrapper, incident classifier, infrastructure.json (OSM data)
    services/         severity_service, duplicate_detector, text_analysis, admin_service, notify…
    core/             settings, security (JWT/bcrypt), event bus (Redis), WebSocket managers
    db/               engine, Alembic runner (migrate.py), startup seeding
    models/ schemas/  SQLAlchemy models, Pydantic schemas
    manage.py         operational commands (create-admin)
  alembic/            migrations (0001 baseline → 0003 severity & duplicates)
  Dockerfile          multi-stage backend image
frontend/             static pages (vanilla HTML/JS) + config.js
deployment/nginx/     Nginx image, reverse-proxy config, production config.js
scripts/
  train_yolo.py       fine-tune YOLOv8 on civic classes
  civic_dataset.yaml  dataset template
  fetch_infrastructure.py   refresh hospitals/schools/major roads from OpenStreetMap
tests/                pytest suite (auth, complaints, admin, AI services)
docker-compose.yml    PostgreSQL + Redis + backend + Nginx
```

## Quick start (local development)

Requirements: Python 3.12. No database server is needed, because development uses SQLite.

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows   (source .venv/bin/activate on Linux/macOS)
pip install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple \
            -r requirements.txt -r requirements-dev.txt

# Optional backend/.env: DATABASE_URL, JWT_SECRET, JWT_EXPIRE_MINUTES… (see the bottom of .env.example)
uvicorn app.main:app --reload --port 8000
```

On startup the backend applies Alembic migrations and seeds the seven departments. Databases created before Alembic was introduced are detected, stamped at the matching revision, and upgraded in place.

Then open `frontend/index.html` in a browser, either from disk or through any static server. `frontend/config.js` points the pages at `http://127.0.0.1:8000`.

Create the first administrator. Admins can't self-register:

```bash
python -m app.manage create-admin --email admin@city.gov --name "City Admin"
python -m app.manage backfill-severity     # once, to score complaints filed before severity scoring existed
```

## Production deployment (Docker)

```bash
cp .env.example .env              # set JWT_SECRET (openssl rand -hex 32) and POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec backend python -m app.manage create-admin --email admin@city.gov --name "City Admin"
```

| Service | Image | Notes |
|---|---|---|
| `nginx` | `deployment/nginx/Dockerfile` | Only published port (`HTTP_PORT`, default 80). Serves the pages, proxies `/api`, `/ws`, `/uploads`, `/docs`, `/health`. 12 MB upload limit. |
| `backend` | `backend/Dockerfile` | Multi-stage, non-root (uid 10001), CPU PyTorch, YOLO weights baked in. The entrypoint waits for the DB and migrates once, then starts `WEB_CONCURRENCY` uvicorn workers. Health check on `/health`. |
| `db` | `postgres:16-alpine` | Data in the `pgdata` volume. |
| `redis` | `redis:7-alpine` | Pub/sub only, so WebSocket events reach clients connected to any worker. |

Uploaded photos live in the `uploads` volume. To use fine-tuned YOLO weights, place the `.pt` file in `backend/app/ai/weights/` (mounted read-only) and set `YOLO_WEIGHTS=/opt/models/custom/<file>.pt` in `.env`.

Terminate TLS in front of Nginx with your load balancer, or add a `listen 443 ssl` block. Then set `CORS_ORIGINS` to your public origin.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` enforces a strong `JWT_SECRET` and pins CORS to `CORS_ORIGINS`. |
| `DATABASE_URL` | `sqlite:///./smartcity_ai.db` | SQLAlchemy URL, e.g. `postgresql+psycopg2://user:pass@host/db`. |
| `JWT_SECRET` | *(dev placeholder)* | Token signing key. **Required** in production (32+ chars). |
| `JWT_EXPIRE_MINUTES` | `60` | Access-token lifetime. |
| `CORS_ORIGINS` | `*` | Comma-separated origins (production only). |
| `REDIS_URL` | *(unset)* | Enables cross-worker WebSocket fan-out. Unset means in-process delivery (single worker). |
| `UPLOAD_DIR` | `uploads` | Photo storage, absolute or relative to `backend/`. |
| `MAX_UPLOAD_SIZE_MB` | `10` | Per-photo limit. |
| `YOLO_WEIGHTS` | `yolov8n.pt` | Detection weights (COCO, or civic weights from `train_yolo.py`). |
| `SPACY_MODEL` | `en_core_web_sm` | spaCy pipeline for `/api/ai/analyze-text`. Falls back to rules if it's missing. |
| `INFRASTRUCTURE_FILE` | `app/ai/data/infrastructure.json` | Key-infrastructure data for severity scoring. |
| `EMAIL_BACKEND` | `console` | `smtp` sends mail; `console` logs it; `disabled` drops it. |
| `EMAIL_FROM` / `EMAIL_FROM_NAME` | `no-reply@smartcity.local` / `SmartCity AI` | Sender address and name. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_SECURITY` | *(unset)* / `587` / `starttls` | Mail server; `SMTP_SECURITY` is `starttls`, `ssl` (port 465) or `none`. |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(unset)* | SMTP login, if the server needs one. |
| `FRONTEND_URL` | *(unset)* | Public address of the pages, used for links in emails. |
| `RATE_LIMIT_ENABLED` | `true` | Throttle login (30/5 min per address, 10/5 min per account), sign-up (20/hour per address) and AI endpoints. Limits are per worker. |
| `AUTO_MIGRATE` | `true` | Run migrations at app startup. The container sets `false` and migrates in its entrypoint instead. |

## API overview

Full interactive documentation is at **`http://localhost:8000/docs`** in development, or `/docs` behind Nginx.

| Area | Endpoints |
|---|---|
| Auth | `POST /api/users` (register), `POST /api/auth/login`, `GET /api/auth/me` |
| Complaints | `POST /api/complaints`, `POST /api/complaints/with-image` (201 created / 200 merged duplicate), `GET /api/complaints/my`, `GET /api/complaints/{id}/updates`, `GET /complaints/map` (signed in; positions, categories and statuses only) |
| Staff | `GET /api/admin/dashboard`, `GET /api/admin/complaints` (filters, sorting, pagination), `GET /api/admin/complaints/{id}`, `PATCH …/{id}/status`, `PATCH …/{id}/department` |
| Staff accounts (admin) | `GET /api/admin/staff?status=pending\|active\|rejected`, `POST /api/admin/staff/{id}/approve`, `POST /api/admin/staff/{id}/reject` |
| AI | `POST /api/ai/analyze-text`, `POST /detect-image` |
| Live (all need `?token=<JWT>`) | `WS /ws/complaints` (complaint feed: full records for staff of that department and admins, map data only for others), `WS /ws/notifications/{user_id}` (personal), `WS /ws/notifications` (city-wide alerts) |
| Ops | `GET /health`, `GET /api/departments` |

### Department staff approval

Department staff who register on the login page start as **pending** and can't sign in until an admin approves them. Active admins get a live notification when a request arrives. In the staff portal's **Staff Access** panel an admin can:
- **approve** a request, optionally correcting the department the applicant picked;
- **reject** a request with a reason, which the applicant sees when they try to log in;
- **revoke** an active account, which takes effect on the next request even with an existing token;
- **reinstate** a rejected account.

Accounts that an admin creates are active immediately. Accounts that existed before approvals were introduced stay active.

**Emails.** The applicant is emailed when their request is received, approved (with any note from the admin), rejected (with the reason), revoked or restored. Every active admin is emailed when a new request arrives, with a link to the portal. Mail is sent after the response returns and retried twice, so a mail-server outage never blocks registration or approval; failures are logged. Configure it with the `EMAIL_*`, `SMTP_*` and `FRONTEND_URL` settings (examples for Gmail, SendGrid and SES are in `.env.example`). The default `EMAIL_BACKEND=console` only writes emails to the backend log.

Roles:
- **citizen**: files and tracks their own complaints.
- **department**: sees and acts only on complaints routed to their department, and can transfer them out.
- **admin**: sees the whole city.

## AI components

**Severity scoring** (`services/severity_service.py`). The score is 1–10, and every point comes with a stated reason:
- **Category hazard:** fire 5, garbage 2, and so on.
- **Description keywords:** "fire", "trapped", "blocked highway" and similar. Negations such as "no fire" are ignored, and keywords add at most 4 points.
- **A confident image detection:** up to +2.
- **Proximity to key infrastructure:** hospitals, schools, fire and police stations, transit hubs, substations and major roads, up to +3.
- **Crowd signal:** +1 once three other citizens have confirmed the issue.

Complaint priority becomes the higher of the rule-based priority and the band implied by the severity score.

**Duplicate detection** (`services/duplicate_detector.py`). A report counts as a duplicate when it has the same category as an active (Pending or In Progress) complaint filed within the last 24 h and within 50 m (haversine distance). A duplicate increments `confirmation_count` on the existing complaint instead of creating a new row. Each citizen counts once. Confirmers see the complaint in their dashboard and are notified when its status changes.

**Text analysis** (`POST /api/ai/analyze-text`). spaCy extracts named entities, landmarks ("near Rasulgarh Square"), time expressions and key issues, classifies intent (`report_issue`, `emergency`, `follow_up`, `feedback`, `question`), and suggests a category, department, title and severity. Without spaCy it falls back to regular expressions, and `backend` in the response tells you which ran.

**Infrastructure data** (`scripts/fetch_infrastructure.py`). This downloads hospitals, schools, emergency services, transit hubs, substations and motorway, trunk and primary roads from OpenStreetMap through the Overpass API. The bundled file covers Bhubaneswar. Re-run it with `--bbox` for another city. Data © OpenStreetMap contributors, ODbL.

**YOLO fine-tuning** (`scripts/train_yolo.py`). This trains YOLOv8 on the civic classes `pothole`, `overflowing_trash`, `streetlight_failure` and `fallen_tree`.
- It checks the dataset first, then trains, validates and prints per-class metrics.
- It exports the best weights plus a JSON summary.
- It supports `--resume`, `--val-only` and `--dry-run`.

The backend already routes those four classes to departments.

```bash
python scripts/train_yolo.py --data scripts/civic_dataset.yaml --epochs 100
YOLO_WEIGHTS=backend/app/ai/weights/civic-yolov8.pt uvicorn app.main:app   # from backend/
```

## Database migrations

```bash
cd backend
alembic upgrade head                              # or: python -m app.db.migrate (also seeds departments)
alembic revision --autogenerate -m "add field"    # after changing a model
alembic check                                     # fails if models and migrations disagree
```

The migrations are tested on both SQLite and PostgreSQL, covering upgrade, `alembic check`, and a full downgrade and re-upgrade.

## Tests

```bash
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
pytest                                            # from the repo root; SQLite by default
TEST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost/smartcity_test pytest   # against PostgreSQL
```

| File | Covers |
|---|---|
| `tests/test_auth.py` | Registration and validation, login, JWT claims and expiry, tampering, role escalation, bcrypt and the legacy scrypt upgrade, the production secret guard, admin-only signup |
| `tests/test_complaints.py` | Login-protected creation, keyword and image routing, severity, duplicate detection (radius, time window, category, status, self-repeat, crowd bump), `/complaints/my` ownership |
| `tests/test_admin.py` | RBAC (anonymous, citizen, department, admin), department scoping, status workflow validation and history, WebSocket notifications to owners and confirmers, re-assignment, pagination |
| `tests/test_staff_approval.py` | Pending staff can't log in, approval and correcting the department, rejection reasons, revoking cuts off existing tokens, reinstating, admin-only review, live notification to admins |
| `tests/test_emails.py` | Who gets which approval email, content (department, note, reason, links), HTML escaping, header-injection refusal, SMTP STARTTLS + login sequence, retries, mail failures never failing the request |
| `tests/test_hardening.py` | Regression tests for the security review: live-feed scoping, login-only map data, socket closing on revoke, stalled clients, upload references, confirmer privacy, department scope, simultaneous reports, input validation, timezone filters, credentials, rate limits, the slide-5 example, the severity backfill |
| `tests/test_ai_services.py` | Severity factors and caps, proximity, text-analysis intent and entities, the analyze-text endpoint, Redis event fan-out across workers (fakeredis) and the fallback when Redis is down |

YOLO is stubbed in tests, so no model weights or GPU are needed.

## CI/CD

`.github/workflows/ci.yml` runs on pushes and pull requests:
1. **Tests** on a SQLite and PostgreSQL 16 matrix, plus `alembic check`.
2. **Lint**: hadolint on both Dockerfiles, `docker compose config`, and `nginx -t`.
3. **Images**: builds the backend and web images, and pushes them to GHCR (`ghcr.io/<owner>/<repo>-backend` / `-web`) on `main` and `v*` tags.
4. **Smoke test**: `docker compose up --wait` and then checks health, the API, the docs and the WebSocket backend through Nginx.

## Security notes

- Passwords are hashed with bcrypt. JWTs are HS256 with a configurable lifetime. Production refuses a weak or default secret.
- Complaints are always filed as the signed-in user, never as a `user_id` taken from the request body.
- Admin accounts can only be created by an admin or with `python -m app.manage create-admin`. Department staff need an admin's approval before they can sign in. The user list and full complaint list are admin-only.
- Every WebSocket requires a valid token from an active account. The complaint feed shows full details only to admins and to staff of the complaint's department. Revoking a staff account closes its open connections immediately.
- Citizens who confirm a duplicate see the official complaint's progress with their own report details, never the original reporter's text, photo or identity.
- Uploaded files are only ever referenced by the server-generated names; `/detect-image` needs a login and keeps nothing.
- Duplicate detection takes a lock (a PostgreSQL advisory lock across workers), so simultaneous reports of one incident become one complaint with confirmations.
- Emails are case-insensitive, passwords over bcrypt's 72-byte limit are refused, and login takes the same time whether or not an account exists. Login, sign-up and AI endpoints are rate limited.
- The containers run as a non-root user. Only Nginx is exposed, and it sends `nosniff`, frame-options and referrer-policy headers.
