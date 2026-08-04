"""Parse and validate hostname configuration from environment variables.

Config is entirely env-var driven, indexed and sequential starting at 1
(``TUNNEL_HOSTNAME_1``, ``TUNNEL_HOSTNAME_2``, ...). Indexing stops at the
first missing index. This is the tunnel container's OWN environment, never
Docker labels read off other containers.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

VALID_ACCESS_TYPES = ("public", "bypass", "auth")
VALID_SERVICE_SCHEMES = ("http", "https", "tcp")


class ConfigError(ValueError):
    """Raised for any invalid or incomplete configuration, with an actionable message."""


@dataclass(frozen=True)
class HostnameConfig:
    index: int
    hostname: str
    service: str
    accesstype: str
    authusers: tuple[str, ...]


def _require(env: dict, name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"required environment variable {name!r} is missing or empty")
    return value


def _validate_service(name: str, value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in VALID_SERVICE_SCHEMES or not parts.netloc:
        raise ConfigError(
            f"{name}={value!r} must be a {'/'.join(VALID_SERVICE_SCHEMES)} URL, e.g. http://app:3000"
        )
    return value


def parse_hostnames(env: dict) -> list[HostnameConfig]:
    """Parse ``TUNNEL_*_{N}`` variables into an ordered list of HostnameConfig.

    Raises ConfigError on any missing/invalid/duplicate entry.
    """
    configs: list[HostnameConfig] = []
    seen_hostnames: dict[str, int] = {}

    index = 1
    while f"TUNNEL_HOSTNAME_{index}" in env:
        hostname = _require(env, f"TUNNEL_HOSTNAME_{index}")
        service = _validate_service(
            f"TUNNEL_SERVICE_{index}", _require(env, f"TUNNEL_SERVICE_{index}")
        )
        accesstype = _require(env, f"TUNNEL_ACCESS_{index}").lower()
        if accesstype not in VALID_ACCESS_TYPES:
            raise ConfigError(
                f"TUNNEL_ACCESS_{index}={accesstype!r} must be one of {VALID_ACCESS_TYPES}"
            )

        authusers: tuple[str, ...] = ()
        if accesstype == "auth":
            raw_users = _require(env, f"TUNNEL_AUTH_USERS_{index}")
            authusers = tuple(u.strip() for u in raw_users.split(",") if u.strip())
            if not authusers:
                raise ConfigError(
                    f"TUNNEL_AUTH_USERS_{index} must list at least one email when "
                    f"TUNNEL_ACCESS_{index}=auth"
                )

        if hostname in seen_hostnames:
            raise ConfigError(
                f"hostname {hostname!r} is configured twice (indexes "
                f"{seen_hostnames[hostname]} and {index})"
            )
        seen_hostnames[hostname] = index

        configs.append(
            HostnameConfig(
                index=index,
                hostname=hostname,
                service=service,
                accesstype=accesstype,
                authusers=authusers,
            )
        )
        index += 1

    if index == 1:
        raise ConfigError(
            "no hostnames configured: set TUNNEL_HOSTNAME_1 / TUNNEL_SERVICE_1 / "
            "TUNNEL_ACCESS_1 (and TUNNEL_AUTH_USERS_1 if access=auth) at minimum"
        )

    return configs


@dataclass(frozen=True)
class GlobalConfig:
    api_token: str
    account_id: str
    tunnel_name: str


def parse_global(env: dict) -> GlobalConfig:
    return GlobalConfig(
        api_token=_require(env, "CLOUDFLARE_API_TOKEN"),
        account_id=_require(env, "CLOUDFLARE_ACCOUNT_ID"),
        tunnel_name=_require(env, "TUNNEL_NAME"),
    )
