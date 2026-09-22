"""Harnesses that run inside the worker: documents, http, workspace.

They never suspend. Their candidates are the run's own declared inputs (a ticked document,
a set of canonical records), the allow-listed endpoints of a company connection, and the
one thing the agent may write in Vista itself — a task for a person.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from pathlib import PurePosixPath

import httpx
from sqlalchemy import select

from vista import documents
from vista.computer_use.harness import Action, ActionResult, Candidate, Observation, ObserveContext, rank_candidates

MAX_ROWS = 200
MAX_TEXT = 4000
HTTP_TIMEOUT_S = 20.0
HTTP_MAX_BYTES = 256 * 1024

RECORD_TABLES: dict[str, str] = {
    "customers": "Customer",
    "invoices": "Invoice",
    "vendors": "Vendor",
    "purchase_orders": "PurchaseOrder",
    "subscriptions": "Subscription",
    "policies": "Policy",
}
PROVENANCE_COLUMNS = {
    "id",
    "company_id",
    "data_source_type",
    "source_file_id",
    "import_job_id",
    "synthetic_demo",
    "created_at",
    "updated_at",
}


def _tag(candidates: list[Candidate], harness: str) -> list[Candidate]:
    return [Candidate(c.id, c.role, c.name, c.kind, {**c.attrs, "harness": harness}) for c in candidates]


class DocumentsHarness:
    """Reads the documents and canonical records bound to the run. `reader(binding)` returns
    `(data, ext, filename)`; the default re-verifies the artifact's digest on read."""

    kind = "documents"

    def __init__(
        self, session, tenant_schema: str, inputs: dict[str, dict], reader: Callable[[dict], tuple[bytes, str, str]] | None = None
    ):
        self.session = session
        self.tenant_schema = tenant_schema
        self.bindings = {name: b for name, b in inputs.items() if b.get("kind") in ("document", "records")}
        self.reader = reader or self._read_artifact

    def capabilities(self) -> set[str]:
        return {"read", "extract"}

    def observe(self, context: ObserveContext) -> Observation:
        candidates = []
        for name, b in self.bindings.items():
            if b["kind"] == "document":
                label = f"{name} ({b.get('filename') or b.get('artifact_id')})"
                candidates.append(Candidate(f"doc:{name}", "document", label, "document", {"input": name}))
            else:
                label = f"{name} ({b.get('table')} × {len(b.get('ids', []))})"
                candidates.append(Candidate(f"records:{name}", "records", label, "record", {"input": name}))
        return Observation("documents", {"inputs": list(self.bindings)}, rank_candidates(_tag(candidates, self.kind)))

    def act(self, action: Action) -> ActionResult:
        if action.primitive not in self.capabilities() or action.target is None:
            return ActionResult(action.step_id, False, error="documents harness only reads and extracts a named input")
        name = action.target.attrs.get("input")
        binding = self.bindings.get(name or "")
        if binding is None:
            return ActionResult(action.step_id, False, error="target_not_found")
        if binding["kind"] == "document":
            data, ext, filename = self.reader(binding)
            result = documents.extract(data, ext)
            facts = _document_facts(result, filename)
            return ActionResult(action.step_id, True, description=f"Read {filename}: {_summary_text(facts)}", facts=facts)
        rows = self._records(binding)
        facts = {
            "rows": rows,
            "columns": sorted({k for r in rows for k in r}),
            "count": len(rows),
            "summary": f"{binding['table']} × {len(rows)}",
        }
        return ActionResult(action.step_id, True, description=f"Read {len(rows)} {binding['table']} record(s)", facts=facts)

    def close(self) -> None:
        return None

    def _read_artifact(self, binding: dict) -> tuple[bytes, str, str]:
        from vista.models.tenant import RecorderSubmission
        from vista.recorder_uploads import read_artifact

        row = self.session.get(RecorderSubmission, uuid.UUID(str(binding["submission_id"])))
        if row is None:
            raise RuntimeError("bound submission no longer exists")
        artifact = next((a for a in row.manifest["artifacts"] if a["id"] == binding["artifact_id"]), None)
        if artifact is None:
            raise RuntimeError("bound artifact is not in the submission")
        verified = {v["id"]: v for v in (row.verified_artifacts or [])}
        data = read_artifact(self.tenant_schema, row, artifact, expected=verified.get(artifact["id"]))
        return data, PurePosixPath(artifact["filename"]).suffix.lower(), artifact["filename"]

    def _records(self, binding: dict) -> list[dict]:
        from vista.models import tenant as models

        model = getattr(models, RECORD_TABLES[binding["table"]])
        ids = [uuid.UUID(str(i)) for i in binding.get("ids", [])][:MAX_ROWS]
        rows = self.session.scalars(select(model).where(model.id.in_(ids))).all() if ids else []
        out = []
        for r in rows:
            row = {}
            for col in r.__table__.columns:
                if col.name in PROVENANCE_COLUMNS:
                    continue
                val = getattr(r, col.name)
                if val is None:
                    continue
                row[col.name] = str(val)[:512]
            out.append(row)
        return out


def _document_facts(result: dict, filename: str) -> dict:
    facts: dict = {"filename": filename, "kind": result.get("kind"), "summary": documents.summary(result)}
    if result.get("error"):
        facts["error"] = result["error"]
    kind = result.get("kind")
    table = None
    if kind == "table":
        table = result.get("rows") or []
    elif kind == "workbook" and result.get("sheets"):
        table = result["sheets"][0].get("rows") or []
        facts["sheet"] = result["sheets"][0].get("name")
    if table:
        header = [str(h) for h in table[0]]
        rows = []
        for raw in table[1 : MAX_ROWS + 1]:
            rows.append({header[i] if i < len(header) else f"col{i + 1}": str(v) for i, v in enumerate(raw) if str(v) != ""})
        facts["rows"], facts["columns"], facts["count"] = rows, header, len(rows)
    elif kind == "document":
        facts["text"] = "\n".join(result.get("paragraphs", [])[:60])[:MAX_TEXT]
    elif kind in ("pdf", "text"):
        facts["text"] = str(result.get("text", ""))[:MAX_TEXT]
    return facts


