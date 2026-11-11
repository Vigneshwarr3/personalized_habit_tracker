FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# libgomp1: OpenMP runtime needed by LightGBM (phase 4)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements-server.txt .
RUN pip install -r requirements-server.txt

COPY server ./server
COPY dashboard ./dashboard

# uid 1000 = the host user that owns ./data (user)
RUN useradd --uid 1000 --create-home ht && mkdir -p /app/data && chown ht:ht /app/data
USER ht
