"""Auto-detect which Cloudflare zone a hostname belongs to.

Given the zones the API token can see, pick the longest matching suffix so
that both apex domains and delegated subdomains resolve correctly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Zone:
    id: str
    name: str


class NoMatchingZoneError(ValueError):
    pass


def match(hostname: str, zones: list[Zone]) -> Zone:
    candidates = [
        zone for zone in zones if hostname == zone.name or hostname.endswith("." + zone.name)
    ]
    if not candidates:
        raise NoMatchingZoneError(
            f"no zone accessible by this API token matches hostname {hostname!r}"
        )
    return max(candidates, key=lambda z: len(z.name))
