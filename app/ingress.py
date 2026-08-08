"""Render the local cloudflared config.yaml from the desired hostname list.

Hand-templated (no PyYAML dependency) since the shape is fixed and small;
all values are already validated in config.py before reaching here.
"""

from __future__ import annotations

from .config import HostnameConfig


def render(tunnel_id: str, credentials_path: str, hostnames: list[HostnameConfig]) -> str:
    lines = [
        f"tunnel: {tunnel_id}",
        f"credentials-file: {credentials_path}",
        "ingress:",
    ]
    for cfg in hostnames:
        if not cfg.is_route:
            continue  # path-scoped entries reuse their base route's ingress rule
        lines.append(f"  - hostname: {cfg.hostname}")
        lines.append(f"    service: {cfg.service}")
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
