# Deploy the backend on AWS

| Component | Service |
| --- | --- |
| Python API (FastAPI) | Amazon ECS **Express Mode** on Fargate: load balancer, HTTPS, auto scaling and canary deployments managed by ECS |
| PostgreSQL | Amazon RDS for PostgreSQL 16 (private, encrypted, managed password in Secrets Manager) |
| Uploaded reports | Private Amazon S3 bucket (public access blocked, TLS-only, versioned) |
| Job worker (agent runs) | Optional standard ECS service on Fargate, same image |
| Website + `bumpsolutions.org` | Unchanged: the Cloudflare Worker `vista` serves the UI and proxies `/api` to the AWS endpoint |

Everything is one CloudFormation stack (`deploy/aws/template.yaml`) plus two scripts:
`deploy.sh` builds and pushes the image and creates/updates the stack; `manage.sh` runs
operator commands (`create-workspace`, `add-user`, `rotate-key`) inside AWS.

## How the app is wired

* The image is the root `Dockerfile`. Its entrypoint runs migrations on start
  (`VISTA_MIGRATE_ON_START=true`; `vista.manage migrate` takes a Postgres advisory lock so
  several tasks starting together migrate once) and then serves the API behind the load
  balancer with proxy headers trusted.
* Database credentials never sit in the template or environment: RDS manages the master
  password in Secrets Manager and ECS injects `VISTA_DB_USER` / `VISTA_DB_PASSWORD` at task
  start. `vista.config` composes `VISTA_DATABASE_URL` from `VISTA_DB_*` and forces
  `sslmode=require`.
* S3 access uses the task IAM role (no static keys): `VISTA_S3_ENDPOINT_URL` and the key
  variables are empty, `VISTA_S3_REGION` is the stack region, and the role can only read and
  write the report bucket.
* The health check is `/api/health`. Logs go to `/vista/<stack>/api` and
  `/vista/<stack>/worker` in CloudWatch with 30-day retention.
* The optional OpenAI key and provisioning key are stored in Secrets Manager by the stack
  and injected as secrets, never as plain environment variables.

## Prerequisites

* AWS CLI v2 signed in to the target account (`brew install awscli`, then `aws configure`
  or `aws configure sso`). Verify with `aws sts get-caller-identity`.
* Docker with buildx (Docker Desktop or OrbStack). Images are built for `linux/amd64`
  because Express Mode tasks run x86_64; on Apple Silicon this uses emulation and takes a few
  minutes.
* A default VPC with at least two public subnets in the chosen region (every new account has
  one). To use another VPC, set `VPC_ID` and `SUBNET_IDS` (public subnets, two or more AZs).
* The IAM identity needs rights for CloudFormation, ECS, ECR, EC2 (security groups), RDS, S3,
  Secrets Manager, CloudWatch Logs and IAM role creation.

## First deployment

```sh
cp deploy/aws/.env.example deploy/aws/.env   # set AWS_REGION, optionally OPENAI_API_KEY
deploy/aws/deploy.sh
```

The script creates the ECR repository, builds and pushes the image tagged with the git
commit, deploys the stack (RDS takes about 10 minutes the first time) and prints the outputs.
Then:

```sh
aws ecs wait services-stable --cluster vista --services vista-api
curl -s https://<ApiEndpoint>/api/health           # {"status":"ok"}
deploy/aws/manage.sh --output-file "$HOME/vista-workspace.json" create-workspace --firm "Your firm" --company "Your company" --email you@example.com
```

`--output-file` saves the result and personal access key to a new file readable only by
your user, then removes that command's CloudWatch log stream after successful retrieval.
It refuses to overwrite an existing file. Move the key into a password manager and remove
the local file afterward. Without `--output-file`, the command prints the key and leaves
its CloudWatch copy in place. If output retrieval fails, inspect the task before retrying:
the workspace may already have been created.

### Connect Cloudflare

