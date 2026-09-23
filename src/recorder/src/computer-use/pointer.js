// The computer's own pointer, through nut-js when that optional dependency is installed.
// The browser harness uses it so the employee sees the cursor travel to the control it
// is about to press; without it, browser clicks are dispatched to the page directly.
export async function loadNut() {
  try {
    const nut = await import('@nut-tree-fork/nut-js');
    nut.mouse.config.autoDelayMs = 20;
    nut.mouse.config.mouseSpeed = 900; // px/s: slow enough that a person can watch it travel
    nut.keyboard.config.autoDelayMs = 15;
    return nut;
  } catch {
    return null;
  }
}

export async function nutPointer(nut = loadNut()) {
  const n = await nut;
  if (!n) return null;
  return {
    moveTo: (x, y) => n.mouse.move(n.straightTo(new n.Point(x, y))),
    click: () => n.mouse.leftClick(),
  };
}
