#!/bin/bash
set -e

echo "Building Oyster Dashboard..."
docker build -t oyster-dashboard .

echo "Tagging for Fly.io registry..."
docker tag oyster-dashboard registry.fly.io/oyster-dashboard:latest

echo "Pushing to Fly.io registry..."
docker push registry.fly.io/oyster-dashboard:latest

echo "Deploying to Fly.io..."
fly deploy

echo "Deployment complete!"