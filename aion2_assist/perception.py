"""Readers over a calibrated Frame. Every reading is (value, confidence, frame_timestamp)."""
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


def classify2(stat, a, b):
    """Nearest of two reference stats -> ('a'|'b', confidence)."""
    da, db, gap = dist(stat, a), dist(stat, b), dist(a, b)
    conf = max(0.0, 1.0 - min(da, db) / gap) * abs(da - db) / (da + db + 1e-9)
    return ("a" if da <= db else "b"), conf


@dataclass
class Observation:
    ts: float
    hp: Reading
    mp: Reading
    anchor: Reading           # value True = HUD anchor matches
    target: Reading           # value bool
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
        k, c = classify2(mean_rgb(frame.flat(tuple(reg["target"]))), tpl["target"]["present"], tpl["target"]["absent"])
        target = Reading(k == "a", c, ts)
        slots = {}
        for sid, r in reg["slots"].items():
            k, c = classify2(mean_vs(frame.flat(tuple(r))), tpl["slots"][sid]["ready"], tpl["slots"][sid]["cooldown"])
            slots[sid] = Reading("ready" if k == "a" else "cooldown", c, ts)
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
