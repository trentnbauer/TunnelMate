ARG CLOUDFLARED_VERSION=2026.7.3
FROM cloudflare/cloudflared:${CLOUDFLARED_VERSION} AS cloudflared

FROM python:3.14-slim AS final
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 1000 tunnelmate

COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
VOLUME /data

# Stays root here -- the entrypoint fixes up /data ownership, then drops
# to the unprivileged `tunnelmate` user via gosu before running anything
# from app/ or cloudflared itself.
ENTRYPOINT ["docker-entrypoint.sh"]
