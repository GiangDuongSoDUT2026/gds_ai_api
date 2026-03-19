FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

# Copy source first (shared must be installed before api)
COPY src/shared /app/src/shared
COPY src/api /app/src/api

# Install shared, then api (both as editable so imports work)
RUN pip install --no-cache-dir -e /app/src/shared \
 && pip install --no-cache-dir -e /app/src/api

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
