"""Parse and validate hostname configuration from environment variables.

Config is entirely env-var driven, indexed and sequential starting at 1
(``HOSTNAME_1``, ``HOSTNAME_2``, ...). Indexing stops at the
first missing index. This is the tunnel container's OWN environment, never
Docker labels read off other containers.

Whether a hostname is public or requires login is inferred from whether
``USERS_N`` is set -- there is no separate access-type field.

``HOSTNAME_N`` may optionally include a path (e.g. ``app.example.com/admin``)
to protect just that sub-path of an app that's otherwise routed publicly by
another index for the same bare hostname. A path-scoped entry reuses that
route's DNS record and tunnel ingress rule -- it only adds a Cloudflare
Access policy scoped to the path, so it always requires USERS_N (there's no
point creating one that's public, since the sub-path is already reachable
via the base route).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

VALID_SERVICE_SCHEMES = ("http", "https", "tcp")


class ConfigError(ValueError):
    """Raised for any invalid or incomplete configuration, with an actionable message."""


@dataclass(frozen=True)
class HostnameConfig:
    index: int
    hostname: str
    path: str | None  # None => whole-hostname route; set => path-scoped Access overlay
    service: str | None  # required iff path is None
    authusers: tuple[str, ...]

    @property
    def is_route(self) -> bool:
        return self.path is None

    @property
    def scope_key(self) -> str:
        return self.hostname if self.path is None else f"{self.hostname}{self.path}"


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
    # urlsplit() already lowercases parts.scheme for the check above, but
    # returning `value` unchanged left the original casing in place --
    # ingress.py's noTLSVerify decision does a case-sensitive check for
    # "https://", so SERVICE_N=HTTPS://... validated fine here but silently
    # skipped noTLSVerify downstream. Normalize once, at the source.
    scheme, sep, rest = value.partition("://")
    return f"{scheme.lower()}{sep}{rest}"


def _normalize_hostname(host: str) -> str:
    # DNS hostnames are case-insensitive; lowercase so e.g. App.example.com
    # and app.example.com are recognized as the same host instead of
    # slipping past duplicate detection and hitting a raw Cloudflare API
    # error later. A single trailing "." (the absolute-FQDN root label,
    # e.g. from a `dig`/`nslookup` copy-paste) is the same hostname too --
    # without stripping it, zone matching fails outright, since Cloudflare
    # zone names never include the trailing dot.
    host = host.lower()
    return host[:-1] if host.endswith(".") else host


def _split_hostname(name: str, raw: str) -> tuple[str, str | None]:
    # The path (if any) is left as-is -- origins may route it case-sensitively.
    if "/" not in raw:
        return _normalize_hostname(raw), None
    host, _, rest = raw.partition("/")
    if not host:
        raise ConfigError(f"{name}={raw!r} is missing a hostname before the '/'")
    return _normalize_hostname(host), f"/{rest}"


def parse_hostnames(env: dict) -> list[HostnameConfig]:
    """Parse the indexed ``*_{N}`` variables into an ordered list of HostnameConfig.

    Raises ConfigError on any missing/invalid/duplicate entry.
    """
    configs: list[HostnameConfig] = []
    route_hostnames: dict[str, int] = {}
    path_scopes: dict[tuple[str, str], int] = {}

    index = 1
    while f"HOSTNAME_{index}" in env:
        hostname, path = _split_hostname(f"HOSTNAME_{index}", _require(env, f"HOSTNAME_{index}"))

        raw_users = env.get(f"USERS_{index}", "").strip()
        authusers = tuple(u.strip() for u in raw_users.split(",") if u.strip())

        service_raw = env.get(f"SERVICE_{index}", "").strip()
        if path is None:
            if not service_raw:
                raise ConfigError(f"required environment variable 'SERVICE_{index}' is missing or empty")
            service = _validate_service(f"SERVICE_{index}", service_raw)

            if hostname in route_hostnames:
                raise ConfigError(
                    f"hostname {hostname!r} already has a route (index "
                    f"{route_hostnames[hostname]}); index {index} duplicates it"
                )
            route_hostnames[hostname] = index
        else:
            if service_raw:
                raise ConfigError(
                    f"SERVICE_{index} must not be set when HOSTNAME_{index} includes a path "
                    f"-- path-scoped entries reuse the base hostname's existing route"
                )
            if not authusers:
                raise ConfigError(
                    f"USERS_{index} is required when HOSTNAME_{index} includes a path "
                    f"-- a public sub-path doesn't need its own entry"
                )
            service = None

            key = (hostname, path)
            if key in path_scopes:
                raise ConfigError(
                    f"hostname {hostname!r} path {path!r} is configured twice (indexes "
                    f"{path_scopes[key]} and {index})"
                )
            path_scopes[key] = index

        configs.append(
            HostnameConfig(
                index=index,
                hostname=hostname,
                path=path,
                service=service,
                authusers=authusers,
            )
        )
        index += 1

    if index == 1:
        raise ConfigError(
            "no hostnames configured: set HOSTNAME_1 / SERVICE_1 at minimum "
            "(add USERS_1 to require login)"
        )

    for host, path in path_scopes:
        if host not in route_hostnames:
            raise ConfigError(
                f"hostname {host!r} is path-scoped (e.g. {host}{path}) but no "
                f"HOSTNAME_*/SERVICE_* entry routes that hostname"
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
        tunnel_name=_require(env, "NAME"),
    )
