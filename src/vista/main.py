from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from vista.api import (
    analytics,
    company_imports,
    company_workflows,
    computer_use,
    deals,
    employees,
    evals,
    findings,
    portfolio,
    recorder,
    recordings,
    runs,
    sessions,
    summaries,
    synthetic,
    tenants,
    usage,
    workflows,
)

app = FastAPI(title="Vista", version="0.2.0")


@app.middleware("http")
async def bounded_requests(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8 * 1024 * 1024:
                return JSONResponse({"detail": "Upload exceeds the 8 MiB limit"}, status_code=413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # Do not echo access keys or recording contents in error payloads/logs.
    return JSONResponse(
        {
            "detail": [
                {"loc": e["loc"], "msg": "Invalid data" if e["type"] == "value_error" else e["msg"], "type": e["type"]}
                for e in exc.errors()
            ]
        },
        status_code=422,
    )


for router in (
    tenants.router,
    deals.router,
    runs.router,
    usage.router,
    analytics.router,  # before employees: /agents/analytics must win over /agents/{agent_id}
    employees.router,
    findings.router,
    summaries.router,
    sessions.router,
    recordings.router,
    recorder.router,
    company_imports.router,
    portfolio.router,
    synthetic.router,
    evals.router,
    workflows.router,
    company_workflows.router,
    computer_use.router,
    computer_use.recorder_router,
):
    app.include_router(router)
    app.include_router(router, prefix="/api", include_in_schema=False)


@app.get("/health")
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


public = Path(__file__).resolve().parents[1] / "web" / "public"
if public.exists():
    app.mount("/", StaticFiles(directory=public, html=True), name="web")
