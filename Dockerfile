# The web app. Production backends (pymongo, qdrant-client, fastembed) are
# installed here but imported only when selected, so an image built without
# them still runs on the defaults.
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir pymongo==4.10.1

COPY . .

# NOT ROOT. The app writes nothing it does not have to; a JSON store, if one is
# used, goes under /app/data, which is the only writable path it needs.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

EXPOSE 8000

# ONE WORKER, deliberately: sessions are in memory, so a second worker would
# not see the first one's interactions. See web_sessions.py.
CMD ["python", "serve.py", "--host", "0.0.0.0", "--port", "8000"]
