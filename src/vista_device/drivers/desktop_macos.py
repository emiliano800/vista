"""Desktop driver on macOS-use's accessibility layer (`mlx_use.mac`): the front app's AX tree →
candidates/descriptors/L0 (title shape + landmark shape), actions through its `AXPress` /
value-set / scroll helpers; key presses through Quartz key events (macOS-use has no key
primitive), screenshots through `screencapture` to a device-local file. Only the tree builder,
element node and action functions are used; its agent, prompts and LLM clients never are.

Status: never executed on a macOS device by us. `available()` is False anywhere pyobjc or
`mlx_use` is missing, and the harness is advertised to the run loop only after the recorded
self-test (`python -m vista_device selftest desktop`, or the recorder's probe) has passed on
that computer — see `vista_device.selftest`.

`candidates_from_elements()`, `text_of_views()` and `KEY_CODES` are pure and covered by tests
with `ElementView`s; `node_views()` turns `MacElementNode`s into them.
"""

from __future__ import annotations

import asyncio
import platform
import re
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from taskmining.normalise import Vocabulary
from vista_device.drivers import ACTIONS, DriverError, Result
from vista_device.frame import Candidate, Frame, frame_from_candidates, position_class
from vista_device.settle import settle

try:
    from mlx_use.mac.actions import click as mac_click
    from mlx_use.mac.actions import scroll as mac_scroll
    from mlx_use.mac.actions import type_into as mac_type
    from mlx_use.mac.tree import MacUITreeBuilder
except ImportError:
    mac_click = mac_type = mac_scroll = None
    MacUITreeBuilder = None
try:
    from AppKit import NSWorkspace
except ImportError:
    NSWorkspace = None
try:
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGEventFlagMaskCommand,
        kCGEventFlagMaskShift,
        kCGHIDEventTap,
    )
except ImportError:
    CGEventCreateKeyboardEvent = CGEventPost = CGEventSetFlags = None
    kCGEventFlagMaskCommand = kCGEventFlagMaskShift = kCGHIDEventTap = 0

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
# The keys the run loop may press, as macOS virtual key codes (Carbon `kVK_*`). Anything else is
# refused: no arbitrary key events, no shortcuts the graph did not record as `press`.
KEY_CODES: dict[str, int] = {
    "Enter": 0x24,
    "Tab": 0x30,
    "Escape": 0x35,
    "ArrowDown": 0x7D,
    "ArrowUp": 0x7E,
    "Backspace": 0x33,
    "Space": 0x31,
}
# Recorded shortcuts the graph may carry as `press` values (`Cmd+S` style); the modifier set is
# closed on purpose — Cmd+Q / Cmd+W never became a primitive.
SHORTCUTS: dict[str, tuple[int, int]] = {
    "Cmd+S": (0x01, 1),
    "Cmd+Enter": (0x24, 1),
    "Cmd+C": (0x08, 1),
    "Cmd+V": (0x09, 1),
    "Cmd+A": (0x00, 1),
    "Shift+Tab": (0x30, 2),
}
MAX_TEXT = 4000
SCROLL_DIRECTIONS = frozenset({"up", "down", "left", "right"})


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
    value: str = ""


def available() -> bool:
    return platform.system() == "Darwin" and MacUITreeBuilder is not None


def text_of_views(views: Iterable[ElementView], limit: int = MAX_TEXT) -> str:
    """The window's readable text, device only: static text, field values and names in tree order."""
    parts: list[str] = []
    size = 0
    for v in views:
        for piece in (v.name, v.value):
            piece = _SPACE.sub(" ", piece or "").strip()
            if not piece or (parts and parts[-1] == piece):
                continue
            parts.append(piece)
            size += len(piece) + 1
            if size >= limit:
                return " ".join(parts)[:limit]
    return " ".join(parts)


def key_event(value: str) -> tuple[int, int] | None:
    """(virtual key code, modifier mask index) for a `press` value, or None when it is not allowed."""
    if value in KEY_CODES:
        return KEY_CODES[value], 0
    return SHORTCUTS.get(value)


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


def node_views(nodes: Iterable[object], *, include_context: bool = False) -> list[ElementView]:  # nodes: mlx_use MacElementNode
    views: list[ElementView] = []
    for n in nodes:
        if n.highlight_index is None and not include_context:
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
                index=int(n.highlight_index) if n.highlight_index is not None else -1,
                ax_role=str(n.role),
                name=str(attrs.get("title") or attrs.get("description") or attrs.get("label") or ""),
                ancestor_roles=tuple(ancestors),
                x=float(pos[0]) if pos else None,
                y=float(pos[1]) if pos else None,
                enabled=bool(attrs.get("enabled", True)),
                has_value=bool(attrs.get("value")),
                is_default=bool(attrs.get("default", False)),
                value=str(attrs.get("value") or "") if str(n.role) in {"AXStaticText", "AXTextField", "AXTextArea"} else "",
            )
        )
    return views


