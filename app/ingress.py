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
        lines.append(f"  - hostname: {cfg.hostname}")
        lines.append(f"    service: {cfg.service}")
    lines.append("  - service: http_status:404")
    return "\n".join(lines) + "\n"
