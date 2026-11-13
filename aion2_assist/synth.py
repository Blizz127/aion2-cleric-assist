"""Synthetic frames/profile for tests and the replay simulator (not real Aion 2 UI)."""
from .frame import Frame
from .profile import build_profile

W, H = 320, 180
BG = (30, 30, 36)
REGIONS = {
    "frame_size": [W, H], "ui_scale": 1.0,
    "regions": {
        "anchor": [4, 4, 24, 12], "hp_bar": [34, 4, 100, 8], "mp_bar": [34, 16, 100, 8],
        "target": [150, 4, 30, 10],
        "slots": {f"s{i}": [10 + 28 * (i - 1), 150, 22, 22] for i in range(1, 6)},
        "states": {"dialog": [100, 60, 80, 30], "loading": [100, 100, 80, 20], "death": [200, 60, 60, 30]},
    },
}
HP_C, MP_C = (200, 40, 40), (40, 90, 210)
TARGET_C = {"enemy": (220, 180, 40), "none": BG, "friendly": (60, 200, 90)}
SLOT_HUES = [(220, 60, 60), (60, 200, 80), (70, 90, 230), (230, 200, 60), (200, 70, 200)]
STATE_C = {"dialog": (235, 235, 235), "loading": (250, 250, 20), "death": (120, 0, 0)}


def _fill(buf, rect, color):
    x, y, w, h = rect
    for yy in range(y, y + h):
        buf[yy][3 * x:3 * (x + w)] = bytes(color) * w


def make_frame(ts, hp=1.0, mp=1.0, target="enemy", cooldown=(), state=None, anchor=True, size=(W, H),
               mp_stray=False, hp_stray=False, bar_fill=None):
    """Render a synthetic HUD. `cooldown` = slot ids shown desaturated/dark.
    `bar_fill` = {"hp"/"mp": "right"} draws that bar anchored at its right end (default: left)."""
    fills = bar_fill or {}
    w, h = size
    buf = [bytearray(bytes(BG) * w) for _ in range(h)]
    r = REGIONS["regions"]
    if anchor:
        x, y, aw, ah = r["anchor"]
        for yy in range(y, y + ah):
            for xx in range(x, x + aw):
                buf[yy][3 * xx:3 * xx + 3] = bytes(((xx * 13 + yy * 7) % 200 + 40, (xx * 5) % 200 + 40, (yy * 29) % 200 + 40))
    for bar, color, val, stray in (("hp", HP_C, hp, hp_stray), ("mp", MP_C, mp, mp_stray)):
        x, y, bw, bh = r[f"{bar}_bar"]
        right = fills.get(bar, "left") == "right"
        n = max(0, min(bw, round(bw * val))) if val == val else 0
        _fill(buf, (x, y, bw, bh), (60, 60, 60))
        if n:
            _fill(buf, (x + bw - n if right else x, y, n, bh), color)
        if stray:  # coloured chunk after a gap: ambiguous bar
            _fill(buf, (x + 10 if right else x + bw - 40, y, 30, bh), color)
    _fill(buf, tuple(r["target"]), TARGET_C[target])
    for i, (sid, rect) in enumerate(r["slots"].items()):
        c = SLOT_HUES[i]
        _fill(buf, tuple(rect), (70, 70, 74) if sid in cooldown else c)
    if state:
        _fill(buf, tuple(r["states"][state]), STATE_C[state])
    return Frame(w, h, [bytes(b) for b in buf], ts)


def synthetic_profile():
    shots = {
        "normal": make_frame(0.0, hp=0.8, mp=0.8),
        "cooldown": make_frame(0.0, hp=0.8, mp=0.8, cooldown=tuple(REGIONS["regions"]["slots"])),
        "no_target": make_frame(0.0, hp=0.8, mp=0.8, target="none"),
        "friendly_target": make_frame(0.0, hp=0.8, mp=0.8, target="friendly"),
    }
    for st in STATE_C:
        shots[st] = make_frame(0.0, hp=0.8, mp=0.8, state=st)
    return build_profile(REGIONS, shots)
