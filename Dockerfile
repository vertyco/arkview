FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/
COPY main.py .

# config.ini should be bind-mounted at runtime, e.g.:
#   docker run -v /host/config.ini:/app/config.ini ...
# Likewise, ARK save paths referenced in config.ini should be bind-mounted.

EXPOSE 8000

CMD ["python", "main.py"]
