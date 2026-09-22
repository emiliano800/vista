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
    harnesses: dict[str, bool] = field(default_factory=dict)
    unmapped_tools: list[str] = field(default_factory=list)
    device: dict | None = None  # the connected recorder device, when one is needed and present

    def to_json(self) -> dict:
        return {
            "available": self.available,
            "reasons": self.reasons,
            "harnesses": self.harnesses,
            "unmapped_tools": self.unmapped_tools,
            "device": self.device,
        }


def availability(session, company_id, allowed_tools: list[str], *, now: datetime | None = None) -> Availability:
    """Can this company run these tools right now? Local kinds are always available; `http`
    needs an active connection; `browser`/`desktop` need a recorder device that polled within
    `DEVICE_TTL`. Pure policy over two queries, so the eligibility endpoint stays cheap."""
    from sqlalchemy import select

    from vista.models.tenant import HarnessConnection, HarnessDevice

    now = now or datetime.now(UTC)
    missing = unmapped(allowed_tools)
    needed = kinds_for(allowed_tools)
    harnesses: dict[str, bool] = {k: True for k in needed & LOCAL_KINDS if k != "http"}
    reasons: list[str] = []
    device_out = None
    if missing:
        reasons.append("no_harness_for_tools")
    if "http" in needed:
        connected = session.scalar(
            select(HarnessConnection.id).where(HarnessConnection.company_id == company_id, HarnessConnection.status == "active").limit(1)
        )
        harnesses["http"] = connected is not None
        if connected is None:
            reasons.append("connection_missing")
    if needed & REMOTE_KINDS:
        device = session.scalar(
            select(HarnessDevice)
            .where(HarnessDevice.company_id == company_id, HarnessDevice.last_seen_at >= now - DEVICE_TTL)
            .order_by(HarnessDevice.last_seen_at.desc())
            .limit(1)
        )
        caps = dict(device.capabilities or {}) if device is not None else {}
        for kind in needed & REMOTE_KINDS:
            harnesses[kind] = bool(device is not None and caps.get(kind))
        if not all(harnesses[k] for k in needed & REMOTE_KINDS):
            reasons.append("harness_not_connected")
        if device is not None:
            device_out = {
                "id": str(device.id),
                "device_id": device.device_id,
                "user_id": str(device.user_id),
                "platform": device.platform,
                "capabilities": caps,
            }
    return Availability(available=not reasons, reasons=reasons, harnesses=harnesses, unmapped_tools=missing, device=device_out)
