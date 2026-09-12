#!/command/with-contenv sh
# Runs after 01-hermes-setup/02-reconcile-profiles (lexical ordering: 03- >
# those). Launches healthcheck.py detached in the background rather than
# wiring it into Hermes's own s6-rc service tree (/etc/s6-overlay/s6-rc.d) --
# that tree is generated/owned by the base image, and mis-registering a new
# s6-rc service (wrong type/contents.d wiring) fails closed and can break
# container boot entirely. A detached background process from cont-init.d is
# lower-risk: cont-init.d scripts run to completion before s6-rc services
# start, and a backgrounded child survives past this script's own exit,
# reparented to PID 1, for the life of the container.
#
# Deliberately left on inherited stdout/stderr (not redirected to a file) --
# Railway's log viewer only captures the container's own stdout/stderr
# stream, which is what all the cont-init.d and gateway logs already show
# up in. Redirecting to a file would make this sidecar invisible there
# (this was tried and confirmed silent -- fixed by removing the redirect).
#
# `disown` is a bash job-control builtin that this shell (busybox/dash
# under s6-overlay's with-contenv) does not have; nohup alone is what
# actually matters here (ignores SIGHUP), so disown is dropped rather than
# papered over with `|| true`.
set -eu
nohup python3 /opt/hermes-template/healthcheck.py &
