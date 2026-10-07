"""Readers over a calibrated Frame. Every reading is (value, confidence, frame_timestamp)."""
import math
from dataclasses import dataclass
from typing import Dict, Optional

from .frame import Frame, Reading, dist, mean_rgb, mean_vs, template_score
from .profile import Profile


def read_bar(frame: Frame, rect, color, tol) -> Reading:
    """Fill fraction = contiguous run of colour-matching columns from the left."""
    rows = frame.crop_rows(rect)
    w, h = rect[2], rect[3]
    match = []
    for x in range(w):
        n = 0
        for row in rows:
            i = 3 * x
            if abs(row[i] - color[0]) <= tol and abs(row[i + 1] - color[1]) <= tol and abs(row[i + 2] - color[2]) <= tol:
                n += 1
        match.append(2 * n >= h)
    run = 0
    while run < w and match[run]:
        run += 1
    stray = sum(match[run:])  # matching columns after the first gap => ambiguous bar
    conf = 1.0 - (min(1.0, stray / (w - run)) if w > run else 0.0)
    if run == 0:
        conf = min(conf, 0.5)  # empty is indistinguishable from "wrong HUD"
    return Reading(run / w, conf, frame.ts)


def valid_fraction(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x) and 0.0 <= x <= 1.0


def classifyn(stat, refs: dict):
    """Nearest reference -> (name, confidence). Confident only when close to the nearest
    reference relative to its gap to the other references, and clearly nearer than the runner-up."""
    ds = sorted((dist(stat, r), n) for n, r in refs.items())
    (d1, n1), (d2, _) = ds[0], ds[1]
    gap = min(dist(refs[n1], r) for n, r in refs.items() if n != n1)
    return n1, max(0.0, 1.0 - d1 / gap) * (d2 - d1) / (d2 + d1 + 1e-9)


@dataclass
class Observation:
    ts: float
    hp: Reading
    mp: Reading
    anchor: Reading           # value True = HUD anchor matches
    target: Reading           # value 'enemy' | 'none' | 'friendly'
    state: Reading            # value None or 'dialog'/'loading'/'death'/...
    slots: Dict[str, Reading]  # value 'ready' | 'cooldown'


class Perceiver:
    def __init__(self, profile: Profile):
        self.p = profile

    def read(self, frame: Frame) -> Observation:
        p = self.p
        p.check_frame(frame)
        reg, tpl, thr = p["regions"], p["templates"], p["thresholds"]
        ts = frame.ts
        score = template_score(frame.flat(tuple(reg["anchor"])), tpl["anchor"])
        ok = score >= thr["anchor"]
        anchor = Reading(ok, score if ok else 1.0 - score, ts)
        hp = read_bar(frame, tuple(reg["hp_bar"]), tpl["hp_color"], thr["bar_tol"])
        mp = read_bar(frame, tuple(reg["mp_bar"]), tpl["mp_color"], thr["bar_tol"])
        k, c = classifyn(mean_rgb(frame.flat(tuple(reg["target"]))), tpl["target"])
        target = Reading(k, c, ts)  # 'enemy' | 'none' | 'friendly' (friendly only if calibrated)
        slots = {}
        for sid, r in reg["slots"].items():
            k, c = classifyn(mean_vs(frame.flat(tuple(r))), tpl["slots"][sid])
            slots[sid] = Reading(k, c, ts)  # 'ready' | 'cooldown'
        state = self._state(frame, reg, tpl, thr, ts)
        return Observation(ts, hp, mp, anchor, target, state, slots)

    @staticmethod
    def _state(frame, reg, tpl, thr, ts) -> Reading:
        best, bscore = None, 0.0
        for name, t in tpl["states"].items():
            s = template_score(frame.flat(tuple(reg["states"][name])), t)
            if s > bscore:
                best, bscore = name, s
        if bscore >= thr["state"]:
            return Reading(best, bscore, ts)
        if bscore > thr["state"] - 0.15:  # ambiguous band: neither clearly present nor absent
            return Reading(best, 0.4, ts)
        return Reading(None, 1.0, ts)
