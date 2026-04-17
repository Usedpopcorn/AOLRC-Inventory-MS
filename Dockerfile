FROM python:3.12-slim

WORKDIR /app

# system deps (psycopg2 needs build tools sometimes; slim usually ok with psycopg2-binary)
RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FLASK_APP=run.py
ENV PYTHONUNBUFFERED=1

# Render will provide PORT; local default 5000
CMD ["sh", "-c", "gunicorn -w 2 -b 0.0.0.0:${PORT:-5000} run:app"]
