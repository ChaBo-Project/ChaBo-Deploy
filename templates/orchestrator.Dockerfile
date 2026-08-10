FROM ghcr.io/chabo-project/chabo-rag-orchestrator:{{TAG}}

WORKDIR /app

COPY instance_config /app/instance_config
ENV INSTANCE_CONFIG_DIR=/app/instance_config

EXPOSE 7860

CMD ["python", "-u", "src/main.py"]
