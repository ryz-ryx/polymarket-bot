FROM nousresearch/hermes-agent:latest

COPY config.yaml /opt/hermes-template/config.yaml
COPY --chmod=0755 seed-config.sh /etc/cont-init.d/00-seed-config

# Platform liveness sidecar (see healthcheck.py for design/rationale) --
# runs detached in the background, checks Telegram/Discord every 10 min
# using Hermes's own token env vars, and alerts on failure.
COPY healthcheck.py /opt/hermes-template/healthcheck.py
COPY --chmod=0755 start-healthcheck.sh /etc/cont-init.d/03-start-healthcheck

# Official docker-compose.yml runs the gateway service as `["gateway", "run"]`
# -- bare CMD would launch the interactive TUI instead of the supervised
# Discord/Telegram/etc. gateway.
CMD ["gateway", "run"]
