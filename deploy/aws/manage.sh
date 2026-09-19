#!/usr/bin/env bash
# Run `python -m vista.manage ...` inside AWS as a one-off Fargate task and print its output.
#
#   deploy/aws/manage.sh create-workspace --firm "Your firm" --company "Your company" --email you@example.com
#   deploy/aws/manage.sh add-user --tenant TENANT_UUID --company COMPANY_UUID --email employee@example.com --role member
#   deploy/aws/manage.sh rotate-key --user USER_UUID
#   deploy/aws/manage.sh migrate
#
# Uses the stack's worker task definition (same image, database secret and bucket as the API).
# Output such as a freshly issued access key is printed once here and is also in the
# /vista/<stack>/worker CloudWatch log group; treat it as sensitive.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [ -f deploy/aws/.env ]; then
  set -a; . deploy/aws/.env; set +a
fi
STACK_NAME=${STACK_NAME:-vista}
[ "$#" -gt 0 ] || { sed -n '2,8p' "$0"; exit 64; }

out() { aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
CLUSTER=$(out ClusterName)
TASK_DEF=$(out WorkerTaskDefinitionArn)
SG=$(out AppSecurityGroupId)
SUBNETS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Parameters[?ParameterKey=='SubnetIds'].ParameterValue" --output text)
LOG_GROUP="/vista/$STACK_NAME/worker"

# Container arguments override the entrypoint's default command (see docker-entrypoint.sh).
ARGS=$(python3 -c 'import json,sys; print(json.dumps([".venv/bin/python","-m","vista.manage",*sys.argv[1:]]))' "$@")
OVERRIDES=$(python3 -c 'import json,sys; print(json.dumps({"containerOverrides":[{"name":"Main","command":json.loads(sys.argv[1])}]}))' "$ARGS")

TASK_ARN=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$TASK_DEF" --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
  --overrides "$OVERRIDES" --query "tasks[0].taskArn" --output text)
TASK_ID=${TASK_ARN##*/}
echo "task $TASK_ID started; waiting..." >&2
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN"
EXIT_CODE=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query "tasks[0].containers[0].exitCode" --output text)
REASON=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" --query "tasks[0].stoppedReason" --output text)

# Logs can lag a few seconds behind task completion.
for _ in 1 2 3 4 5 6; do
  if aws logs get-log-events --log-group-name "$LOG_GROUP" --log-stream-name "ecs/Main/$TASK_ID" \
      --start-from-head --query "events[].message" --output text 2>/dev/null | grep -q .; then break; fi
  sleep 3
done
OUTPUT=$(aws logs get-log-events --log-group-name "$LOG_GROUP" --log-stream-name "ecs/Main/$TASK_ID" \
  --start-from-head --query "events[].message" --output text 2>/dev/null || true)
if [ -n "$OUTPUT" ]; then printf '%s\n' "$OUTPUT"; else echo "(command produced no output)" >&2; fi
echo "task $TASK_ID finished with exit code $EXIT_CODE" >&2

if [ "$EXIT_CODE" != "0" ]; then
  echo "task exited with code $EXIT_CODE ($REASON)" >&2
  exit 1
fi
