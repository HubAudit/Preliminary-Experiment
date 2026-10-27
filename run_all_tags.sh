#!/bin/bash

TAGS=(text-classification text-generation feature-extraction image-classification image-segmentation)
REPO_TYPES=(dataset model)

for repo_type in "${REPO_TYPES[@]}"; do
    for tag in "${TAGS[@]}"; do
        echo "-- $repo_type / $tag"
        python find-formats.py --tags "$tag" --repo-type "$repo_type" --threads 3 --delay 0.3
        echo "done, sleeping 30s..."
        sleep 30  # rest between tags to ease rate limiting
    done
done

echo "running stats..."
python compute-stats.py
