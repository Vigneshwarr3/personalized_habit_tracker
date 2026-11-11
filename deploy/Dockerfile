# 1) build the web app (React) -> /web/dist
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
RUN npm run build

# 2) the API (also serves the web app at /ui/) + the Streamlit dashboard (until every page has moved)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# libgomp1: OpenMP runtime needed by LightGBM (phase 4)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements-server.txt .
RUN pip install -r requirements-server.txt

COPY server ./server
COPY dashboard ./dashboard
COPY --from=web /web/dist ./web/dist

# uid 1000 = the host user that owns ./data (user)
RUN useradd --uid 1000 --create-home ht && mkdir -p /app/data && chown ht:ht /app/data
USER ht
