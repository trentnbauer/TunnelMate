"""Entrypoint: validate config, reconcile Cloudflare + local cloudflared
config, then exec into `cloudflared tunnel run` so it becomes PID 1.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from . import reconcile, state as state_mod
from .cf_client import CloudflareClient
from .config import ConfigError, parse_global, parse_hostnames

DATA_DIR = os.environ.get("TUNNEL_DATA_DIR", "/data")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
CREDENTIALS_PATH = os.path.join(DATA_DIR, "credentials.json")
CONFIG_PATH = os.path.join(DATA_DIR, "config.yaml")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("cloudflare-tunnel")

    try:
        global_cfg = parse_global(os.environ)
        hostnames = parse_hostnames(os.environ)
    except ConfigError as exc:
        log.error("invalid configuration: %s", exc)
        sys.exit(1)

    os.makedirs(DATA_DIR, exist_ok=True)
    state = state_mod.load(STATE_PATH)
    client = CloudflareClient(global_cfg.api_token, global_cfg.account_id)

    tunnel = reconcile.reconcile_tunnel(client, global_cfg, state)
    if not os.path.exists(CREDENTIALS_PATH):
        write_credentials(tunnel)

    log.info("fetching accessible zones for hostname auto-detection")
    zones = client.list_zones()

    reconcile.reconcile_hostnames(client, tunnel, hostnames, state, zones)
    state_mod.save(STATE_PATH, state)

    config_text = reconcile.render_local_config(tunnel, CREDENTIALS_PATH, hostnames)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(config_text)
    log.info("wrote %s with %d hostname(s)", CONFIG_PATH, len(hostnames))

    log.info("starting cloudflared tunnel %s", tunnel["name"])
    os.execvp("cloudflared", ["cloudflared", "tunnel", "--config", CONFIG_PATH, "run", tunnel["name"]])


def write_credentials(tunnel: dict) -> None:
    credentials = {
        "AccountTag": tunnel["account_tag"],
        "TunnelSecret": tunnel["tunnel_secret"],
        "TunnelID": tunnel["id"],
    }
    tmp_path = f"{CREDENTIALS_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(credentials, f)
    os.replace(tmp_path, CREDENTIALS_PATH)


if __name__ == "__main__":
    main()
