FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3-pip \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY src/shared /app/src/shared
COPY src/worker/pyproject.toml /app/src/worker/pyproject.toml

RUN cd /app/src/shared && pip install --no-cache-dir -e .

COPY src/worker /app/src/worker
RUN cd /app/src/worker && pip install --no-cache-dir -e .

COPY code /app/code

ENV TRANSNETV2_WEIGHTS_DIR=/app/code/transnetv2-weights
# Makes `import transnetv2` work without sys.path manipulation
ENV PYTHONPATH="/app/code:${PYTHONPATH}"

CMD ["celery", "-A", "worker.app", "worker", "--loglevel=info", "-Q", "gpu.high,gpu.medium,db", "-c", "4"]
