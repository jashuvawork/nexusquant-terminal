#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:=ap-south-1}"
: "${ECR_REPOSITORY:=nexusquant-api}"
: "${IMAGE_TAG:=latest}"

if ! command -v aws >/dev/null 2>&1; then
  echo "AWS CLI is required. Use AWS CloudShell or install awscli." >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required." >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}"

echo "Using AWS account: ${ACCOUNT_ID}"
echo "Using region: ${AWS_REGION}"
echo "Using ECR repo: ${ECR_URI}"

aws ecr describe-repositories --repository-names "${ECR_REPOSITORY}" --region "${AWS_REGION}" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "${ECR_REPOSITORY}" --region "${AWS_REGION}" >/dev/null

aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

docker build -t "${ECR_REPOSITORY}:${IMAGE_TAG}" ./backend
docker tag "${ECR_REPOSITORY}:${IMAGE_TAG}" "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:${IMAGE_TAG}"

cat <<EOF

Pushed image:
${ECR_URI}:${IMAGE_TAG}

Use this image URI in ECS/Fargate task definition.
EOF
