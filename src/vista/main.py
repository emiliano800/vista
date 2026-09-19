from fastapi import FastAPI

from vista.api import deals, runs, tenants, usage

app = FastAPI(title="Vista", version="0.1.0")

app.include_router(tenants.router)
app.include_router(deals.router)
app.include_router(runs.router)
app.include_router(usage.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
