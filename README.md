# Vista

Process intelligence for lower-middle-market private equity: desktop task mining,
company workspaces, and per-employee AI agents that surface operational
inefficiencies with evidence.

Docs:
- [CURRENT_IMPLEMENTATION.md](CURRENT_IMPLEMENTATION.md) — everything built and deployed, how it works
- [NEXT_STEPS.md](NEXT_STEPS.md) — roadmap, starting with the frontend rebuild toward the real vision
- [BUSINESS_COURSE_OF_ACTION.md](BUSINESS_COURSE_OF_ACTION.md) — business plan + go-to-market sequence

Live: https://bumpsolutions.org (Cloudflare Worker → AWS ECS/RDS/S3 backend).

## Quickstart

```sh
docker compose up -d                                # Postgres + MinIO
uv sync && cp .env.example .env
uv run python -m vista.manage migrate               # schemas + bucket
VISTA_COOKIE_SECURE=false uv run uvicorn vista.main:app --reload
uv run python -m vista.jobs.worker                  # separate terminal
uv run pytest
```

Workspaces are provisioned with `python -m vista.manage create-workspace`
(public `POST /tenants` is disabled unless an operator provisioning key is set).
Hosting: [deploy/README.md](deploy/README.md) · AWS: [deploy/aws/README.md](deploy/aws/README.md).
