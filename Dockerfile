# ArkViewer v3 - slim runtime image.
# Build: docker build -t arkviewer:latest .
# Run:   see docker-compose.yml for the multi-map setup.

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ARKVIEWER_CONFIG=/config/config.ini \
    ARKVIEWER_DB=/state/arkviewer.db

WORKDIR /app

# Build deps for any wheel-less compile (arkparser ships pure-Python, but be safe).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY main.py launcher.py ./

# Non-root user. UID/GID 1000 is the common Docker host default; override at
# build time with --build-arg if your bind-mount owner differs.
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} arkviewer \
 && useradd  -u ${UID} -g ${GID} -M -s /usr/sbin/nologin arkviewer \
 && mkdir -p /config /state \
 && chown -R arkviewer:arkviewer /app /config /state

USER arkviewer

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/ || exit 1

CMD ["python", "main.py"]
