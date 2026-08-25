FROM ghcr.io/chabo-project/hf-chat-ui:{{TAG}}

USER root
COPY custom_startup.sh /usr/local/bin/custom_startup.sh
RUN chmod +x /usr/local/bin/custom_startup.sh

WORKDIR /app

USER user
EXPOSE 3000
CMD ["/usr/local/bin/custom_startup.sh"]
