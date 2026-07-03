FROM python:3.11-slim

WORKDIR /app

# Deps first for layer caching. calamine/pandas/openpyxl ship wheels — no
# build toolchain needed on slim.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite lives on the mounted volume; the app creates the dir on first write.
ENV LUMNIA_DB=/data/lumnia.db
EXPOSE 8080

# --proxy-headers so the app sees the real https scheme behind the platform
# edge (needed for Secure session cookies). Shell form so $PORT expands:
# Render injects PORT; Fly doesn't, so the 8080 fallback matches fly.toml.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} \
    --proxy-headers --forwarded-allow-ips="*"
