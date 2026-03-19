#!/bin/sh
set -e

mc alias set local http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"

mc mb --ignore-existing "local/$MINIO_BUCKET_VIDEOS"
mc mb --ignore-existing "local/$MINIO_BUCKET_FRAMES"

# Allow public read for frames (presigned URLs still work with private)
mc anonymous set download "local/$MINIO_BUCKET_FRAMES"

echo "MinIO buckets initialized: $MINIO_BUCKET_VIDEOS, $MINIO_BUCKET_FRAMES"
