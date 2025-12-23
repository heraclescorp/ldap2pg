#!/bin/bash
set -e
cd "${0%/*}"

IMAGE_NAME="199658938451.dkr.ecr.us-east-2.amazonaws.com/ldap2pg:latest"

echo 'Logging into AWS ECR'
aws ecr get-login-password --region us-east-2 $HERACLES_AWS_PROFILE | docker login --username AWS --password-stdin $IMAGE_NAME

echo "Building image $IMAGE_NAME"
docker build --platform=linux/amd64 -t "$IMAGE_NAME" -f ./docker/Dockerfile .

echo "Pushing image $IMAGE_NAME"
docker push "$IMAGE_NAME"
