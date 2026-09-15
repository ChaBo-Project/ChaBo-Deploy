FROM ghcr.io/chabo-project/chabo-chatui-db:{{TAG}}

USER root
COPY custom_startup.sh /usr/local/bin/custom_startup.sh
RUN chmod +x /usr/local/bin/custom_startup.sh
COPY --chown=1000 PRIVACY.md /app/PRIVACY.md

WORKDIR /app

USER user
EXPOSE 3000
CMD ["/usr/local/bin/custom_startup.sh"]
