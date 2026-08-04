"""Reconcile Cloudflare (tunnel, DNS, Access) and the local cloudflared
config against the desired hostname list, exactly once per container start.

This tool never watches the Docker socket for live changes -- env vars only
change when the container is recreated, which is an accepted, deliberate
trade-off in exchange for never needing Docker-socket access at all.
"""

from __future__ import annotations

import logging

from . import ingress, state as state_mod
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


def reconcile_hostnames(
    client: CloudflareClient,
    tunnel: dict,
    desired: list[HostnameConfig],
    state: dict,
    zones,
) -> None:
    desired_by_hostname = {cfg.hostname: cfg for cfg in desired}
    existing_hostnames = state["hostnames"]
    target = f"{tunnel['id']}.{TUNNEL_CNAME_SUFFIX}"

    # 1. Prune hostnames that disappeared from config -- only ever touches
    #    resources this tool itself created and recorded in state.json.
    for hostname in list(existing_hostnames) :
        if hostname in desired_by_hostname:
            continue
        entry = existing_hostnames[hostname]
        log.info("removing hostname %s (no longer in config)", hostname)
        if entry.get("dns_record_id"):
            client.delete_dns_record(entry["zone_id"], entry["dns_record_id"])
        if entry.get("access_app_id"):
            client.delete_access_app(entry["access_app_id"])
        del existing_hostnames[hostname]

    # 2. Create or update every desired hostname.
    for hostname, cfg in desired_by_hostname.items():
        prev = existing_hostnames.get(hostname)
        wants_access = cfg.accesstype in ("bypass", "auth")

        if prev is None:
            zone = match_zone(hostname, zones)
            dns_record_id = client.create_dns_record(zone.id, hostname, target)
            log.info("created DNS record for %s in zone %s", hostname, zone.name)

            access_app_id = access_policy_id = None
            if wants_access:
                access_app_id = client.create_access_app(hostname)
                access_policy_id = client.create_access_policy(
                    access_app_id, hostname, cfg.accesstype, cfg.authusers
                )
                log.info("created Access app+policy for %s (accesstype=%s)", hostname, cfg.accesstype)

            existing_hostnames[hostname] = {
                "zone_id": zone.id,
                "dns_record_id": dns_record_id,
                "access_app_id": access_app_id,
                "access_policy_id": access_policy_id,
                "accesstype": cfg.accesstype,
                "authusers": list(cfg.authusers),
            }
            continue

        # Existing hostname: the DNS record never changes (always the same
        # tunnel CNAME); only the Access app/policy can need reconciling.
        was_protected = prev.get("access_app_id") is not None
        changed = (cfg.accesstype, list(cfg.authusers)) != (
            prev.get("accesstype"),
            prev.get("authusers", []),
        )

        if not was_protected and wants_access:
            access_app_id = client.create_access_app(hostname)
            access_policy_id = client.create_access_policy(
                access_app_id, hostname, cfg.accesstype, cfg.authusers
            )
            log.info("added Access protection to %s (accesstype=%s)", hostname, cfg.accesstype)
        elif was_protected and not wants_access:
            client.delete_access_app(prev["access_app_id"])
            access_app_id = access_policy_id = None
            log.info("removed Access protection from %s (now public)", hostname)
        elif was_protected and wants_access and changed:
            # Update in place so the hostname is never briefly unprotected.
            client.update_access_policy(
                prev["access_app_id"], prev["access_policy_id"], hostname, cfg.accesstype, cfg.authusers
            )
            access_app_id, access_policy_id = prev["access_app_id"], prev["access_policy_id"]
            log.info("updated Access policy for %s (accesstype=%s)", hostname, cfg.accesstype)
        else:
            access_app_id, access_policy_id = prev.get("access_app_id"), prev.get("access_policy_id")

        existing_hostnames[hostname] = {
            "zone_id": prev["zone_id"],
            "dns_record_id": prev["dns_record_id"],
            "access_app_id": access_app_id,
            "access_policy_id": access_policy_id,
            "accesstype": cfg.accesstype,
            "authusers": list(cfg.authusers),
        }


def render_local_config(tunnel: dict, credentials_path: str, desired: list[HostnameConfig]) -> str:
    return ingress.render(tunnel["id"], credentials_path, desired)
