// The computer's own pointer, through nut-js when that optional dependency is installed.
// The harnesses use it so the employee sees the real cursor on the control being pressed;
// without it, browser clicks are dispatched to the page directly. The pointer jumps
// straight to the control (no glide) — the in-page halo marks where it landed.
export async function loadNut() {
  try {
    const nut = await import('@nut-tree-fork/nut-js');
    nut.mouse.config.autoDelayMs = 0;
    nut.keyboard.config.autoDelayMs = 10;
    return nut;
  } catch {
    return null;
  }
}

export const moveTo = (n, x, y) => n.mouse.setPosition(new n.Point(x, y));

export async function nutPointer(nut = loadNut()) {
  const n = await nut;
  if (!n) return null;
  return {
    moveTo: (x, y) => moveTo(n, x, y),
    click: () => n.mouse.leftClick(),
  };
}
