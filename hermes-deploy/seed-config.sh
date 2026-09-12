#!/command/with-contenv sh
# Runs before 01-hermes-setup (lexical s6-overlay cont-init.d ordering).
# Always overwrites config.yaml with our tracked template on every boot --
# this deploy's config.yaml (in git) is the single source of truth. Model/
# platform/mcp_servers changes belong in hermes-deploy/config.yaml, not made
# ad hoc inside the running container (those would be silently lost anyway
# on the next redeploy).
set -eu
HOME_DIR="${HERMES_HOME:-/opt/data}"
mkdir -p "$HOME_DIR"
cp /opt/hermes-template/config.yaml "$HOME_DIR/config.yaml"