def _summary_text(facts: dict) -> str:
    if "count" in facts:
        return f"{facts['count']} rows × {len(facts.get('columns', []))} columns"
    if "text" in facts:
        return f"{len(facts['text'])} characters of text"
    return str(facts.get("kind", "document"))


class HttpHarness:
    """GET-only against the allow-listed paths of a company's connections. The response is a
    fact, never something to execute."""

    kind = "http"

    def __init__(self, connections: list[dict], client: httpx.Client | None = None, env: dict | None = None):
        self.connections = connections
        self.client = client
        self.env = env

    def capabilities(self) -> set[str]:
        return {"http_get"}

    def observe(self, context: ObserveContext) -> Observation:
        candidates = []
        for conn in self.connections:
            cfg = conn.get("config") or {}
            base = str(cfg.get("base_url", "")).rstrip("/")
            for path in cfg.get("allow_paths", []):
                candidates.append(
                    Candidate(
                        f"http:{conn['name']}:{path}",
                        "endpoint",
                        f"{conn['name']} {path}",
                        "endpoint",
                        {"url": base + path, "connection": conn["name"]},
                    )
                )
        return Observation("http", {"connections": [c["name"] for c in self.connections]}, rank_candidates(_tag(candidates, self.kind)))

    def act(self, action: Action) -> ActionResult:
        if action.primitive != "http_get" or action.target is None:
            return ActionResult(action.step_id, False, error="http harness only fetches a named endpoint")
        url = action.target.attrs.get("url", "")
        conn = next((c for c in self.connections if c["name"] == action.target.attrs.get("connection")), None)
        cfg = (conn or {}).get("config") or {}
        base = str(cfg.get("base_url", "")).rstrip("/")
        if conn is None or not base or not (url == base or url.startswith(base + "/")):
            return ActionResult(action.step_id, False, error="url_not_allowed")
        headers = {}
        token_env = cfg.get("token_env")
        if token_env:
            token = (self.env if self.env is not None else os.environ).get(token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        try:
            client = self.client or httpx.Client(timeout=HTTP_TIMEOUT_S)
            resp = client.get(url, headers=headers)
            body = resp.content[:HTTP_MAX_BYTES]
        except httpx.HTTPError as exc:
            return ActionResult(action.step_id, False, error=f"http_error: {exc.__class__.__name__}")
        facts: dict = {"status": resp.status_code, "url": url}
        try:
            parsed = json.loads(body)
            if isinstance(parsed, list):
                facts["rows"] = [r for r in parsed[:MAX_ROWS] if isinstance(r, dict)]
                facts["count"] = len(parsed)
            elif isinstance(parsed, dict):
                facts["keys"] = list(parsed)[:50]
                rows = next((v for v in parsed.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
                if rows:
                    facts["rows"] = rows[:MAX_ROWS]
                    facts["count"] = len(rows)
        except ValueError:
            facts["text"] = body.decode("utf-8", "replace")[:MAX_TEXT]
        return ActionResult(action.step_id, resp.status_code < 400, description=f"Fetched {url} → {resp.status_code}", facts=facts)

    def close(self) -> None:
        return None


class WorkspaceHarness:
    """The one thing the agent may write in Vista: a task for a person, citing gathered facts."""

    kind = "workspace"

    def __init__(self, session, platform_session_factory, company_id: uuid.UUID, agent_run_id: uuid.UUID, workflow_name: str):
        self.session = session
        self.platform_session_factory = platform_session_factory
        self.company_id = company_id
        self.agent_run_id = agent_run_id
        self.workflow_name = workflow_name

    def capabilities(self) -> set[str]:
        return {"create_task"}

    def observe(self, context: ObserveContext) -> Observation:
        c = Candidate("task:new", "task", "Create a follow-up task for a person in the Vista workspace", "record", {})
        return Observation("workspace", {}, _tag([c], self.kind))

    def act(self, action: Action) -> ActionResult:
        if action.primitive != "create_task":
            return ActionResult(action.step_id, False, error="workspace harness only creates tasks")
        from vista.models.platform import FirmCompany
        from vista.models.tenant import Task
        from vista.portfolio.service import next_ref

        with self.platform_session_factory() as platform:
            firm_id = platform.scalar(select(FirmCompany.firm_id).where(FirmCompany.id == self.company_id))
            ref = next_ref(platform, firm_id, "task", "T") if firm_id else f"T-{uuid.uuid4().hex[:6]}"
            platform.commit()
        task = Task(
            company_id=self.company_id,
            ref=ref,
            title=f"{self.workflow_name}: follow-up from the Computer Use Agent",
            description=str(
                action.args.get("description") or "Created during a sandbox workflow run; see the run trace for the facts gathered."
            ),
            category="Automation",
            source_type="agent_run",
            source_id=str(self.agent_run_id),
            priority="Medium",
            status="Open",
            created_by="Computer Use Agent",
        )
        self.session.add(task)
        self.session.flush()
        return ActionResult(
            action.step_id, True, description=f"Created task {ref}", facts={"task_ref": ref}, undo={"task_ref": ref, "action": "dismiss"}
        )

    def close(self) -> None:
        return None
