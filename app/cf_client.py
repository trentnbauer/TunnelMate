"""Thin wrapper around the Cloudflare API v4 endpoints this tool needs.

Only the calls actually used by reconcile.py are implemented -- this is not
a general-purpose Cloudflare SDK.
"""

from __future__ import annotations

import base64
import os

import requests

from .zones import Zone

API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareAPIError(RuntimeError):
    def __init__(self, response: requests.Response):
        self.status_code = response.status_code
        self.body = response.text
        super().__init__(f"Cloudflare API error {response.status_code}: {response.text}")


class CloudflareClient:
    def __init__(self, api_token: str, account_id: str, session: requests.Session | None = None):
        self.account_id = account_id
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        response = self._session.request(method, f"{API_BASE}{path}", timeout=30, **kwargs)
        if not response.ok:
            raise CloudflareAPIError(response)
        payload = response.json()
        if not payload.get("success", False):
            raise CloudflareAPIError(response)
        return payload

    # -- Zones -----------------------------------------------------------

    def list_zones(self) -> list[Zone]:
        zones: list[Zone] = []
        page = 1
        while True:
            payload = self._request("GET", "/zones", params={"per_page": 50, "page": page})
            zones.extend(Zone(id=z["id"], name=z["name"]) for z in payload["result"])
            total_pages = payload.get("result_info", {}).get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
        return zones

    # -- Tunnel ------------------------------------------------------------

    def create_tunnel(self, name: str) -> dict:
        tunnel_secret = base64.b64encode(os.urandom(32)).decode("ascii")
        payload = self._request(
            "POST",
            f"/accounts/{self.account_id}/cfd_tunnel",
            json={"name": name, "config_src": "local", "tunnel_secret": tunnel_secret},
        )
        result = payload["result"]
        return {
            "id": result["id"],
            "name": name,
            "account_tag": result.get("account_tag", self.account_id),
            "tunnel_secret": tunnel_secret,
        }

    # -- DNS -----------------------------------------------------------------

    def create_dns_record(self, zone_id: str, hostname: str, target: str) -> str:
        payload = self._request(
            "POST",
            f"/zones/{zone_id}/dns_records",
            json={"type": "CNAME", "name": hostname, "content": target, "ttl": 1, "proxied": True},
        )
        return payload["result"]["id"]

    def delete_dns_record(self, zone_id: str, record_id: str) -> None:
        self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")

    # -- Access ------------------------------------------------------------

    def create_access_app(self, hostname: str) -> str:
        payload = self._request(
            "POST",
            f"/accounts/{self.account_id}/access/apps",
            json={
                "name": hostname,
                "domain": hostname,
                "type": "self_hosted",
                "session_duration": "24h",
            },
        )
        return payload["result"]["id"]

    def delete_access_app(self, app_id: str) -> None:
        # Deleting the app cascades to its app-scoped policies.
        self._request("DELETE", f"/accounts/{self.account_id}/access/apps/{app_id}")

    def create_access_policy(self, app_id: str, hostname: str, accesstype: str, authusers: tuple) -> str:
        body = _access_policy_body(hostname, accesstype, authusers)
        payload = self._request(
            "POST",
            f"/accounts/{self.account_id}/access/apps/{app_id}/policies",
            json=body,
        )
        return payload["result"]["id"]

    def update_access_policy(
        self, app_id: str, policy_id: str, hostname: str, accesstype: str, authusers: tuple
    ) -> None:
        body = _access_policy_body(hostname, accesstype, authusers)
        self._request(
            "PUT",
            f"/accounts/{self.account_id}/access/apps/{app_id}/policies/{policy_id}",
            json=body,
        )


def _access_policy_body(hostname: str, accesstype: str, authusers: tuple) -> dict:
    if accesstype == "bypass":
        return {"name": f"{hostname}-bypass", "decision": "bypass", "include": [{"everyone": {}}]}
    if accesstype == "auth":
        return {
            "name": f"{hostname}-auth",
            "decision": "allow",
            "include": [{"email": {"email": email}} for email in authusers],
        }
    raise ValueError(f"no Access policy needed for accesstype={accesstype!r}")
