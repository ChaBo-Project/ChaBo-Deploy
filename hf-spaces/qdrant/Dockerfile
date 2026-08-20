# Start from the official Qdrant image
FROM qdrant/qdrant:v1.17.1

# We switch to root temporarily to install dependencies and copy files,
# then revert to the 'qdrant' user for execution.
USER root

# Install necessary Python dependencies for data loading, embedding, and Gradio
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# 1. Copy the requirements file into a temporary location
COPY requirements.txt /tmp/


# 2. Install Python packages required by initialize_qdrant.py AND app.py
# Added --break-system-packages to resolve the externally-managed-environment error (PEP 668)
# We now install from the requirements.txt file.
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt


# Create a directory for our scripts
RUN mkdir -p /app

# Copy the initialization script, the startup script, and the new Gradio app
COPY initialize_qdrant.py /app/
COPY start.sh /usr/local/bin/
COPY app.py /app/

# Make the startup script executable
# RUN chmod +x /usr/local/bin/start.sh

RUN mkdir -p /tmp/storage \
           /tmp/snapshots \
           /tmp/hf_cache && \
    # 2. Set executable flag for the main startup script
    chmod +x /usr/local/bin/start.sh && \
    # 3. Set full permissions on all required directories
    chmod -R 777 /tmp/storage /tmp/snapshots /tmp/hf_cache /app

# Environment variables for Qdrant configuration
ENV QDRANT__STORAGE__STORAGE_PATH="/tmp/storage"
ENV QDRANT__STORAGE__SNAPSHOTS_PATH="/tmp/snapshots"
# Use QDRANT__SERVICE__HOST to ensure Qdrant binds to 0.0.0.0
ENV QDRANT__SERVICE__HOST="0.0.0.0"
ENV QDRANT_HOST="127.0.0.1"
ENV QDRANT_PORT="6333"
# Added to insure the cache is accesible by initialization script as it downloads the dataset
ENV HF_HOME="/tmp/hf_cache"

# Set the custom entrypoint to run our initialization logic before starting the main server
ENTRYPOINT ["/usr/local/bin/start.sh"]

# Expose the default Qdrant ports (HTTP/REST and gRPC) and the Gradio port
EXPOSE 6333
EXPOSE 6334
# This is Gradio port
EXPOSE 7860
