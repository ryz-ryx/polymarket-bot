#!/command/with-contenv sh
# Runs after 01-hermes-setup/02-reconcile-profiles (lexical ordering: 03- >
# those). Launches healthcheck.py detached in the background rather than
# wiring it into Hermes's own s6-rc service tree (/etc/s6-overlay/s6-rc.d) --
# that tree is generated/owned by the base image, and mis-registering a new
# s6-rc service (wrong type/contents.d wiring) fails closed and can break
# container boot entirely. A detached background process from cont-init.d is
# lower-risk: cont-init.d scripts run to completion before s6-rc services
# start, and a backgrounded+disowned child survives past this script's own
# exit, reparented to PID 1, for the life of the container.
set -eu
nohup python3 /opt/hermes-template/healthcheck.py >> /opt/data/healthcheck.log 2>&1 &
disown || true