class DesktopDriver:
    kind = "desktop"

    def __init__(
        self, *, vocab: Vocabulary | None = None, screenshots: bool = False, screenshot_dir: str | None = None, app: str | None = None
    ):
        if not available():
            raise DriverError("harness_unsupported", "macOS-use (mlx_use) is not available on this computer.")
        self.builder = MacUITreeBuilder()
        self.vocab = vocab
        self.screenshots = screenshots
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self._nodes: dict[str, object] = {}
        self._views: list[ElementView] = []
        self._pid: int | None = None
        self._window: tuple[float, float] | None = None
        if app:
            self.bind(launch(app))

    def capabilities(self) -> list[str]:
        out = [a for a in ACTIONS if a != "navigate"]
        if not self.screenshots:
            out.remove("screenshot")
        return out

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
        self._window = _window_size(nodes)
        self._views = node_views(nodes, include_context=True)
        candidates, landmarks, dialog = candidates_from_elements((v for v in self._views if v.index >= 0), window=self._window)
        self._nodes = {str(n.highlight_index): n for n in nodes if n.highlight_index is not None}
        title = _window_title(nodes) or (str((root.attributes or {}).get("title") or "") if root is not None else "")
        return frame_from_candidates(
            kind="desktop",
            url="",
            title=title,
            candidates=candidates,
            landmarks=landmarks,
            dialog=dialog,
            text=text_of_views(self._views),
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
                previous = str(dict(node.attributes or {}).get("value") or "")
                if not mac_type(node, str(step["value"])):
                    raise DriverError("harness_error", "The field did not accept the value.")
                return Result(
                    True,
                    step.get("description") or "Typed the value into the field",
                    frame=await self.observe(),
                    result={"previous_value": previous, "undo": {"action": "type", "target_id": step.get("target_id"), "value": previous}},
                )
            if action == "press":
                key = key_event(str(step.get("value")))
                if key is None:
                    return Result.refused(action, "invalid_value", f"Unknown key {step.get('value')!r}.")
                press_key(*key)
                f = await self.observe()
                return Result(True, f"Pressed {step.get('value')}", frame=f, result={"title_after": f.title})
            if action == "scroll":
                direction = str(step.get("value", "down"))
                if direction not in SCROLL_DIRECTIONS:
                    return Result.refused(action, "invalid_value", f"Unknown scroll direction {direction!r}.")
                target = node if node is not None else self._scrollable()
                if target is None or not mac_scroll(target, direction):
                    raise DriverError("harness_error", "Nothing scrolled.")
                return Result(True, f"Scrolled {direction}", frame=await self.observe())
            if action == "extract":
                f = await self.observe()
                return Result(True, "Read the text", frame=f, result={"text": f.text_local})
            if action == "screenshot":
                if not self.screenshots or self.screenshot_dir is None:
                    return Result.refused(action, "consent_required", "Screenshots are not covered by this session's consent.")
                path = screenshot(self.screenshot_dir)
                return Result(
                    True,
                    "Took a screenshot",
                    frame=await self.observe(),
                    evidence={"screenshot_path": str(path), "content_type": "image/png"},
                )
            if action == "wait":
                await asyncio.sleep(float(step.get("value", 0)) / 1000)
                return Result(True, f"Waited {step.get('value')} ms", frame=await self.observe())
            return Result.refused(action, "invalid_value", f"The desktop driver cannot {action}.")
        except DriverError as e:
            return Result.refused(action, e.code, str(e))
        except Exception as e:  # noqa: BLE001
            return Result.refused(action, "harness_error", str(e)[:500])

    def _scrollable(self):
        return next((n for n in self._nodes.values() if any(a.startswith("AXScroll") for a in n.actions)), None)

    async def close(self) -> None:
        self._nodes = {}
        self._views = []
        self.builder.cleanup()


def frontmost_pid() -> int | None:
    if NSWorkspace is None:
        return None
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(front.processIdentifier()) if front is not None else None


def launch(app: str, timeout_s: float = 10.0) -> int:
    """Open (or bring forward) an application by name or bundle id and return its pid."""
    if NSWorkspace is None:
        raise DriverError("harness_unsupported", "AppKit is not available on this computer.")
    ws = NSWorkspace.sharedWorkspace()
    if "." in app and not ws.launchAppWithBundleIdentifier_options_additionalEventParamDescriptor_launchIdentifier_(app, 0, None, None):
        raise DriverError("no_target", f"Could not open {app!r}.")
    if "." not in app and not ws.launchApplication_(app):
        raise DriverError("no_target", f"Could not open {app!r}.")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for running in ws.runningApplications():
            if app in {str(running.bundleIdentifier() or ""), str(running.localizedName() or "")}:
                running.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
                return int(running.processIdentifier())
        time.sleep(0.1)
    raise DriverError("no_target", f"{app!r} did not start in time.")


def press_key(code: int, modifier: int) -> None:
    if CGEventCreateKeyboardEvent is None:
        raise DriverError("harness_unsupported", "Quartz is not available on this computer.")
    flags = {0: 0, 1: kCGEventFlagMaskCommand, 2: kCGEventFlagMaskShift}[modifier]
    for down in (True, False):
        event = CGEventCreateKeyboardEvent(None, code, down)
        if flags:
            CGEventSetFlags(event, flags)
        CGEventPost(kCGHIDEventTap, event)


def screenshot(directory: Path) -> Path:
    """Front window to a PNG under the device store; the file never leaves the device by itself."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"shot-{int(time.time() * 1000)}.png"
    proc = subprocess.run(["screencapture", "-x", "-t", "png", str(path)], capture_output=True, timeout=15, check=False)
    if proc.returncode != 0 or not path.exists():
        raise DriverError("harness_error", "screencapture failed.")
    return path


def _window_size(nodes: list) -> tuple[float, float] | None:
    for n in nodes:
        if str(n.role) == "AXWindow":
            size = dict(n.attributes or {}).get("size")
            if size:
                return float(size[0]), float(size[1])
    return None


def _window_title(nodes: list) -> str:
    for n in nodes:
        if str(n.role) == "AXWindow":
            return str(dict(n.attributes or {}).get("title") or "")
    return ""


def _walk(root):
    stack = [root] if root is not None else []
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(list(n.children or [])))
