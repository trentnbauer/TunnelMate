"""Reconcile Cloudflare (tunnel, DNS, Access) and the local cloudflared
config against the desired hostname list, exactly once per container start.

This tool never watches the Docker socket for live changes -- env vars only
change when the container is recreated, which is an accepted, deliberate
trade-off in exchange for never needing Docker-socket access at all.

Two kinds of desired entries are reconciled separately:
- routes (HOSTNAME_N with no path): own a DNS record + tunnel ingress rule,
  and optionally a whole-hostname Access app if USERS_N is set.
- path scopes (HOSTNAME_N with a path, e.g. app.example.com/admin): only
  add an Access app scoped to that path on top of an existing route -- no
  DNS or ingress changes.
"""

from __future__ import annotations

import logging

from . import ingress
from .cf_client import CloudflareClient
from .config import GlobalConfig, HostnameConfig
from .zones import match as match_zone

log = logging.getLogger(__name__)

TUNNEL_CNAME_SUFFIX = "cfargotunnel.com"


def reconcile_tunnel(client: CloudflareClient, global_cfg: GlobalConfig, state: dict) -> dict:
    """Create the tunnel on first run; reuse the persisted identity afterwards."""
    if state.get("tunnel"):
        return state["tunnel"]

    tunnel = client.create_tunnel(global_cfg.tunnel_name)
    state["tunnel"] = tunnel
    log.info("created new tunnel %s (id=%s)", tunnel["name"], tunnel["id"])
    return tunnel


def reconcile_routes(
    client: CloudflareClient,
    tunnel: dict,
    desired: list[HostnameConfig],
    state: dict,
    zones,
) -> None:
    desired_by_hostname = {cfg.hostname: cfg for cfg in desired}
    routes = state["routes"]
    target = f"{tunnel['id']}.{TUNNEL_CNAME_SUFFIX}"

    # 1. Prune routes that disappeared from config -- only ever touches
    #    resources this tool itself created and recorded in state.json.
    for hostname in list(routes):
        if hostname in desired_by_hostname:
            continue
        entry = routes[hostname]
        log.info("removing route %s (no longer in config)", hostname)
        if entry.get("dns_record_id"):
            client.delete_dns_record(entry["zone_id"], entry["dns_record_id"])
        if entry.get("access_app_id"):
            client.delete_access_app(entry["access_app_id"])
        del routes[hostname]

    # 2. Create or update every desired route.
    for hostname, cfg in desired_by_hostname.items():
        prev = routes.get(hostname)
        wants_access = bool(cfg.authusers)

        if prev is None:
            zone = match_zone(hostname, zones)
            dns_record_id = client.create_dns_record(zone.id, hostname, target)
            log.info("created DNS record for %s in zone %s", hostname, zone.name)

            access_app_id = access_policy_id = None
            if wants_access:
                access_app_id = client.create_access_app(hostname)
                access_policy_id = client.create_access_policy(access_app_id, hostname, cfg.authusers)
                log.info("created Access app+policy for %s", hostname)

            routes[hostname] = {
                "zone_id": zone.id,
                "dns_record_id": dns_record_id,
                "access_app_id": access_app_id,
                "access_policy_id": access_policy_id,
                "authusers": list(cfg.authusers),
            }
            continue

        # Existing route: the DNS record never changes (always the same
        # tunnel CNAME); only the Access app/policy can need reconciling.
        was_protected = prev.get("access_app_id") is not None
        changed = list(cfg.authusers) != prev.get("authusers", [])

        if not was_protected and wants_access:
            access_app_id = client.create_access_app(hostname)
            access_policy_id = client.create_access_policy(access_app_id, hostname, cfg.authusers)
            log.info("added Access protection to %s", hostname)
        elif was_protected and not wants_access:
            client.delete_access_app(prev["access_app_id"])
            access_app_id = access_policy_id = None
            log.info("removed Access protection from %s (now public)", hostname)
        elif was_protected and wants_access and changed:
            # Update in place so the hostname is never briefly unprotected.
            client.update_access_policy(prev["access_app_id"], prev["access_policy_id"], hostname, cfg.authusers)
            access_app_id, access_policy_id = prev["access_app_id"], prev["access_policy_id"]
            log.info("updated Access policy for %s", hostname)
        else:
            access_app_id, access_policy_id = prev.get("access_app_id"), prev.get("access_policy_id")

        routes[hostname] = {
            "zone_id": prev["zone_id"],
            "dns_record_id": prev["dns_record_id"],
            "access_app_id": access_app_id,
            "access_policy_id": access_policy_id,
            "authusers": list(cfg.authusers),
        }


def reconcile_path_scopes(client: CloudflareClient, desired: list[HostnameConfig], state: dict) -> None:
    desired_by_key = {cfg.scope_key: cfg for cfg in desired}
    path_scopes = state["path_scopes"]

    # 1. Prune path scopes that disappeared from config.
    for key in list(path_scopes):
        if key in desired_by_key:
            continue
        entry = path_scopes[key]
        log.info("removing path scope %s (no longer in config)", key)
        client.delete_access_app(entry["access_app_id"])
        del path_scopes[key]

    # 2. Create or update every desired path scope. Every entry here always
    #    has authusers (config.py rejects a path-scoped entry without them).
    for key, cfg in desired_by_key.items():
        prev = path_scopes.get(key)

        if prev is None:
            access_app_id = client.create_access_app(key)
            access_policy_id = client.create_access_policy(access_app_id, key, cfg.authusers)
            log.info("created Access app+policy for path scope %s", key)
            path_scopes[key] = {
                "access_app_id": access_app_id,
                "access_policy_id": access_policy_id,
                "authusers": list(cfg.authusers),
            }
            continue

        if list(cfg.authusers) != prev.get("authusers", []):
            client.update_access_policy(prev["access_app_id"], prev["access_policy_id"], key, cfg.authusers)
            log.info("updated Access policy for path scope %s", key)

        path_scopes[key] = {
            "access_app_id": prev["access_app_id"],
            "access_policy_id": prev["access_policy_id"],
            "authusers": list(cfg.authusers),
        }


def render_local_config(tunnel: dict, credentials_path: str, desired: list[HostnameConfig]) -> str:
    return ingress.render(tunnel["id"], credentials_path, desired)
