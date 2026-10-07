#!/bin/sh
set -e
python - << 'PY'
import os, time
from urllib.parse import urlparse
import psycopg2
u = urlparse(os.environ["DATABASE_URL"])
for i in range(60):
    try:
        conn = psycopg2.connect(dbname=u.path.lstrip("/"), user=u.username, password=u.password, host=u.hostname, port=u.port or 5432)
        conn.close()
        print("PostgreSQL ready")
        break
    except Exception as e:
        print(f"waiting db {i+1}/60 {e}")
        time.sleep(2)
else:
    raise SystemExit("db unavailable")
PY
python manage.py migrate --noinput
python manage.py seed
exec uvicorn config.asgi:application --host 0.0.0.0 --port 8770
