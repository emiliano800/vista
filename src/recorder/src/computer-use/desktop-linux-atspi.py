"""Lists the named controls of one window through AT-SPI2 (the Linux accessibility bus).

    python3 desktop-linux-atspi.py <pid> <window title>

Prints one JSON array: [{id, role, name, x, y, width, height, enabled}], screen coordinates.
Only applications that register on the bus are visible (GTK/Qt apps; Chromium/Electron with
accessibility on). Empty output means "nothing observable", never a guess.
"""

import json
import sys

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

MAX_ELEMENTS = 400
MAX_DEPTH = 40
ROLE_NAMES = {
    "text": "text field",
    "entry": "text field",
    "password text": "secure text field",
    "push button": "button",
    "toggle button": "button",
    "check box": "check box",
    "radio button": "radio button",
    "combo box": "combo box",
    "spin button": "text field",
    "link": "link",
    "menu item": "menu item",
    "check menu item": "menu item",
    "radio menu item": "menu item",
    "page tab": "tab",
    "table row": "row",
    "list item": "row",
    "tree item": "row",
    "table": "table",
    "tree table": "outline",
    "list box": "table",
}


def find_window(pid: int, title: str):
    desktop = Atspi.get_desktop(0)
    fallback = None
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None or app.get_process_id() != pid:
            continue
        for j in range(app.get_child_count()):
            win = app.get_child_at_index(j)
            if win is None:
                continue
            if (win.get_name() or "") == title:
                return win
            fallback = fallback or win
    return fallback


def walk(root):
    out = []
    stack = [(root, 0)]
    while stack and len(out) < MAX_ELEMENTS:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            continue
        try:
            states = node.get_state_set()
            if not states.contains(Atspi.StateType.SHOWING) and depth > 0:
                continue
            role = node.get_role_name() or ""
            name = node.get_name() or node.get_description() or ""
            mapped = ROLE_NAMES.get(role)
            if mapped and name.strip():
                ext = node.get_extents(Atspi.CoordType.SCREEN)
                out.append(
                    {
                        "id": f"ax:{len(out)}",
                        "role": mapped,
                        "name": name.strip()[:200],
                        "x": ext.x,
                        "y": ext.y,
                        "width": ext.width,
                        "height": ext.height,
                        "enabled": states.contains(Atspi.StateType.ENABLED),
                    }
                )
            for k in range(node.get_child_count() - 1, -1, -1):
                child = node.get_child_at_index(k)
                if child is not None:
                    stack.append((child, depth + 1))
        except Exception:  # noqa: BLE001 - a vanished node is not an error
            continue
    return out


def main() -> None:
    pid, title = int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else ""
    win = find_window(pid, title)
    json.dump(walk(win) if win is not None else [], sys.stdout)


if __name__ == "__main__":
    main()
