# Grex Parser — web panel and REST API in one container.
# Build: docker build -t grex-parser .   Run with Redis: docker compose up -d
# bookworm: patchright 1.52 installs Chromium system deps for Debian 12, not 13
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Xvfb: the crawlers open Chromium with a window (headless=False), which needs
# a display; xvfb-run gives it a virtual one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && patchright install --with-deps chromium

COPY . .
# A copy outside the data/ volume, so first start can always create settings.yml
RUN chmod +x docker/entrypoint.sh && cp data/settings.yml.example docker/settings.yml.example

# data/ (settings.yml, admin.db, cookies, reports) is a volume — see docker-compose.yml
VOLUME ["/app/data"]
EXPOSE 8004

ENTRYPOINT ["docker/entrypoint.sh"]
