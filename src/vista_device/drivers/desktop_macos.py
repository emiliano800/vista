"""Desktop driver on macOS-use's accessibility layer (`mlx_use.mac`): the front app's AX tree →
candidates/descriptors/L0 (title shape + landmark shape), actions through its `AXPress` /
value-set / key helpers. Only the tree builder, element node and action functions are used;
its agent, prompts and LLM clients never are.

Status: the adapter is declared here so the sidecar, the recorder and the run loop bind to one
surface; it has never executed on a macOS device (the design of record says so and the
harness is advertised only after a recorded self-test). `available()` is False anywhere pyobjc
or `mlx_use` is missing, so nothing is advertised until then.

`candidates_from_elements()` is pure and covered by tests with `ElementView`s; `node_views()`
turns `MacElementNode`s into them.
"""

from __future__ import annotations

import platform
import re
from collections.abc import Iterable
from dataclasses import dataclass

from taskmining.normalise import Vocabulary
from vista_device.drivers import ACTIONS, DriverError, Result
from vista_device.frame import Candidate, Frame, frame_from_candidates, position_class
from vista_device.settle import settle

try:
    from mlx_use.mac.actions import click as mac_click
    from mlx_use.mac.actions import type_into as mac_type
    from mlx_use.mac.tree import MacUITreeBuilder
except ImportError:
    mac_click = mac_type = None
    MacUITreeBuilder = None
try:
    from AppKit import NSWorkspace
except ImportError:
    NSWorkspace = None

MAX_CANDIDATES = 40
# AX role → (graph role, kind). Unlisted roles are not targets.
AX_ROLES: dict[str, tuple[str, str]] = {
    "AXTextField": ("textbox", "field"),
    "AXTextArea": ("textbox", "field"),
    "AXSearchField": ("searchbox", "field"),
    "AXComboBox": ("combobox", "field"),
    "AXPopUpButton": ("combobox", "field"),
    "AXIncrementor": ("spinbutton", "field"),
    "AXSlider": ("slider", "field"),
    "AXCheckBox": ("checkbox", "interactive"),
    "AXRadioButton": ("radio", "interactive"),
    "AXButton": ("button", "interactive"),
    "AXMenuItem": ("menuitem", "interactive"),
    "AXMenuButton": ("button", "interactive"),
    "AXLink": ("link", "link"),
    "AXTab": ("tab", "interactive"),
    "AXRow": ("row", "row"),
    "AXCell": ("cell", "row"),
    "AXTable": ("table", "record"),
    "AXOutline": ("grid", "record"),
}
AX_LANDMARKS: dict[str, str] = {
    "AXToolbar": "toolbar",
    "AXTabGroup": "tablist",
    "AXSheet": "dialog",
    "AXDialog": "dialog",
    "AXMenuBar": "navigation",
    "AXSplitGroup": "main",
    "AXScrollArea": "region",
    "AXGroup": "",
}
_SPACE = re.compile(r"\s+")


@dataclass
class ElementView:
    index: int
    ax_role: str
    name: str
    ancestor_roles: tuple[str, ...]
    x: float | None = None
    y: float | None = None
    enabled: bool = True
    has_value: bool = False
    is_default: bool = False


def available() -> bool:
    return platform.system() == "Darwin" and MacUITreeBuilder is not None


def candidates_from_elements(
    views: Iterable[ElementView], *, window: tuple[float, float] | None, limit: int = MAX_CANDIDATES
) -> tuple[list[Candidate], list[str], str | None]:
    out: list[Candidate] = []
    seen: set[str] = set()
    landmarks: set[str] = set()
    dialog: str | None = None
    ww, wh = window or (None, None)
    for v in views:
        marks = [AX_LANDMARKS.get(a, "") for a in v.ancestor_roles]
        landmarks.update(m for m in marks if m)
        if v.ax_role in {"AXSheet", "AXDialog"} and dialog is None:
            dialog = v.name or ""
        if "dialog" in marks and dialog is None:
            dialog = ""
        mapped = AX_ROLES.get(v.ax_role)
        if not mapped:
            continue
        role, kind = mapped
        name = _SPACE.sub(" ", v.name or "").strip()[:200]
        if not name:
            continue
        key = f"{role}\0{name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Candidate(
                id=str(v.index),
                role=role,
                name=name,
                kind=kind,
                landmark=next((m for m in marks if m), ""),
                position=position_class(v.x, v.y, ww, wh),
                has_value=v.has_value if kind == "field" else False,
                disabled=not v.enabled,
                primary=v.is_default,
            )
        )
        if len(out) >= limit:
            break
    return out, sorted(landmarks), dialog


