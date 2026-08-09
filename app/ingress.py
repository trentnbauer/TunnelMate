"""Render the local cloudflared config.yaml from the desired hostname list.

Hand-templated (no PyYAML dependency) since the shape is fixed and small;
all values are already validated in config.py before reaching here.
"""

from __future__ import annotations

import json

from .config import HostnameConfig


def _yaml_str(value: str) -> str:
    # A YAML double-quoted scalar uses the same escaping rules as JSON, so
    # json.dumps is a safe, dependency-free way to quote hostname/service
    # values -- guards against '#', ': ', leading '-'/'*', etc. breaking
    # the hand-templated YAML below.
    return json.dumps(value)


# Without an explicit bind, cloudflared's metrics/health server (which
# exposes /ready) defaults to a random port (`--metrics`'s own default is
# "0.0.0.0:0") -- pinning it here is what makes the Dockerfile's
# HEALTHCHECK (which checks this exact address) able to find it at all.
METRICS_ADDR = "127.0.0.1:2000"


def render(tunnel_id: str, credentials_path: str, hostnames: list[HostnameConfig]) -> str:
    lines = [
        f"tunnel: {tunnel_id}",
        f"credentials-file: {_yaml_str(credentials_path)}",
        f"metrics: {METRICS_ADDR}",
        "ingress:",
    ]
    for cfg in hostnames:
        if not cfg.is_route:
            continue  # path-scoped entries reuse their base route's ingress rule
        lines.append(f"  - hostname: {_yaml_str(cfg.hostname)}")
        lines.append(f"    service: {_yaml_str(cfg.service)}")
        if cfg.service.startswith("https://"):
            # The tunnel already terminates TLS at the Cloudflare edge, so
            # this is just the last-mile hop to the origin container --
            # almost always a self-signed/internal cert. Verifying it buys
            # nothing (an attacker on that hop could see plaintext HTTP
            # just as easily) and breaks the common case, so always skip it.
            lines.append("    originRequest:")
            lines.append("      noTLSVerify: true")
    lines.append("  - service: http_status:404")
    return "\n".join(lines) + "\n"
