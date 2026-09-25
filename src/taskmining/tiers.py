"""Per-edge autonomy tiers: `shadow → ask → confirm → unattended`, pinned in the version.

Entry conditions are computed here from the edge's statistics, but they only ever *propose*:
promotion past `ask` is an FDE click after a cooling period, and the click lands in a draft
version that goes through the ordinary approval gate. Demotion is automatic — any denial, a
verified failure, `effect_missing` on a write or a leakage failure takes the edge one tier
down in the next draft. Statistics live outside the structural hash; on their own they can
never change what the agent may do.

Every number is provisional until milestone 1 replaces it with a measured one.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

TIERS: tuple[str, ...] = ("shadow", "ask", "confirm", "unattended")
COOLING_DAYS = 7  # between an entry condition being met and the FDE click that acts on it
SHADOW_MIN_TOTAL = 3
SHADOW_MIN_AGREEMENT = 0.8
CONFIRM_MIN_EXECUTED = 3
UNATTENDED_MIN_EXECUTED = 10
UNATTENDED_MIN_VERIFIED = 0.95
FDE_CLICK_ABOVE = "ask"  # tiers above this are never entered by code alone
WRITE_CLASSES: frozenset[str] = frozenset({"click", "type_value", "submit", "create_task"})
DEMOTIONS: tuple[tuple[str, str], ...] = (
    ("denied", "a person denied the move"),
    ("verified_fail", "a run traversing it failed verification"),
    ("effect_missing", "a write whose effect was not seen"),
    ("leakage_failed", "a cloud-bound observation failed the leakage test"),
)


def empty_tier_stats() -> dict:
    return {"recovery_used": 0, "shadow_total": 0, "shadow_agree": 0, "verified_fail": 0, "leakage_failed": 0}


def tier_index(tier: str | None) -> int:
    return TIERS.index(tier) if tier in TIERS else 0


def lower(a: str | None, b: str | None) -> str:
    """The stricter (lower) of two tiers; an absent tier is `shadow`."""
    return TIERS[min(tier_index(a), tier_index(b))]


def is_write(edge: dict) -> bool:
    """The code-assigned irreversibility class decides; the action class is the v1 fallback."""
    cls = edge.get("irreversibility")
    if cls:
        return cls in ("mutating", "committing")
    return edge["action_class"] in WRITE_CLASSES


def is_committing(edge: dict) -> bool:
    cls = edge.get("irreversibility")
    if cls:
        return cls == "committing"
    return edge["action_class"] == "submit"


def ceiling(edge: dict, covered: bool) -> str:
    """The highest tier this edge may ever hold: committing edges and writes without a covering
    `read_back` stop at `confirm` — a stated ceiling, not a workaround."""
    if is_committing(edge):
        return "confirm"
    if is_write(edge) and not covered:
        return "confirm"
    return "unattended"


def entry_tier(edge: dict, covered: bool) -> str:
    """The tier whose entry conditions the statistics satisfy, capped by `ceiling`."""
    s = {**empty_tier_stats(), **edge.get("stats", {})}
    if s["denied"] > 0 or s["leakage_failed"] > 0:
        return "shadow"
    tier = "shadow"
    if s["shadow_total"] >= SHADOW_MIN_TOTAL and s["shadow_agree"] / s["shadow_total"] >= SHADOW_MIN_AGREEMENT:
        tier = "ask"
    elif s["executed"] > 0 and s["verified_ok"] > 0:
        tier = "ask"  # a run a person approved and that verified is at least as good as a shadow agreement
    if tier == "ask" and s["executed"] >= CONFIRM_MIN_EXECUTED and s["verified_ok"] >= CONFIRM_MIN_EXECUTED and s["verified_fail"] == 0:
        tier = "confirm"
    if (
        tier == "confirm"
        and s["executed"] >= UNATTENDED_MIN_EXECUTED
        and s["verified_ok"] / s["executed"] >= UNATTENDED_MIN_VERIFIED
        and s["verified_fail"] == 0
        and (s["effect_missing"] == 0 or not is_write(edge))
    ):
        tier = "unattended"
    return lower(tier, ceiling(edge, covered))


def demotion(edge: dict) -> tuple[str, str] | None:
    """`(tier after, reason)` when this version's statistics demand an automatic demotion."""
    cur = edge.get("tier")
    if cur is None or tier_index(cur) == 0:
        return None
    s = {**empty_tier_stats(), **edge.get("stats", {})}
    for key, why in DEMOTIONS:
        if key == "effect_missing" and not is_write(edge):
            continue
        if s.get(key, 0) > 0:
            return TIERS[tier_index(cur) - 1], f"{key} > 0: {why}"
    return None


def cooled(edge: dict, today: date) -> bool:
    since = edge.get("tier_since")
    if not since:
        return True
    try:
        start = datetime.fromisoformat(str(since)).date()
    except ValueError:
        return True
    return today - start >= timedelta(days=COOLING_DAYS)


def proposal(edge: dict, covered: bool, today: date) -> dict | None:
    """What code may say about this edge's tier: `{"to", "reason", "needs_fde", "cooled"}` when
    the entry conditions point above the pinned tier. Only `shadow → ask` may be applied without
    an FDE; everything above is a click, and only after the cooling period."""
    cur = edge.get("tier") or "shadow"
    target = entry_tier(edge, covered)
    if tier_index(target) <= tier_index(cur):
        return None
    to = TIERS[tier_index(cur) + 1]  # one tier at a time
    s = edge.get("stats", {})
    return {
        "to": to,
        "reason": (
            f"{s.get('executed', 0)} executed, {s.get('verified_ok', 0)} verified, {s.get('shadow_agree', 0)}/{s.get('shadow_total', 0)} "
            f"shadow agreement, {s.get('denied', 0)} denied, {s.get('effect_missing', 0)} effect missing"
        ),
        "needs_fde": tier_index(to) > tier_index(FDE_CLICK_ABOVE),
        "cooled": cooled(edge, today),
        "ceiling": ceiling(edge, covered),
    }


def covering_read_back(edge: dict, criteria: list[dict]) -> bool:
    """A write is covered when a `read_back` criterion names a slot the edge writes or produces."""
    slots = {edge.get("slot"), *edge.get("produces", [])} - {None}
    return any(c.get("type") == "read_back" and c.get("slot") in slots for c in criteria)
