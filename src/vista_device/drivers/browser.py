"""Browser driver on browser-use's `BrowserSession` (observation + action layers only).

What is taken from browser-use: the merged DOM/AX snapshot (`get_browser_state_summary` →
`selector_map` of `EnhancedDOMTreeNode`), its event bus for clicks/typing/keys/navigation,
`allowed_domains` enforcement and the isolated profile. What is *not* taken: `Agent`, the
system prompt, any LLM client, telemetry. The library is pinned in `pyproject.toml`
(`[device]` extra) and is imported lazily so a recorder build without it still runs the
frame/leakage code paths.

`_candidates()` is the pure part (a list of node views → `Candidate`s) and is what the tests
cover; the session plumbing is exercised by the recorded device self-test only.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass

from taskmining.normalise import Vocabulary
from vista_device.drivers import ACTIONS, DriverError, Result
from vista_device.frame import DIALOG_ROLES, LANDMARK_ROLES, MAX_TEXT, Candidate, Frame, frame_from_candidates, position_class
from vista_device.settle import settle

try:
    from browser_use import BrowserSession
    from browser_use.browser.events import ClickElementEvent, NavigateToUrlEvent, SendKeysEvent, TypeTextEvent
except ImportError:  # the [device] extra is optional
    BrowserSession = None
    ClickElementEvent = NavigateToUrlEvent = SendKeysEvent = TypeTextEvent = None

MAX_CANDIDATES = 40
ROLE_KIND: dict[str, str] = {
    "textbox": "field",
    "searchbox": "field",
    "combobox": "field",
    "spinbutton": "field",
    "slider": "field",
    "link": "link",
    "button": "interactive",
    "checkbox": "interactive",
    "radio": "interactive",
    "switch": "interactive",
    "menuitem": "interactive",
    "menuitemcheckbox": "interactive",
    "menuitemradio": "interactive",
    "tab": "interactive",
    "option": "interactive",
    "treeitem": "interactive",
    "row": "row",
    "table": "record",
    "grid": "record",
}
TAG_ROLE: dict[str, str] = {
    "a": "link",
    "button": "button",
    "input": "textbox",
    "textarea": "textbox",
    "select": "combobox",
    "tr": "row",
    "table": "table",
    "nav": "navigation",
    "main": "main",
    "header": "banner",
    "footer": "contentinfo",
    "form": "form",
    "dialog": "dialog",
    "aside": "complementary",
}
INPUT_TYPE_ROLE: dict[str, str] = {
    "checkbox": "checkbox",
    "radio": "radio",
    "button": "button",
    "submit": "button",
    "search": "searchbox",
    "range": "slider",
    "number": "spinbutton",
}
KEY_NAMES: dict[str, str] = {
    "Enter": "Enter",
    "Tab": "Tab",
    "Escape": "Escape",
    "ArrowDown": "ArrowDown",
    "ArrowUp": "ArrowUp",
    "Backspace": "Backspace",
    "Space": "Space",
}
_SPACE = re.compile(r"\s+")


@dataclass
class NodeView:
    """The slice of an `EnhancedDOMTreeNode` the adapter reads; built by `node_view()` from
    the library object, or directly by tests."""

    index: int
    backend_node_id: int
    tag: str
    role: str | None
    name: str
    attributes: dict[str, str]
    ancestor_roles: tuple[str, ...]
    x: float | None = None
    y: float | None = None
    visible: bool = True
    disabled: bool = False
    has_value: bool = False


def role_of(tag: str, ax_role: str | None, attributes: dict[str, str]) -> str:
    explicit = (attributes.get("role") or "").strip().lower()
    if ax_role and ax_role not in {"generic", "none", "presentation", ""}:
        return ax_role
    if explicit:
        return explicit
    if tag == "input":
        return INPUT_TYPE_ROLE.get((attributes.get("type") or "text").lower(), "textbox")
    return TAG_ROLE.get(tag, tag)


def node_view(index: int, node: object) -> NodeView:  # node: browser_use EnhancedDOMTreeNode
    ax = node.ax_node
    attributes = dict(node.attributes or {})
    tag = node.tag_name.lower()
    name = (
        (ax.name if ax and ax.name else "")
        or attributes.get("aria-label")
        or attributes.get("placeholder")
        or attributes.get("title")
        or ""
    )
    if not name and tag not in {"input", "textarea", "select"}:
        name = node.get_all_children_text(max_depth=2)
    ancestors: list[str] = []
    p = node.parent_node
    while p is not None:
        pax = p.ax_node
        r = role_of(p.tag_name.lower(), pax.role if pax else None, dict(p.attributes or {}))
        if r in LANDMARK_ROLES or r in DIALOG_ROLES:
            ancestors.append(r)
        p = p.parent_node
    pos = node.absolute_position
    props = {pr.name: pr.value for pr in (ax.properties or [])} if ax else {}
    has_value = bool(attributes.get("value")) or bool(props.get("valuetext")) or (props.get("checked") is True)
    return NodeView(
        index=index,
        backend_node_id=node.backend_node_id,
        tag=tag,
        role=ax.role if ax else None,
        name=name,
        attributes=attributes,
        ancestor_roles=tuple(ancestors),
        x=pos.x if pos else None,
        y=pos.y if pos else None,
        visible=node.is_visible is not False,
        disabled=props.get("disabled") is True or "disabled" in attributes,
        has_value=has_value,
    )


def candidates_from_views(
    views: Iterable[NodeView], *, viewport: tuple[int, int] | None, limit: int = MAX_CANDIDATES
) -> tuple[list[Candidate], list[str], str | None]:
    """Views → (candidates, landmark roles present, front dialog name). Pure."""
    out: list[Candidate] = []
    seen: set[str] = set()
    landmarks: set[str] = set()
    dialog: str | None = None
    vw, vh = viewport or (None, None)
    for v in views:
        role = role_of(v.tag, v.role, v.attributes)
        landmarks.update(a for a in v.ancestor_roles if a in LANDMARK_ROLES)
        if role in LANDMARK_ROLES:
            landmarks.add(role)
        if role in DIALOG_ROLES and dialog is None:
            dialog = v.name or ""
        if any(a in DIALOG_ROLES for a in v.ancestor_roles) and dialog is None:
            dialog = ""
        kind = ROLE_KIND.get(role)
        if not kind or not v.visible:
            continue
        name = _SPACE.sub(" ", v.name or "").strip()[:200]
        if not name:
            continue
        key = f"{role}\0{name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        landmark = next((a for a in v.ancestor_roles if a in LANDMARK_ROLES), "")
        out.append(
            Candidate(
                id=str(v.index),
                role=role,
                name=name,
                kind=kind,
                landmark=landmark,
                position=position_class(v.x, v.y, vw, vh),
                has_value=v.has_value if kind == "field" else False,
                disabled=v.disabled,
                primary=(v.attributes.get("type") or "").lower() == "submit",
            )
        )
        if len(out) >= limit:
            break
    return out, sorted(landmarks), dialog


class BrowserDriver:
    kind = "browser"

    def __init__(
        self,
        *,
        allowed_domains: list[str] | None = None,
        user_data_dir: str | None = None,
        headless: bool = False,
        vocab: Vocabulary | None = None,
        screenshots: bool = False,
    ):
        if BrowserSession is None:
            raise DriverError("harness_unsupported", "browser-use is not installed in this recorder build (pip install 'vista[device]').")
        self.session = BrowserSession(allowed_domains=allowed_domains, user_data_dir=user_data_dir, headless=headless)
        self.vocab = vocab
        self.screenshots = screenshots
        self._started = False
        self._nodes: dict[str, object] = {}
        self._frame: Frame | None = None

    def capabilities(self) -> list[str]:
        return list(ACTIONS)

    async def _start(self) -> None:
        if not self._started:
            await self.session.start()
            self._started = True

    async def _fingerprint(self) -> str:
        state = await self.session.get_browser_state_summary(include_screenshot=False)
        return "|".join(
            f"{i}:{n.tag_name}:{(n.ax_node.name if n.ax_node else '') or ''}" for i, n in sorted(state.dom_state.selector_map.items())
        )

    async def observe(self) -> Frame:
        await self._start()
        settled = await settle(self._fingerprint)
        state = await self.session.get_browser_state_summary(include_screenshot=False)
        views = [node_view(i, n) for i, n in sorted(state.dom_state.selector_map.items())]
        viewport = (state.page_info.viewport_width, state.page_info.viewport_height) if state.page_info else None
        candidates, landmarks, dialog = candidates_from_views(views, viewport=viewport)
        self._nodes = {str(i): n for i, n in state.dom_state.selector_map.items()}
        text = ""
        page = await self.session.get_current_page()
        if page is not None:
            text = str(await page.evaluate("() => (document.body && document.body.innerText) || ''"))[:MAX_TEXT]
        self._frame = frame_from_candidates(
            kind="browser",
            url=state.url,
            title=state.title,
            candidates=candidates,
            landmarks=landmarks,
            dialog=dialog,
            text=text,
            vocab=self.vocab,
            settled=settled,
        )
        return self._frame

    def _node(self, step: dict) -> object:
        node = self._nodes.get(str(step.get("target_id")))
        if node is None:
            raise DriverError("stale_observation", "The target is not in the current observation.")
        return node

    async def perform(self, step: dict, frame: Frame | None) -> Result:
        action = str(step.get("action", ""))
        try:
            await self._start()
            bus = self.session.event_bus
            if action == "observe":
                return Result(True, "Looked at the page", frame=await self.observe())
            if action == "navigate":
                await bus.dispatch(NavigateToUrlEvent(url=str(step["value"])))
                f = await self.observe()
                return Result(
                    True,
                    "Opened a page" if f.sensitive else f"Opened {f.title or step['value']}",
                    frame=f,
                    result={"url_after": f.url, "title_after": f.title},
                )
            if action == "click":
                await bus.dispatch(ClickElementEvent(node=self._node(step)))
                f = await self.observe()
                return Result(
                    True, step.get("description") or "Clicked the control", frame=f, result={"url_after": f.url, "title_after": f.title}
                )
            if action == "type":
                node = self._node(step)
                previous = str(dict(node.attributes or {}).get("value") or "")
                await bus.dispatch(
                    TypeTextEvent(node=node, text=str(step["value"]), clear=step.get("replace", True) is not False, is_sensitive=True)
                )
                f = await self.observe()
                return Result(
                    True,
                    step.get("description") or "Typed the value into the field",
                    frame=f,
                    result={"previous_value": previous, "undo": {"action": "type", "target_id": step.get("target_id"), "value": previous}},
                )
            if action == "press":
                key = KEY_NAMES.get(str(step.get("value")))
                if key is None:
                    return Result.refused(action, "invalid_value", f"Unknown key {step.get('value')!r}.")
                await bus.dispatch(SendKeysEvent(keys=key))
                f = await self.observe()
                return Result(True, f"Pressed {key}", frame=f, result={"url_after": f.url, "title_after": f.title})
            if action == "extract":
                f = await self.observe()
                return Result(True, "Read the text", frame=f, result={"text": f.text_local})
            if action == "screenshot":
                if not self.screenshots:
                    return Result.refused(action, "consent_required", "Screenshots are not covered by this session's consent.")
                page = await self.session.must_get_current_page()
                data = await page.screenshot(format="png")
                return Result(
                    True, "Took a screenshot", frame=await self.observe(), evidence={"screenshot_base64": data, "content_type": "image/png"}
                )
            if action == "wait":
                await asyncio.sleep(float(step.get("value", 0)) / 1000)
                return Result(True, f"Waited {step.get('value')} ms", frame=await self.observe())
            return Result.refused(action, "invalid_value", f"The browser driver cannot {action}.")
        except DriverError as e:
            return Result.refused(action, e.code, str(e))
        except Exception as e:  # noqa: BLE001 - the run loop needs a refusal, not a crash
            return Result.refused(action, "harness_error", str(e)[:500])

    async def close(self) -> None:
        if self._started:
            await self.session.stop()
            self._started = False
