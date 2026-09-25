"""A frame: what one screen looks like to the graph, computed in code from an observation.

The same function serves the recorder (a frame per focus/click/commit) and the run loop (a frame
per step), so node identity is decided once. Two halves, per the design of record §2:

- **L0** — identity. Here `in:<screen-class>` (URL path shape + sorted landmark roles; title
  shape + landmarks on desktop) and `ctx:<dialog-class>` when a modal is up. The slot-derived
  members (`have:`/`read:`/`open:`) need the recording's slot table and are added by the
  compiler (step 3) from `fields_with_value`; a frame alone cannot know slot names.
- **L1** — context: landmark roles, modal present, primary-button descriptor, which control
  classes exist (never how many). Tie-breaks and `effect_seen`, never identity.

Descriptors are `(role, normalised name, landmark, position class, aliases)`. The raw
accessible name and any coordinate stay in `Candidate` (device-local); `Frame.cloud()` emits only
the normalised form, capped at `MAX_DESCRIPTORS`, and it is what the leakage test is run over.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from taskmining.leakage import LANDMARK_ROLES
from taskmining.normalise import Vocabulary, normalise

MAX_DESCRIPTORS = 40
MAX_TEXT = 12000

DIALOG_ROLES: frozenset[str] = frozenset({"dialog", "alertdialog"})
FIELD_ROLES: frozenset[str] = frozenset({"textbox", "searchbox", "combobox", "spinbutton", "slider", "checkbox", "radio", "switch"})
# Normalised names that make a button "committing" (design §2, edge contract). Shared with
# `irreversibility()` in step 3; declared here because the primary-button descriptor uses it.
COMMIT_VOCABULARY: frozenset[str] = frozenset(
    {"save", "submit", "send", "post", "delete", "approve", "confirm", "pay", "ok", "yes", "continue"}
)
POSITION_CLASSES: tuple[str, ...] = ("top-left", "top-right", "bottom-left", "bottom-right", "unknown")

_ID_SEGMENT = re.compile(r"^(?:\d+|[0-9a-f]{8,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[A-Z]{1,4}-?\d{2,})$", re.I)
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Descriptor:
    role: str
    name: str  # normalised (class tokens / vocabulary words / {text}); safe for the cloud
    landmark: str = ""
    position: str = "unknown"
    aliases: tuple[str, ...] = ()

    def key(self) -> str:
        return f"{self.role}|{self.name}|{self.landmark}"

    def to_json(self) -> dict:
        return {"role": self.role, "name": self.name, "landmark": self.landmark, "position": self.position, "aliases": list(self.aliases)}


@dataclass
class Candidate:
    """One target the driver can act on. `id` is meaningful for the current observation only;
    `name` is the raw accessible name and stays on the device."""

    id: str
    role: str
    name: str
    kind: str  # field | link | interactive | row | record
    landmark: str = ""
    position: str = "unknown"
    has_value: bool = False
    disabled: bool = False
    primary: bool = False

    def to_json(self) -> dict:
        attrs: dict[str, bool] = {}
        if self.has_value:
            attrs["has_value"] = True
        if self.disabled:
            attrs["disabled"] = True
        if self.primary:
            attrs["primary"] = True
        return {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "kind": self.kind,
            "landmark": self.landmark,
            "position": self.position,
            "attrs": attrs,
        }


@dataclass
class Frame:
    kind: str  # browser | desktop
    url: str
    title: str
    sensitive: bool
    candidates: list[Candidate]
    descriptors: list[Descriptor]
    l0: list[str]
    l1: dict
    screen_class: str
    fields_with_value: list[str]  # descriptor keys of fields that hold a value (value itself never read)
    text_local: str = ""  # page text excerpt; device only
    settled: bool = True
    extra: dict = field(default_factory=dict)

    def observation(self, observation_id: str) -> dict:
        """The harness observation the run loop already consumes (`browser.js` shape) plus the
        state halves. Still device-side: `text_excerpt` and raw names are in it."""
        return {
            "observation_id": observation_id,
            "url": self.url,
            "title": self.title,
            "sensitive": self.sensitive,
            "candidates": [c.to_json() for c in self.candidates],
            "text_excerpt": self.text_local,
            "l0": list(self.l0),
            "l1": dict(self.l1),
            "screen_class": self.screen_class,
            "settled": self.settled,
        }

    def cloud(self) -> dict:
        """What may leave the device about this frame: L0, L1, at most `MAX_DESCRIPTORS`
        normalised descriptors, and the same descriptors once per live candidate under an opaque
        per-observation alias (`targets`) so the run loop can resolve an edge's descriptor to a
        thing it may act on. No URL, title, text or raw name."""
        return {
            "kind": self.kind,
            "sensitive": self.sensitive,
            "settled": self.settled,
            "l0": list(self.l0),
            "l1": dict(self.l1),
            "descriptors": [d.to_json() for d in self.descriptors[:MAX_DESCRIPTORS]],
            "targets": [t for _, t in self.targets()],
            "fields_with_value": list(self.fields_with_value),
        }

    def targets(self, vocab: Vocabulary | None = None) -> list[tuple[str, dict]]:
        """`(real candidate id, cloud target)` pairs; the alias is the target's `id`."""
        out: list[tuple[str, dict]] = []
        for i, c in enumerate(self.candidates[:MAX_DESCRIPTORS]):
            d = descriptor_for(c, vocab or self.extra.get("vocab"))
            out.append(
                (
                    c.id,
                    {
                        **d.to_json(),
                        "id": f"c{i}",
                        "kind": c.kind,
                        "has_value": c.has_value,
                        "disabled": c.disabled,
                        "primary": c.primary,
                    },
                )
            )
        return out

    def to_json(self) -> dict:
        data = asdict(self)
        data["candidates"] = [c.to_json() for c in self.candidates]
        data["descriptors"] = [d.to_json() for d in self.descriptors]
        return data


