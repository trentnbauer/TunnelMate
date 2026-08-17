ARG CLOUDFLARED_VERSION=2026.8.2
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

# `cloudflared tunnel ready` calls its own metrics server's /ready
# endpoint and returns a clean exit code -- true tunnel-connection health
# (connected to the Cloudflare edge or not), not just "the process is
# alive". Given the same --config as the running tunnel, it resolves the
# metrics address from that file's `metrics:` key itself (rendered by
# ingress.py -- cloudflared's own --metrics default is a random port, so
# without that pin in config.yaml this would have nothing to find).
# start-period is generous since reconcile.py's first run (tunnel/DNS/
# Access creation) has to finish, and cloudflared has to connect, before
# it starts listening at all.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD cloudflared tunnel --config /data/config.yaml ready

# Stays root here -- the entrypoint fixes up /data ownership, then drops
# to the unprivileged `tunnelmate` user via gosu before running anything
# from app/ or cloudflared itself.
ENTRYPOINT ["docker-entrypoint.sh"]
