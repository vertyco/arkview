FROM python:3.12-slim

# arkviewer is a small FastAPI service that reads ARK save files and serves
# parsed game data. One instance per map. Mount the save directory and a
# config.ini at runtime; arkviewer watches the directory for changes via
# watchdog (inotify on Linux) and reparses incrementally.

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so the layer caches when source changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application source.
COPY app/ ./app/
COPY main.py .

# Run as a non-root user. Save files are bind-mounted read-only from the host
# (the ASA dedicated server writes them; arkviewer only reads), so the
# container does not need any host-side write permissions.
RUN useradd --create-home --shell /bin/bash arkviewer && \
    chown -R arkviewer:arkviewer /app
USER arkviewer

# config.ini and the ARK save directory MUST be bind-mounted at runtime, e.g.:
#   docker run -d --name arkviewer-ragnarok \
#     -v /srv/ark/ragnarok/config.ini:/app/config.ini:ro \
#     -v /srv/ark/ragnarok/saves:/srv/ark/ragnarok/saves:ro \
#     -p 8000:8000 \
#     arkviewer:latest
#
# Inside config.ini, MapFilePath should point at the path AS SEEN INSIDE THE
# CONTAINER, e.g. /srv/ark/ragnarok/saves/Ragnarok.ark - same as the mount target.

EXPOSE 8000

# Lightweight health probe - the root endpoint returns metadata in <1ms after
# the first parse completes. Allow a generous start-period because the
# initial save parse can take 30-60s for a busy map.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request, sys; \
    sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status == 200 else sys.exit(1)"

CMD ["python", "main.py"]
