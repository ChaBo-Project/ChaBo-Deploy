#!/bin/bash
# --- 1. Start Qdrant Server in the background ---
echo "Starting Qdrant server, configured to listen on 0.0.0.0 via ENV variable..."
# Running the executable using its full path without arguments
/qdrant/qdrant &
QDRANT_PID=$!

# Give the Qdrant server a brief moment to start up locally
echo "Giving Qdrant a moment to initialize the core service..."
sleep 5

# --- 2. Run Initialization Script ---
# This script connects to the local Qdrant server and indexes data if the collection is missing.
echo "Running data initialization script..."
python3 /app/initialize_qdrant.py

# --- 3. Start Gradio API Server ---
# The Gradio app will connect to the local Qdrant instance and expose the API.
# It uses the default port 7860. The process will block here, keeping the container alive.
echo "Starting Gradio API server on port 7860..."
python3 /app/app.py

# If the Gradio app crashes, we wait for the Qdrant process as a fallback,
# although the intention is for Gradio to be the primary blocker.
echo "Gradio server stopped. Monitoring Qdrant process (PID: $QDRANT_PID) to keep the container alive..."
wait $QDRANT_PID
