"""Entrypoint: validate config, reconcile Cloudflare + local cloudflared
config, then supervise `cloudflared tunnel run` as a child process.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time

from . import reconcile, state as state_mod
from .cf_client import CloudflareClient
from .config import ConfigError, parse_global, parse_hostnames

DATA_DIR = os.environ.get("DATA_DIR", "/data")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
CREDENTIALS_PATH = os.path.join(DATA_DIR, "credentials.json")
CONFIG_PATH = os.path.join(DATA_DIR, "config.yaml")

# How long to wait for cloudflared to report a real connection (via the
# same `cloudflared tunnel ready` check the HEALTHCHECK uses) before giving
# up on showing the routing summary and just following its logs as-is.
READY_TIMEOUT_SECONDS = 120
READY_POLL_INTERVAL_SECONDS = 1


def _routing_table_lines(routes: list) -> list[str]:
    """A bordered summary of the actual routing table, one log line per
    row -- matching cloudflared's own boxed "CONNECTIVITY PRE-CHECKS"
    banner style (each line logged separately, not one multi-line
    message).
    """
    if not routes:
        return []
    title = "TUNNELMATE ROUTES"
    rows = [f"https://{cfg.hostname}  -->  {cfg.service}" for cfg in routes]
    width = max(len(title), *(len(row) for row in rows))
    lines = [
        "+" + "-" * (width + 2) + "+",
        "|" + title.center(width + 2) + "|",
        "+" + "-" * (width + 2) + "+",
    ]
    lines += [f"| {row.ljust(width)} |" for row in rows]
    lines.append("+" + "-" * (width + 2) + "+")
    return lines


def _wait_until_ready(is_alive, check_ready, timeout: float, poll_interval: float) -> bool:
    """Poll `check_ready()` until it returns True, `is_alive()` returns
    False (the process exited), or `timeout` elapses.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_alive():
            return False
        if check_ready():
            return True
        time.sleep(poll_interval)
    return False


def run_cloudflared(tunnel_name: str, routes: list, log: logging.Logger) -> int:
    """Supervise cloudflared as a child process instead of exec'ing into
    it, so the routing summary can print *after* confirming a real
    connection -- as the last, most visible thing in the log stream --
    instead of before cloudflared's own dozens of startup lines, which is
    where an exec-and-replace approach is forced to put it.

    Signals are forwarded to the child rather than handled by this
    process directly, so `docker stop` still reaches cloudflared's own
    graceful-shutdown handling (its `--grace-period`) exactly as it would
    under a direct exec.
    """
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--config", CONFIG_PATH, "--no-autoupdate", "run", tunnel_name]
    )

    def _forward(signum, _frame):
        proc.send_signal(signum)

    signal.signal(signal.SIGTERM, _forward)
    signal.signal(signal.SIGINT, _forward)

    ready = _wait_until_ready(
        is_alive=lambda: proc.poll() is None,
        check_ready=lambda: subprocess.run(
            ["cloudflared", "tunnel", "--config", CONFIG_PATH, "ready"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0,
        timeout=READY_TIMEOUT_SECONDS,
        poll_interval=READY_POLL_INTERVAL_SECONDS,
    )

    if ready:
        for line in _routing_table_lines(routes):
            log.info(line)
    else:
        log.warning(
            "cloudflared did not report ready within %ss; skipping routing summary",
            READY_TIMEOUT_SECONDS,
        )

    return proc.wait()


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
    persist = lambda: state_mod.save(STATE_PATH, state)  # noqa: E731

    try:
        tunnel = reconcile.reconcile_tunnel(client, global_cfg, state)
        # Persist the tunnel's identity before doing anything else that could
        # fail (zone lookup, DNS/Access reconciliation) -- otherwise a failure
        # there would leave a freshly created tunnel unrecorded, and a restart
        # would create a second, different tunnel while `credentials.json`
        # (written below) still holds the first one's secret, permanently
        # desyncing the two.
        persist()
        if not os.path.exists(CREDENTIALS_PATH):
            write_credentials(tunnel)

        log.info("fetching accessible zones for hostname auto-detection")
        zones = client.list_zones()

        routes = [cfg for cfg in hostnames if cfg.is_route]
        path_scopes = [cfg for cfg in hostnames if not cfg.is_route]
        # reconcile_routes/reconcile_path_scopes call `persist` after each
        # individual Cloudflare mutation, so a failure partway through doesn't
        # lose track of what already happened this run -- see reconcile.py.
        reconcile.reconcile_routes(client, tunnel, routes, state, zones, persist)
        reconcile.reconcile_path_scopes(client, path_scopes, state, persist)

        config_text = reconcile.render_local_config(tunnel, CREDENTIALS_PATH, hostnames)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(config_text)
        log.info("wrote %s with %d route(s)", CONFIG_PATH, len(routes))
    except Exception as exc:
        # A bare traceback here (the default for an uncaught exception)
        # buries the one line that actually matters -- e.g. Cloudflare's
        # own "record already exists" -- under 15+ lines of call-stack
        # noise, repeated on every restart/unless-stopped crash loop.
        # Anything reaching here is already a message meant to be read
        # (a CloudflareAPIError's str() is the API's own error body; a
        # NoMatchingZoneError's is a plain sentence), so surface just that.
        log.error("failed to reconcile Cloudflare Tunnel (%s): %s", type(exc).__name__, exc)
        sys.exit(1)

    log.info("starting cloudflared tunnel %s", tunnel["name"])
    sys.exit(run_cloudflared(tunnel["name"], routes, log))


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
