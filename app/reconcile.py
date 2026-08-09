"""Reconcile Cloudflare (tunnel, DNS, Access) and the local cloudflared
config against the desired hostname list, exactly once per container start.

This tool never watches the Docker socket for live changes -- env vars only
change when the container is recreated, which is an accepted, deliberate
trade-off in exchange for never needing Docker-socket access at all.

Two kinds of desired entries are reconciled separately:
- routes (HOSTNAME_N with no path): own a DNS record + tunnel ingress rule,
  plus a whole-hostname Access app -- an "allow" policy restricted to
  USERS_N if set, or an explicit "bypass" policy if not. Every route always
  gets an app so a hostname's access decision is never left to whatever
  other Access application on the account happens to also match it.
- path scopes (HOSTNAME_N with a path, e.g. app.example.com/admin): only
  add an Access app scoped to that path on top of an existing route -- no
  DNS or ingress changes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from . import ingress
from .cf_client import CloudflareClient
from .config import GlobalConfig, HostnameConfig
from .zones import match as match_zone

log = logging.getLogger(__name__)

TUNNEL_CNAME_SUFFIX = "cfargotunnel.com"

# No-op default so existing callers (and tests) that don't care about
# incremental persistence don't need to pass anything.
_NO_PERSIST: Callable[[], None] = lambda: None  # noqa: E731


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
    persist: Callable[[], None] = _NO_PERSIST,
) -> None:
    """`persist` is called after each individual Cloudflare mutation so
    state.json never lags behind what's actually on Cloudflare -- if a
    later hostname's API call fails, everything already applied this run
    stays durable and won't be re-applied (and error out as "already
    exists"/404) on the next restart.
    """
    desired_by_hostname = {cfg.hostname: cfg for cfg in desired}
    routes = state["routes"]
    target = f"{tunnel['id']}.{TUNNEL_CNAME_SUFFIX}"

    # 1. Prune routes that disappeared from config -- only ever touches
    #    resources this tool itself created and recorded in state.json.
    #    Each deleted resource is cleared from the entry and persisted
    #    immediately, so a failure partway through a hostname's removal
    #    doesn't retry a delete against an already-deleted resource.
    for hostname in list(routes):
        if hostname in desired_by_hostname:
            continue
        entry = routes[hostname]
        log.info("removing route %s (no longer in config)", hostname)
        if entry.get("dns_record_id"):
            client.delete_dns_record(entry["zone_id"], entry["dns_record_id"])
            entry["dns_record_id"] = None
            persist()
        if entry.get("access_app_id"):
            client.delete_access_app(entry["access_app_id"])
            entry["access_app_id"] = None
            persist()
        del routes[hostname]
        persist()

    # 2. Create or update every desired route. Every route always gets its
    #    own Access app -- "public" is an explicit bypass policy (see
    #    cf_client._access_policy_body), not the absence of an app -- so a
    #    hostname can never fall back to some other, unrelated Access
    #    application already on the account (e.g. an account-wide wildcard
    #    app) that this tool doesn't know about and doesn't manage.
    for hostname, cfg in desired_by_hostname.items():
        prev = routes.get(hostname)

        if prev is None:
            zone = match_zone(hostname, zones)
            dns_record_id = client.create_dns_record(zone.id, hostname, target)
            log.info("created DNS record for %s in zone %s", hostname, zone.name)
            # Persist right away: if Access app creation below fails, a
            # retry must back-fill the app rather than re-create this DNS
            # record (which already exists on Cloudflare now).
            routes[hostname] = {
                "zone_id": zone.id,
                "dns_record_id": dns_record_id,
                "access_app_id": None,
                "access_policy_id": None,
                "authusers": list(cfg.authusers),
            }
            persist()
            prev = routes[hostname]

        zone_id = prev["zone_id"]
        dns_record_id = prev["dns_record_id"]
        prev_authusers = prev.get("authusers")
        access_app_id = prev.get("access_app_id")
        access_policy_id = prev.get("access_policy_id")

        if access_app_id is None:
            # New route, or an existing one created before every hostname
            # got its own app -- back-fill it now.
            access_app_id = client.create_access_app(hostname)
            access_policy_id = client.create_access_policy(access_app_id, hostname, cfg.authusers)
            log.info(
                "created Access app+policy for %s (%s)",
                hostname, "protected" if cfg.authusers else "public",
            )
        elif list(cfg.authusers) != prev_authusers:
            client.update_access_policy(access_app_id, access_policy_id, hostname, cfg.authusers)
            log.info(
                "updated Access policy for %s (%s)",
                hostname, "protected" if cfg.authusers else "public",
            )

        routes[hostname] = {
            "zone_id": zone_id,
            "dns_record_id": dns_record_id,
            "access_app_id": access_app_id,
            "access_policy_id": access_policy_id,
            "authusers": list(cfg.authusers),
        }
        persist()


def reconcile_path_scopes(
    client: CloudflareClient,
    desired: list[HostnameConfig],
    state: dict,
    persist: Callable[[], None] = _NO_PERSIST,
) -> None:
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
        persist()

    # 2. Create or update every desired path scope. Every entry here always
    #    has authusers (config.py rejects a path-scoped entry without them).
    for key, cfg in desired_by_key.items():
        prev = path_scopes.get(key)
        access_app_id = prev.get("access_app_id") if prev else None
        access_policy_id = prev.get("access_policy_id") if prev else None
        prev_authusers = prev.get("authusers") if prev else None

        if access_app_id is None:
            access_app_id = client.create_access_app(key)
            # Persist right away: if policy creation below fails, a retry
            # must back-fill the policy rather than create a second,
            # orphaned Access app for this path scope.
            path_scopes[key] = {"access_app_id": access_app_id, "access_policy_id": None, "authusers": []}
            persist()

        if access_policy_id is None:
            access_policy_id = client.create_access_policy(access_app_id, key, cfg.authusers)
            log.info("created Access app+policy for path scope %s", key)
        elif list(cfg.authusers) != prev_authusers:
            client.update_access_policy(access_app_id, access_policy_id, key, cfg.authusers)
            log.info("updated Access policy for path scope %s", key)

        path_scopes[key] = {
            "access_app_id": access_app_id,
            "access_policy_id": access_policy_id,
            "authusers": list(cfg.authusers),
        }
        persist()


def render_local_config(tunnel: dict, credentials_path: str, desired: list[HostnameConfig]) -> str:
    return ingress.render(tunnel["id"], credentials_path, desired)