def node_views(nodes: Iterable[object]) -> list[ElementView]:  # nodes: mlx_use MacElementNode with highlight_index
    views: list[ElementView] = []
    for n in nodes:
        if n.highlight_index is None:
            continue
        ancestors: list[str] = []
        p = n.parent
        while p is not None:
            ancestors.append(p.role)
            p = p.parent
        attrs = dict(n.attributes or {})
        pos = attrs.get("position")
        views.append(
            ElementView(
                index=int(n.highlight_index),
                ax_role=str(n.role),
                name=str(attrs.get("title") or attrs.get("description") or attrs.get("label") or ""),
                ancestor_roles=tuple(ancestors),
                x=float(pos[0]) if pos else None,
                y=float(pos[1]) if pos else None,
                enabled=bool(attrs.get("enabled", True)),
                has_value=bool(attrs.get("value")),
                is_default=bool(attrs.get("default", False)),
            )
        )
    return views


class DesktopDriver:
    kind = "desktop"

    def __init__(self, *, vocab: Vocabulary | None = None):
        if not available():
            raise DriverError("harness_unsupported", "macOS-use (mlx_use) is not available on this computer.")
        self.builder = MacUITreeBuilder()
        self.vocab = vocab
        self._nodes: dict[str, object] = {}
        self._pid: int | None = None

    def capabilities(self) -> list[str]:
        return [a for a in ACTIONS if a not in {"navigate", "screenshot", "extract"}]

    async def _tree(self):
        pid = self._pid if self._pid is not None else frontmost_pid()
        if pid is None:
            raise DriverError("no_target", "No application is bound to this session.")
        return await self.builder.build_tree(pid)

    async def _fingerprint(self) -> str:
        root = await self._tree()
        return "|".join(f"{v.ax_role}:{v.name}" for v in node_views(_walk(root)))

    def bind(self, pid: int) -> None:
        self._pid = pid

    async def observe(self) -> Frame:
        settled = await settle(self._fingerprint)
        root = await self._tree()
        nodes = list(_walk(root))
        views = node_views(nodes)
        candidates, landmarks, dialog = candidates_from_elements(views, window=None)
        self._nodes = {str(n.highlight_index): n for n in nodes if n.highlight_index is not None}
        title = str((root.attributes or {}).get("title") or "") if root is not None else ""
        return frame_from_candidates(
            kind="desktop",
            url="",
            title=title,
            candidates=candidates,
            landmarks=landmarks,
            dialog=dialog,
            vocab=self.vocab,
            settled=settled,
        )

    async def perform(self, step: dict, frame: Frame | None) -> Result:
        action = str(step.get("action", ""))
        try:
            if action == "observe":
                return Result(True, "Looked at the window", frame=await self.observe())
            node = self._nodes.get(str(step.get("target_id")))
            if action in {"click", "type"} and node is None:
                raise DriverError("stale_observation", "The target is not in the current observation.")
            if action == "click":
                action_name = next((a for a in ("AXPress", "AXClick", "AXOpen") if a in node.actions), "AXPress")
                if not mac_click(node, action_name):
                    raise DriverError("harness_error", "The element did not accept the press.")
                return Result(True, step.get("description") or "Clicked the control", frame=await self.observe())
            if action == "type":
                if not mac_type(node, str(step["value"])):
                    raise DriverError("harness_error", "The field did not accept the value.")
                return Result(True, step.get("description") or "Typed the value into the field", frame=await self.observe())
            return Result.refused(action, "invalid_value", f"The desktop driver cannot {action} yet.")
        except DriverError as e:
            return Result.refused(action, e.code, str(e))
        except Exception as e:  # noqa: BLE001
            return Result.refused(action, "harness_error", str(e)[:500])

    async def close(self) -> None:
        self._nodes = {}


def frontmost_pid() -> int | None:
    if NSWorkspace is None:
        return None
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(front.processIdentifier()) if front is not None else None


def _walk(root):
    stack = [root] if root is not None else []
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(list(n.children or [])))
