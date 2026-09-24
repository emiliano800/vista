"""The device sidecar: one `observe()` for recording and execution.

`vista-device` runs beside the recorder (spawned by `main.js`, spoken to over newline-delimited
JSON on stdin/stdout) and owns the two open-source observation/action layers:

- browser-use (`browser_use.BrowserSession`) for pages — `drivers/browser.py`;
- macOS-use (`mlx_use.mac`) for the front window's accessibility tree — `drivers/desktop_macos.py`
  (step 5; the adapter surface is declared here so the recorder and the run loop bind to it once).

Neither library's agent, prompt or LLM code is used: Jev stays the only policy. What both sides
receive is a `Frame` (`frame.py`) — bounded candidates with typed descriptors, the code-computed
L0/L1 state and a page-text excerpt that never leaves the device — so a recorded frame and a
run-time frame come from the same function by construction (design §2b gap 1).
"""

from vista_device.frame import Frame, frame_from_candidates

__all__ = ["Frame", "frame_from_candidates"]
