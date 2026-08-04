ARG CLOUDFLARED_VERSION=2026.7.3
FROM cloudflare/cloudflared:${CLOUDFLARED_VERSION} AS cloudflared

FROM python:3.12-slim AS final
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV PYTHONUNBUFFERED=1
VOLUME /data

ENTRYPOINT ["python", "-m", "app.main"]
