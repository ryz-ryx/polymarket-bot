FROM nousresearch/hermes-agent:latest

COPY config.yaml /opt/hermes-template/config.yaml
COPY --chmod=0755 seed-config.sh /etc/cont-init.d/00-seed-config

# Official docker-compose.yml runs the gateway service as `["gateway", "run"]`
# -- bare CMD would launch the interactive TUI instead of the supervised
# Discord/Telegram/etc. gateway.
CMD ["gateway", "run"]
