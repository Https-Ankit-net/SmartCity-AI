# syntax=docker/dockerfile:1.7
# SmartCity AI backend: FastAPI + Alembic + YOLOv8 (CPU) + spaCy.
#   docker build -t smartcity-backend ./backend

ARG PYTHON_VERSION=3.12

# ── Stage 1: build the virtualenv (compilers and pip caches stay here) ──
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Debian package versions move with the slim base image, so they are not pinned.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt .
# CPU-only PyTorch wheels keep the image ~2 GB smaller than the default CUDA build.
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
                --extra-index-url https://pypi.org/simple \
                -r requirements.txt
# Ultralytics pulls in desktop OpenCV, which needs X11 libraries (libxcb…) a server image doesn't have.
# Swap it for the headless build of the same version.
RUN version="$(python -c 'import importlib.metadata as m; print(m.version("opencv-python"))')" \
    && pip uninstall -y opencv-python \
    && pip install "opencv-python-headless==${version}"

# Bake the base YOLO weights into the image so containers never download at runtime.
WORKDIR /opt/models
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" \
    && test -f /opt/models/yolov8n.pt

# ── Stage 2: slim runtime ──
FROM python:${PYTHON_VERSION}-slim AS runtime

# libgl1/libglib2.0-0: OpenCV (pulled in by Ultralytics) needs them even without a display.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/uploads /tmp/ultralytics \
    && chown -R app:app /data /tmp/ultralytics

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/models /opt/models

WORKDIR /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app
COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production \
    AUTO_MIGRATE=false \
    UPLOAD_DIR=/data/uploads \
    YOLO_WEIGHTS=/opt/models/yolov8n.pt \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    WEB_CONCURRENCY=2

# Numeric so orchestrators can verify the container doesn't run as root.
USER 10001:10001
EXPOSE 8000
VOLUME ["/data/uploads"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY} --proxy-headers --forwarded-allow-ips='*'"]
