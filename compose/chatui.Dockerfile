ARG CHATUI_IMAGE=ghcr.io/chabo-project/chabo-chatui-db
ARG CHATUI_TAG
FROM ${CHATUI_IMAGE}:${CHATUI_TAG}

USER root
COPY custom_startup.sh /usr/local/bin/custom_startup.sh
RUN chmod +x /usr/local/bin/custom_startup.sh
COPY --chown=1000 PRIVACY.md /app/PRIVACY.md

WORKDIR /app

USER user
CMD ["/usr/local/bin/custom_startup.sh"]
