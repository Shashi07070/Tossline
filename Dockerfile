FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persist the Telethon session + SQLite DB across container restarts by
# mounting a volume at /app/data and pointing DB_PATH/TELETHON_SESSION there.
VOLUME ["/app/data"]

ENV DB_PATH=/app/data/toss_forward.db
ENV LOG_PATH=/app/data/toss_forward.log
ENV TELETHON_SESSION=/app/data/toss_forward_user

CMD ["python", "main.py"]
