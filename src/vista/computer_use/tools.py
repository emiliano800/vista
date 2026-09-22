"""The tool registry: which primitives an approved `allowed_tools` label unlocks, on which
harness kinds, and whether those harnesses exist for a company right now.

A `WorkflowDefinition` stores tool *labels* (`ToolName`); this is the only place a label
means anything. The vocabulary is exactly what the Recording Reviewer's prefilled drafts
request (`recorder_analysis.TOOLS`), so every draft an FDE approves can be resolved here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from vista.computer_use.harness import CONTROL_PRIMITIVES, LOCAL_KINDS, REMOTE_KINDS

# tool label → (primitives it unlocks, harness kinds that can perform them)
REGISTRY: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "read_source_records": (frozenset({"navigate", "read", "extract"}), frozenset({"documents", "browser"})),
    "read_destination_records": (frozenset({"navigate", "read", "extract", "http_get"}), frozenset({"browser", "http"})),
    "lookup_reference": (frozenset({"navigate", "read", "extract", "http_get"}), frozenset({"documents", "http", "browser"})),
    "map_fields": (frozenset({"extract"}), frozenset({"documents"})),
    "write_destination_records": (
        frozenset({"navigate", "click", "type_value", "press", "submit", "wait"}),
        frozenset({"browser", "desktop"}),
    ),
    "compare_with_manual_entry": (frozenset({"read", "extract", "screenshot"}), frozenset({"documents", "browser"})),
    "match_records": (frozenset({"extract"}), frozenset({"documents"})),
    "report_differences": (frozenset({"done"}), frozenset()),
    "read_messages": (frozenset({"navigate", "read", "extract"}), frozenset({"desktop", "browser", "documents"})),
    "extract_requests": (frozenset({"extract"}), frozenset({"documents", "browser"})),
    "create_task": (frozenset({"create_task"}), frozenset({"workspace"})),
    "draft_reply": (frozenset({"click", "type_value"}), frozenset({"browser", "desktop"})),  # never submit
    "check_against_rules": (frozenset({"extract"}), frozenset({"documents"})),
    "route_for_approval": (frozenset({"ask_human"}), frozenset()),
    "record_decision": (frozenset({"click", "type_value", "submit"}), frozenset({"browser", "desktop"})),
}

DEVICE_TTL = timedelta(seconds=90)  # a recorder that polled within this window is "connected"
REMOTE_PREFERENCE = ["browser", "desktop"]  # when a device offers both for one tool, claim the first


def unmapped(allowed_tools: list[str]) -> list[str]:
    return [t for t in allowed_tools if t not in REGISTRY]


def primitives_for(allowed_tools: list[str]) -> set[str]:
    out: set[str] = set(CONTROL_PRIMITIVES)
    for t in allowed_tools:
        if t in REGISTRY:
            out |= REGISTRY[t][0]
    return out


def kinds_for(allowed_tools: list[str]) -> set[str]:
    out: set[str] = set()
    for t in allowed_tools:
        if t in REGISTRY:
            out |= REGISTRY[t][1]
    return out


@dataclass
class Availability:
    available: bool
    reasons: list[str] = field(default_factory=list)
    harnesses: dict[str, bool] = field(default_factory=dict)  # every kind the run would use → present now?
    remote_kinds: list[str] = field(default_factory=list)  # the recorder harnesses this run must claim
    unmapped_tools: list[str] = field(default_factory=list)
    device: dict | None = None  # the connected recorder device, when one is needed and present

    def to_json(self) -> dict:
        return {
            "available": self.available,
            "reasons": self.reasons,
            "harnesses": self.harnesses,
            "remote_kinds": self.remote_kinds,
            "unmapped_tools": self.unmapped_tools,
            "device": self.device,
        }


def availability(session, company_id, allowed_tools: list[str], *, now: datetime | None = None) -> Availability:
    """Can this company run these tools right now? A tool's kinds are *alternatives*: a tool is
    satisfied by any one of them. `documents`/`workspace` are always there; `http` needs an
    active connection; `browser`/`desktop` need a recorder device that polled within
    `DEVICE_TTL`. A remote kind is required only by a tool with no local alternative, and only
    the remote kinds the connected device actually provides are claimed. Pure policy over two
    queries, so the eligibility endpoint stays cheap."""
    from sqlalchemy import select

    from vista.models.tenant import HarnessConnection, HarnessDevice

    now = now or datetime.now(UTC)
    missing = unmapped(allowed_tools)
    union = kinds_for(allowed_tools)
    reasons: list[str] = []
    if missing:
        reasons.append("no_harness_for_tools")

    connected = True
    if "http" in union:
        active = select(HarnessConnection.id).where(HarnessConnection.company_id == company_id, HarnessConnection.status == "active")
        connected = session.scalar(active.limit(1)) is not None
    device = None
    caps: dict = {}
    if union & REMOTE_KINDS:
        device = session.scalar(
            select(HarnessDevice)
            .where(HarnessDevice.company_id == company_id, HarnessDevice.last_seen_at >= now - DEVICE_TTL)
            .order_by(HarnessDevice.last_seen_at.desc())
            .limit(1)
        )
        caps = dict(device.capabilities or {}) if device is not None else {}
    present = {
        "documents": True,
        "workspace": True,
        "http": connected,
        "browser": bool(caps.get("browser")),
        "desktop": bool(caps.get("desktop")),
    }

    harnesses: dict[str, bool] = {}
    remote: set[str] = set()
    for tool in allowed_tools:
        alts = REGISTRY.get(tool, (frozenset(), frozenset()))[1]
        if not alts:
            continue
        local_alts = alts & LOCAL_KINDS
        for k in local_alts:
            harnesses[k] = present[k]
        if any(present[k] for k in local_alts):
            continue  # served locally; a recorder is not needed for this tool
        remote_alts = alts & REMOTE_KINDS
        usable = {k for k in remote_alts if present[k]}
        for k in remote_alts:
            harnesses[k] = present[k]
        if usable:
            remote.add(min(usable, key=REMOTE_PREFERENCE.index))  # one harness per tool: the page before the desktop
        elif remote_alts:
            if "harness_not_connected" not in reasons:
                reasons.append("harness_not_connected")
        elif "connection_missing" not in reasons:
            reasons.append("connection_missing")

    device_out = None
    if device is not None:
        device_out = {
            "id": str(device.id),
            "device_id": device.device_id,
            "user_id": str(device.user_id),
            "platform": device.platform,
            "capabilities": caps,
        }
    return Availability(
        available=not reasons,
        reasons=reasons,
        harnesses=harnesses,
        remote_kinds=sorted(remote),
        unmapped_tools=missing,
        device=device_out,
    )
