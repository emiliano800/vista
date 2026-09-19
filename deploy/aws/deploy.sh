#!/usr/bin/env bash
# Build the API image, push it to ECR and create/update the Vista CloudFormation stack.
#
#   deploy/aws/deploy.sh                 # build + push + deploy
#   deploy/aws/deploy.sh --no-build      # redeploy the stack with the last pushed image
#   deploy/aws/deploy.sh --outputs       # only print stack outputs
#
# Configuration (environment variables; copy deploy/aws/.env.example to deploy/aws/.env):
#   AWS_REGION / AWS_PROFILE      standard AWS CLI settings
#   STACK_NAME                    default "vista"
#   VPC_ID, SUBNET_IDS            default: the account's default VPC and all its public subnets
#   OPENAI_API_KEY, PROVISIONING_KEY, ALLOWED_ORIGINS, WORKER_DESIRED_COUNT, DB_DELETION_PROTECTION
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -f deploy/aws/.env ]; then
  set -a; . deploy/aws/.env; set +a
fi

STACK_NAME=${STACK_NAME:-vista}
ECR_REPO=${ECR_REPO:-$STACK_NAME-api}
BUILD=true
ONLY_OUTPUTS=false
for arg in "$@"; do
  case $arg in
    --no-build) BUILD=false ;;
    --outputs) ONLY_OUTPUTS=true ;;
    *) echo "unknown argument: $arg" >&2; exit 64 ;;
  esac
done

command -v aws >/dev/null || { echo "aws CLI not found: brew install awscli" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker not found" >&2; exit 1; }

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_REGION:-$(aws configure get region || true)}
[ -n "$REGION" ] || { echo "Set AWS_REGION (e.g. export AWS_REGION=us-east-1)" >&2; exit 1; }
export AWS_REGION=$REGION

outputs() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
}

if $ONLY_OUTPUTS; then outputs; exit 0; fi

# ---------------------------------------------------------------- networking
if [ -z "${VPC_ID:-}" ]; then
  VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query "Vpcs[0].VpcId" --output text)
  [ "$VPC_ID" != "None" ] || { echo "No default VPC in $REGION; set VPC_ID and SUBNET_IDS" >&2; exit 1; }
fi
if [ -z "${SUBNET_IDS:-}" ]; then
  SUBNET_IDS=$(aws ec2 describe-subnets \
    --filters Name=vpc-id,Values="$VPC_ID" Name=map-public-ip-on-launch,Values=true \
    --query "Subnets[].SubnetId" --output text | tr '\t' ',')
fi
[ -n "$SUBNET_IDS" ] || { echo "No public subnets found in $VPC_ID; set SUBNET_IDS" >&2; exit 1; }
echo "account=$ACCOUNT_ID region=$REGION vpc=$VPC_ID subnets=$SUBNET_IDS"

# ------------------------------------------------------------------- image
REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
aws ecr describe-repositories --repository-names "$ECR_REPO" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "$ECR_REPO" \
    --image-scanning-configuration scanOnPush=true --image-tag-mutability MUTABLE >/dev/null

if $BUILD; then
  TAG=$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then TAG="$TAG-dirty"; fi
  aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY"
  # Express Mode runs X86_64 tasks; build for that platform even on Apple Silicon.
  docker buildx build --platform linux/amd64 --provenance=false \
    -t "$REGISTRY/$ECR_REPO:$TAG" -t "$REGISTRY/$ECR_REPO:latest" --push .
  IMAGE_URI="$REGISTRY/$ECR_REPO@$(aws ecr describe-images --repository-name "$ECR_REPO" \
    --image-ids imageTag="$TAG" --query "imageDetails[0].imageDigest" --output text)"
else
  IMAGE_URI="$REGISTRY/$ECR_REPO@$(aws ecr describe-images --repository-name "$ECR_REPO" \
    --image-ids imageTag=latest --query "imageDetails[0].imageDigest" --output text)"
fi
echo "image=$IMAGE_URI"

# ------------------------------------------------------------------- stack
# SubnetIds is a List<> parameter: commas inside the value must be escaped for --parameter-overrides.
PARAMS=(
  "ImageUri=$IMAGE_URI"
  "VpcId=$VPC_ID"
  "SubnetIds=${SUBNET_IDS//,/\\,}"
  "WorkerDesiredCount=${WORKER_DESIRED_COUNT:-0}"
  "DBDeletionProtection=${DB_DELETION_PROTECTION:-true}"
)
[ -n "${ALLOWED_ORIGINS:-}" ] && PARAMS+=("AllowedOrigins=$ALLOWED_ORIGINS")
[ -n "${OPENAI_API_KEY:-}" ] && PARAMS+=("OpenAIApiKey=$OPENAI_API_KEY")
[ -n "${PROVISIONING_KEY:-}" ] && PARAMS+=("ProvisioningKey=$PROVISIONING_KEY")

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file deploy/aws/template.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides "${PARAMS[@]}"

# ECS Exec is a service-level switch that the Express resource does not expose; turn it on
# so `aws ecs execute-command` works against the API tasks (task role already allows it).
CLUSTER=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" --output text)
aws ecs update-service --cluster "$CLUSTER" --service "$STACK_NAME-api" --enable-execute-command >/dev/null || true

outputs
ENDPOINT=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text)
case $ENDPOINT in https://*) ;; *) ENDPOINT="https://${ENDPOINT#http://}" ;; esac
ENDPOINT=${ENDPOINT%/}
cat <<EOF

Next:
  1. Wait for the service to settle:  aws ecs wait services-stable --cluster $CLUSTER --services $STACK_NAME-api
  2. Check health:                    curl -s $ENDPOINT/api/health
  3. Provision the first workspace:   deploy/aws/manage.sh create-workspace --firm "Your firm" --company "Your company" --email you@example.com
  4. Cloudflare Worker "vista" -> Settings -> Variables: API_ORIGIN = $ENDPOINT
EOF
