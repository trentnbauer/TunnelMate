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

1. On startup, the controller reads `HOSTNAME_N` / `SERVICE_N` / `USERS_N`
   variables (indexed from `1`, stopping at the first gap).
2. It creates a Cloudflare Tunnel via the API on first run (or reuses the
   one it created previously, persisted in the `/data` volume).
3. For each hostname it creates a proxied DNS `CNAME` record pointing at
   the tunnel, and — if `USERS_N` is set — a Cloudflare Access application
   + policy restricted to those emails.
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
| `HOSTNAME_N` | yes, per hostname | Public FQDN, e.g. `app.example.com`, or `app.example.com/admin` to scope just that sub-path (see below) |
| `SERVICE_N` | yes, unless `HOSTNAME_N` has a path | Where traffic for that hostname is sent, e.g. `http://app:3000` (the tunnel origin, not a URL path filter) |
| `USERS_N` | no | Comma-separated emails. Set it to require login for that hostname/path; leave it unset to keep it public |

`N` starts at `1` and must be sequential with no gaps — if `HOSTNAME_2`
is missing, `HOSTNAME_3` and beyond are never read.

### Public vs protected

Whether a hostname needs login is inferred from `USERS_N`, not a separate
flag: leave it unset for public, set it for a Cloudflare Access application
+ policy restricted to those emails. Login uses Cloudflare Access's
built-in email one-time-PIN login — an account-wide Zero Trust setting
(**Settings → Authentication → Login methods → One-time PIN**), not
something this tool configures — enable it once in your Cloudflare Zero
Trust dashboard if it isn't already.

### Protecting just a sub-path

`HOSTNAME_N` can include a path, e.g. `HOSTNAME_2=app.example.com/admin`,
to lock down only that sub-path of an app that's otherwise public. A
path-scoped entry:

- must have a matching plain-hostname entry elsewhere in the config (the
  one that actually routes `app.example.com`, e.g. `HOSTNAME_1`) — it
  reuses that entry's DNS record and tunnel ingress rule rather than
  creating its own
- must not set `SERVICE_N` (there's nothing new to route)
- must set `USERS_N` (a public sub-path doesn't need its own entry)

See the `HOSTNAME_2`/`USERS_2` pair in `docker-compose.yml` for a working
example: `whoami.example.com` is fully public, but
`whoami.example.com/admin` requires login.

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
service names. For a more realistic setup with data stores that should
never be reachable through the tunnel, see
[`examples/queueup/docker-compose.yml`](examples/queueup/docker-compose.yml)
and "Example: network-segmented QueueUp deployment" below.

## Removing a hostname

Delete its `HOSTNAME_N`/`SERVICE_N`/`USERS_N` variables (re-sequencing the
remaining ones so there's no gap) and recreate the container. The
controller will delete the DNS record and Access application it created
for that hostname (or, for a path-scoped entry, just its Access
application) — it never touches anything it didn't create itself.

## Example: network-segmented QueueUp deployment

`examples/queueup/docker-compose.yml` deploys
[QueueUp](https://github.com/trentnbauer/QueueUp) (a real, self-hosted
game backlog/voting app) with its Postgres and Redis isolated from the
tunnel on Docker's own network layer, not just an app-level firewall rule:

```
  tunnel  -- frontend --  app  -- backend --  postgres, redis
  (no route to backend at all)
```

- `tunnel` is only attached to the `frontend` network.
- `postgres` and `redis` are only attached to the `backend` network.
- `app` (QueueUp itself) is the only service on both, since it needs to
  talk to each side.

Docker only lets containers reach each other over a network they both
belong to. `tunnel` and `postgres`/`redis` never share one, so **the
tunnel container has no network path to either data store at all** — not
"blocked by a rule that could be misconfigured," genuinely absent at the
container-networking level. Even in the worst case — the tunnel container
or its Cloudflare credentials fully compromised — the attacker still has
to separately compromise `app` itself before they can reach `postgres` or
`redis`; compromising the tunnel doesn't hand them the database for free.
Neither is ever given a `HOSTNAME_N` entry, so neither is reachable
through the Cloudflare Tunnel or covered by an Access application either —
they're unreachable at both the Cloudflare layer and the Docker network
layer.

One thing worth not mixing up: `USERS_N` (TunnelMate's own config) gates
who can reach `app` *at all*, at the Cloudflare edge, before a request
ever reaches the container. QueueUp's own `ADMIN_EMAILS` is a separate,
application-level list — it decides who gets admin features (deleting
rooms/users, etc.) *after* someone has already signed in through one of
QueueUp's own auth providers (Google/Discord/Steam/OIDC). They're
independent gates at different layers, not two ways of writing the same
thing.

A couple of other security properties fall out of this setup basically for
free:

- **`backend` is `internal: true`**, meaning nothing on it — including
  `postgres` and `redis` — has a route to the internet at all. If either
  (or anything else you later add to `backend`) were compromised through
  some other channel entirely, it still couldn't phone home or pull down
  another payload.
- **No `ports:` are published on any service, including `tunnel` itself.**
  Cloudflare Tunnel works by `tunnel` making an *outbound* connection to
  Cloudflare's edge, so nothing needs to listen on a host port for inbound
  traffic to arrive. That means no port-forwarding rule on your router, no
  open inbound port on the host's firewall, and no service for a port scan
  of your public IP to find — the entire "attacker finds an open port and
  probes what's behind it" class of exposure just doesn't apply here,
  which is a meaningfully different posture than the traditional
  forward-a-port-to-your-server setup.

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