In the Cloudflare dashboard, Worker `vista` → Settings → Variables, set `API_ORIGIN` to the
`ApiEndpoint` output, for example `https://vista-api.ecs.us-east-1.on.aws` (no `/api`
suffix, no trailing slash). The Worker only accepts an `https` origin with an empty path.
Redeploy the Worker if `keep_vars` is off. The site at `bumpsolutions.org` then talks to
AWS; nothing else in Cloudflare changes.

### Desktop recorder

In the recorder's Settings → Cloud workspace, enter the website URL
(`https://bumpsolutions.org`), the company ID from `create-workspace` and the access key.
With a workspace connected, Stop uploads the redacted report and submits the session's
stretches for AI review; the worker (`WORKER_DESIRED_COUNT=1` + `OPENAI_API_KEY`) writes the
explanations, the recorder polls them back, and Approve / Fix / Explain decisions sync to the
workspace. Employees never need an OpenAI key. Without a worker the review stays
"Explaining…" until one runs.

## Day-to-day

| Task | Command |
| --- | --- |
| Ship a new version | `deploy/aws/deploy.sh` (build, push, canary deploy; rolls back on 5xx alarms) |
| Redeploy without rebuilding | `deploy/aws/deploy.sh --no-build` |
| Stack outputs | `deploy/aws/deploy.sh --outputs` |
| Add a user | `deploy/aws/manage.sh add-user --tenant TENANT_UUID --company COMPANY_UUID --email employee@example.com --role member` |
| Rotate a key | `deploy/aws/manage.sh rotate-key --user USER_UUID` |
| Run agent jobs and recording AI review in AWS | set `WORKER_DESIRED_COUNT=1` (and `OPENAI_API_KEY`) in `deploy/aws/.env`, redeploy |
| API logs | `aws logs tail /vista/vista/api --follow` |
| Shell into a running API task | `aws ecs execute-command --cluster vista --task <task-id> --container Main --interactive --command /bin/sh` (needs the Session Manager plugin) |
| Scale | change `ApiMinTasks` / `ApiMaxTasks` / `ApiCpu` / `ApiMemory` parameters and redeploy |

Changing `ALLOWED_ORIGINS` also requires updating the bucket CORS rule in the template if the
browser uploads documents through presigned URLs.

## Costs (rough, us-east-1, on-demand)

| Item | Approx. per month |
| --- | --- |
| Fargate 0.5 vCPU / 1 GB, one task always on | ~$18 |
| Application Load Balancer (created by Express Mode) | ~$17 + traffic |
| RDS db.t4g.micro, 20 GB gp3, single-AZ | ~$15 |
| S3, CloudWatch Logs, Secrets Manager (2–3 secrets) | a few dollars |

Around $50–60/month at idle. AWS Activate credits cover this. The largest lever is the
load balancer; it is shared by up to 25 Express services in the same VPC.

## Tear down

```sh
aws ecs update-service --cluster vista --service vista-api --desired-count 0   # optional, faster
aws cloudformation delete-stack --stack-name vista
```

The database is protected by default: set `DB_DELETION_PROTECTION=false` and redeploy before
deleting, or the stack deletion fails. The report bucket is retained on delete (empty it and
remove it by hand); the database is snapshotted.

## Hardening later

* Move RDS and the tasks into private subnets with a NAT gateway (the default VPC only has
  public subnets; the database is still unreachable from the internet because it has no
  public address and its security group only admits the app).
* Put a custom hostname (`api.bumpsolutions.org`) on the Express endpoint through a CNAME in
  Cloudflare, and attach AWS WAF to the load balancer.
* Multi-AZ RDS and a larger instance class once real customers rely on it.
* CI deployment from GitHub Actions with an OIDC role instead of local credentials.

Reference: [Amazon ECS Express Mode](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-getting-started.html),
[AWS::ECS::ExpressGatewayService](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ecs-expressgatewayservice.html),
[RDS managed master passwords](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-secrets-manager.html).
