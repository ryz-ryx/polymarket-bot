#!/command/with-contenv sh
# Runs before 01-hermes-setup (lexical s6-overlay cont-init.d ordering).
# Seeds our config.yaml into the persistent volume ONLY on first boot --
# never overwrites it once it exists, so later in-app config changes
# (model swaps, mcp_servers edits via `hermes config`) survive restarts.
set -eu
HOME_DIR="${HERMES_HOME:-/opt/data}"
mkdir -p "$HOME_DIR"
if [ ! -f "$HOME_DIR/config.yaml" ]; then
    cp /opt/hermes-template/config.yaml "$HOME_DIR/config.yaml"
fi
