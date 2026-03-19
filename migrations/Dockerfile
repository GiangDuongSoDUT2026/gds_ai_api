FROM python:3.11-slim

WORKDIR /migrations

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/src/shared
COPY ../src/shared /app/src/shared 2>/dev/null || true

CMD ["alembic", "upgrade", "head"]
