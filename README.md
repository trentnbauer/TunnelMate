#### More AI slop!

# TunnelMate

IaaC Cloudflare Tunnel solution for docker-compose files.

A single Docker container that bundles `cloudflared` itself and a small
Python controller that creates a Cloudflare Tunnel and its hostnames
straight from environment variables you set on the container -- no web
UI, no Docker-socket access. Published as
[`ghcr.io/trentnbauer/tunnelmate`](https://github.com/trentnbauer/TunnelMate/pkgs/container/tunnelmate),
no local build required.

**Full docs: [the wiki](https://github.com/trentnbauer/TunnelMate/wiki)**
-- configuration reference, public vs. protected hostnames, path-scoped
protection, the network-segmented QueueUp example, versioning, backups,
and running the tests.

## Quick start

```bash
cp .env.example .env
```

Edit `.env` with your `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and
hostnames (see [Configuration](https://github.com/trentnbauer/TunnelMate/wiki/Configuration)
for what each variable does and the required token permissions). Then:

```bash
docker compose up -d
```

`docker-compose.yml` ships with a demo `whoami` service so you can see the
tunnel working end to end before pointing it at real apps -- replace it
with your own services and update `SERVICE_N` to use their compose
service names.

## Keeping cloudflared up to date

`cloudflared`'s own autoupdate is explicitly disabled inside the
container (confirmed the non-root container user can't write its own
binary anyway, so it was never going to work). Its version is pinned via
the `Dockerfile`'s `ARG CLOUDFLARED_VERSION`, and a scheduled
[workflow](.github/workflows/update-cloudflared.yml) checks weekly for a
new `cloudflared` release, bumps that pin, and cuts a new TunnelMate
version automatically. **This requires a `RELEASE_TOKEN` repo secret**
(a PAT with `contents: write`) -- see
[Versioning and Releases](https://github.com/trentnbauer/TunnelMate/wiki/Versioning-and-Releases)
for why and how to add it.

Security issues: see [SECURITY.md](SECURITY.md) -- please don't file
those as public issues.
