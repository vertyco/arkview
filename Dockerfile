# ArkViewer - slim runtime image.
# Build: docker build -t arkviewer:latest .
# Run:   see docker-compose.example.yml for the multi-map setup.
#
# Parsing is delegated to the pure-Python `arkparser` library and runs in a
# short-lived multiprocessing child, so no .NET / external exe is needed and
# the long-lived server process stays lean. There is no database: each parse
# writes ASV_*.json into /app/output (container-local, ephemeral) and the
# server holds the parsed result in memory until the next parse.

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is only needed for the HEALTHCHECK below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY common/ ./common/
COPY main.py ./

# Non-root user. UID/GID 1000 is the common Docker host default; override at
# build time with --build-arg if your save-mount/host owner differs.
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} arkviewer \
 && useradd  -u ${UID} -g ${GID} -M -s /usr/sbin/nologin arkviewer \
 && mkdir -p /app/output \
 && chown -R arkviewer:arkviewer /app

USER arkviewer

EXPOSE 8000

# `/` requires the bearer key when APIKey is set, so a plain GET returns 401 -
# both 200 (no key) and 401 (key set) mean "server is up". Connection refused
# yields 000 and fails the check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ | grep -qE "^(200|401)$" || exit 1

# Bind 0.0.0.0 explicitly (argv[1] overrides the host): inside a container the
# service runs as plain `python`, and the in-code default binds 127.0.0.1 when
# not frozen — which the published port mapping can't reach.
CMD ["python", "main.py", "0.0.0.0"]
