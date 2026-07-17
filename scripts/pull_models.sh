#!/bin/bash
# Shell script to seed/pull Ollama models inside docker containers with a distributed mapping.
# Usage: ./pull_models.sh

declare -A MODEL_MAPPING
MODEL_MAPPING["ollama-1"]="gemma2:2b"
MODEL_MAPPING["ollama-2"]="gemma2:2b"
MODEL_MAPPING["ollama-3"]="tinyllama:latest"

echo "=========================================="
echo "Ollama Model Downloader/Seeder"
echo "=========================================="

for container in "${!MODEL_MAPPING[@]}"; do
    model=${MODEL_MAPPING[$container]}
    # Check if container is running
    running=$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null)
    if [ "$running" != "true" ]; then
        echo "[WARNING] Container $container is not running. Skipping."
        continue
    fi

    echo "Pulling '$model' on '$container' (this may take a few minutes)..."
    docker exec "$container" ollama pull "$model"
    if [ $? -eq 0 ]; then
        echo "[SUCCESS] Successfully pulled '$model' on '$container'."
    else
        echo "[ERROR] Failed to pull '$model' on '$container'."
    fi
done

echo "=========================================="
echo "Model pull process completed."
