# Security Policy

## Supported Versions

TunnelMate is pre-1.0 and follows [semver](https://semver.org) (see
"Versioning / Releases" in the README). Only the latest `v0.x` release is
supported with security fixes -- there's no long-term support for older
patch releases.

| Version | Supported |
| --- | --- |
| latest `v0.x` (`TMVer=latest` or `TMVer=v0`) | :white_check_mark: |
| older pinned `v0.x.y` releases | :x: (upgrade to the latest `v0.x`) |

## Reporting a Vulnerability

Please report security issues privately using
[GitHub's private vulnerability reporting](https://github.com/trentnbauer/TunnelMate/security/advisories/new)
(**Security** tab -> **Report a vulnerability**) rather than a public
issue. This is a single-maintainer project run on a best-effort basis --
there's no formal SLA, but reports will be acknowledged as soon as
possible and a fix or mitigation prioritized based on severity.

### Scope

In scope:

- Anything that could leak `CLOUDFLARE_API_TOKEN` or the tunnel's
  `credentials.json`/`state.json` (e.g. via logging, error messages, or
  the rendered `cloudflared` config).
- Anything that lets a `HOSTNAME_N` entry bypass or weaken its intended
  Cloudflare Access policy (public vs. restricted to `USERS_N`), or lets
  the controller touch Cloudflare resources it didn't create itself.
- Container/image issues (e.g. running as an unexpected user, an
  unnecessarily large attack surface in the published image).

Out of scope (report upstream instead):

- Vulnerabilities in `cloudflared` itself -- report to
  [Cloudflare](https://github.com/cloudflare/cloudflared/security).
- Vulnerabilities in third-party dependencies (`requests`, base images,
  etc.) -- report to the upstream project; a GitHub issue/PR here to bump
  the pinned version is also welcome.
