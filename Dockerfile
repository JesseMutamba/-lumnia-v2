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

# --proxy-headers so the app sees the real https scheme behind Fly's edge
# (needed for Secure session cookies).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