def path_shape(url: str) -> str:
    """`/customers/4821/invoices/INV-2024-7` → `/customers/{id}/invoices/{id}`; query and fragment dropped."""
    try:
        path = urlsplit(url or "").path
    except ValueError:
        path = ""
    segments = [s for s in path.split("/") if s]
    return "/" + "/".join("{id}" if _ID_SEGMENT.match(s) else s.lower() for s in segments)


def title_shape(title: str, vocab: Vocabulary | None = None) -> str:
    """Desktop counterpart of `path_shape`: the title normalised (data → tokens, unknown words → {text})."""
    return normalise(title or "", vocab)


def screen_class(kind: str, shape: str, landmarks: list[str]) -> str:
    h = hashlib.sha256(f"{kind}\0{shape}\0{'|'.join(sorted(set(landmarks)))}".encode()).hexdigest()
    return h[:12]


def dialog_class(name: str, vocab: Vocabulary | None = None) -> str:
    n = normalise(name or "", vocab)
    if not n or n == "{text}":
        return "dialog"
    return _SPACE.sub("-", n)[:60]


def position_class(x: float | None, y: float | None, width: float | None, height: float | None) -> str:
    if x is None or y is None or not width or not height or width <= 0 or height <= 0:
        return "unknown"
    return f"{'top' if y < height / 2 else 'bottom'}-{'left' if x < width / 2 else 'right'}"


def descriptor_for(c: Candidate, vocab: Vocabulary | None = None) -> Descriptor:
    return Descriptor(role=c.role, name=normalise(c.name, vocab) or "{text}", landmark=c.landmark, position=c.position)


def frame_from_candidates(
    *,
    kind: str,
    url: str,
    title: str,
    candidates: list[Candidate],
    landmarks: list[str],
    dialog: str | None = None,
    text: str = "",
    sensitive: bool = False,
    vocab: Vocabulary | None = None,
    settled: bool = True,
) -> Frame:
    """The one state function. `landmarks` are the landmark roles present on the screen (the
    driver walks them); `dialog` is the accessible name of the front-most modal, if any."""
    if sensitive:
        return Frame(kind, "", "", True, [], [], [f"in:{screen_class(kind, 'sensitive', [])}"], {}, "sensitive", [], "", settled)
    shape = path_shape(url) if kind == "browser" else title_shape(title, vocab)
    marks = sorted({m for m in landmarks if m in LANDMARK_ROLES})
    sc = screen_class(kind, shape, marks)
    descriptors: list[Descriptor] = []
    seen: set[str] = set()
    fields_with_value: list[str] = []
    primary: Descriptor | None = None
    control_classes: set[str] = set()
    for c in candidates:
        d = descriptor_for(c, vocab)
        control_classes.add(c.role)
        if d.key() not in seen:
            seen.add(d.key())
            descriptors.append(d)
        if c.role in FIELD_ROLES and c.has_value and d.key() not in fields_with_value:
            fields_with_value.append(d.key())
        if primary is None and c.role == "button" and not c.disabled and (c.primary or any(w in COMMIT_VOCABULARY for w in d.name.split())):
            primary = d
    l0 = [f"in:{sc}"]
    if dialog is not None:
        l0.append(f"ctx:{dialog_class(dialog, vocab)}")
    l1 = {
        "landmarks": marks,
        "modal": dialog is not None,
        "primary_button": primary.to_json() if primary else None,
        "control_classes": sorted(control_classes),
    }
    return Frame(
        kind=kind,
        url=url,
        title=title,
        sensitive=False,
        candidates=candidates,
        descriptors=descriptors,
        l0=sorted(l0),
        l1=l1,
        screen_class=sc,
        fields_with_value=fields_with_value,
        text_local=(text or "")[:MAX_TEXT],
        settled=settled,
        extra={"vocab": vocab} if vocab is not None else {},
    )


def l0_equal(a: list[str] | set[str], b: list[str] | set[str]) -> bool:
    """Node identity: two frames are one node iff their L0 sets are equal."""
    return set(a) == set(b)
