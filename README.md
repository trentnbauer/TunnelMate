# TunnelMate

IaaC Cloudflare Tunnel solution for docker-compose files.

A single Docker container that bundles `cloudflared` itself and a small
Python controller that creates a Cloudflare Tunnel and its hostnames
straight from environment variables you set on the container.

## Design principles

- **One container, not two.** `cloudflared` runs inside this container
  (as PID 1, via `exec`) instead of a separate container you have to wire
  up and keep in sync.
- **No web UI required.** Every setting comes from environment variables
  on this container's own `docker-compose.yml` service block. There's
  nothing to click through before it works.
- **No Docker-socket access, no label scanning.** This tool never reads
  Docker labels off other containers and never touches the Docker socket
  at all, so it can't accidentally pick up or reconfigure a container it
  wasn't meant to manage. It only ever creates, updates, or deletes the
  Cloudflare DNS records and Access applications it created itself,
  tracked in a local `state.json`.

The trade-off: because it only reads its own environment, hostnames only
update when the container is recreated (e.g. `docker compose up -d`), not
live as other containers start/stop.

## How it works

1. On startup, the controller reads `HOSTNAME_N` / `SERVICE_N`
   / `ACCESS_N` / `AUTH_USERS_N` variables (indexed from `1`,
   stopping at the first gap).
2. It creates a Cloudflare Tunnel via the API on first run (or reuses the
   one it created previously, persisted in the `/data` volume).
3. For each hostname it creates a proxied DNS `CNAME` record pointing at
   the tunnel, and — if `accesstype` is `bypass` or `auth` — a Cloudflare
   Access application + policy.
4. It writes a local `cloudflared` `config.yaml` with the ingress rules
   and `exec`s into `cloudflared tunnel run`, which becomes the
   container's PID 1.
5. On every subsequent start, it diffs the desired hostnames against what
   it created last time (`state.json`) and creates/updates/deletes only
   what changed — see "Removing a hostname" below.

## Configuration

Set these under the `tunnel` service's `environment:` block in
`docker-compose.yml` — it ships with a commented example already filled in:

| Variable | Required | Description |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | yes | Scoped API token, see permissions below |
| `CLOUDFLARE_ACCOUNT_ID` | yes | Your Cloudflare account ID (dashboard sidebar) |
| `NAME` | yes | Display name for the tunnel |
| `HOSTNAME_N` | yes, per hostname | Public FQDN, e.g. `app.example.com` |
| `SERVICE_N` | yes, per hostname | Where traffic for that hostname is sent, e.g. `http://app:3000` (the tunnel origin, not a URL path filter) |
| `ACCESS_N` | yes, per hostname | `public`, `bypass`, or `auth` (see below) |
| `AUTH_USERS_N` | only if `ACCESS_N=auth` | Comma-separated emails allowed to log in |

`N` starts at `1` and must be sequential with no gaps — if `HOSTNAME_2`
is missing, `HOSTNAME_3` and beyond are never read.

### Access types

- **`public`** — no Cloudflare Access application is created at all; the
  hostname is only protected by whatever the tunnel exposes.
- **`bypass`** — creates a Cloudflare Access application with a `bypass`
  policy. Useful to exempt a hostname from a broader/wildcard Access
  policy on the same zone while still tracking it in Zero Trust.
- **`auth`** — creates a Cloudflare Access application with an `allow`
  policy restricted to the emails in `AUTH_USERS_N`.

Login for `auth` hostnames uses Cloudflare Access's built-in email
one-time-PIN login. That's an account-wide Zero Trust setting
(**Settings → Authentication → Login methods → One-time PIN**), not
something this tool configures — enable it once in your Cloudflare Zero
Trust dashboard if it isn't already.

### Required API token permissions

Create a scoped token (**My Profile → API Tokens → Create Token**) with:

- **Account → Cloudflare Tunnel → Edit**
- **Account → Access: Apps and Policies → Edit**
- **Zone → DNS → Edit** (for the zone(s)/domains you'll use, or all zones)
- **Zone → Zone → Read** (needed to auto-detect which zone each hostname belongs to)

## Usage

Edit the `environment:` block in `docker-compose.yml` with your token,
account ID, and hostnames, then:

```bash
docker compose up -d --build
```

`docker-compose.yml` includes a demo `whoami` service so you can see the
tunnel working end to end before pointing it at real apps — replace it
with your own services and update `SERVICE_N` to use their compose
service names.

## Removing a hostname

Delete its `HOSTNAME_N`/`SERVICE_N`/`ACCESS_N`
variables (re-sequencing the remaining ones so there's no gap) and recreate
the container. The controller will delete the DNS record and Access
application it created for that hostname — it never touches anything it
didn't create itself.

## Persisting the tunnel

The tunnel's identity (`credentials.json`) and `state.json` live in the
`tunnel-data` named volume. **If that volume is lost, the tunnel's secret
is gone with it** — you'll need to delete the orphaned tunnel from the
Cloudflare dashboard (**Zero Trust → Networks → Tunnels**) and let the
controller create a new one. Back up or avoid pruning this volume.

## Development

```bash
pip install -r requirements.txt pytest
pytest
```

Tests cover the pure logic (env-var parsing, zone matching, ingress
rendering, and the create/update/delete reconciliation decisions) against
a fake Cloudflare client — no live API calls or credentials needed.
