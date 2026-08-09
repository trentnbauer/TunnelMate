#!/bin/sh
# Runs as root (the image's default) so it can fix up ownership of the
# mounted /data volume -- which Docker creates owned by root on first use,
# and which may already be root-owned from a container built before this
# entrypoint existed -- then drops to the unprivileged `tunnelmate` user
# for everything else, including the cloudflared process app.main execs
# into.
set -e
chown -R tunnelmate:tunnelmate /data
exec gosu tunnelmate python -m app.main
