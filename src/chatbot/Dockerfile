FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY src/shared /app/src/shared
COPY src/chatbot /app/src/chatbot

RUN pip install --no-cache-dir -e /app/src/shared \
 && pip install --no-cache-dir -e /app/src/chatbot

EXPOSE 8001

CMD ["uvicorn", "chatbot.main:app", "--host", "0.0.0.0", "--port", "8001"]
