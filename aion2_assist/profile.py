"""Calibration profile: frame size, UI scale, regions, reference templates, hash."""
import hashlib
import json
from typing import Dict

from .frame import Frame, dist, mean_rgb, mean_vs

MIN_TEMPLATE_GAP = 10.0  # ready vs cooldown (or target vs absent) must differ at least this much


class ProfileMismatch(Exception):
    """Frame size / anchor / profile identity does not match the calibration."""


class CalibrationError(ValueError):
    pass


def compute_hash(d: dict) -> str:
    body = {k: v for k, v in d.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Profile:
    def __init__(self, data: dict):
        self.data = data
        if data.get("hash") != compute_hash(data):
            raise ProfileMismatch("profile hash does not match its contents (edited or corrupt)")
        self.frame_size = tuple(data["frame_size"])
        self.hash = data["hash"]

    def __getitem__(self, k):
        return self.data[k]

    def check_frame(self, frame: Frame):
        if (frame.width, frame.height) != self.frame_size:
            raise ProfileMismatch(f"frame {frame.width}x{frame.height} != profile {self.frame_size[0]}x{self.frame_size[1]}")


def load_profile(path: str) -> Profile:
    with open(path) as f:
        return Profile(json.load(f))


def save_profile(profile: Profile, path: str):
    with open(path, "w") as f:
        json.dump(profile.data, f, indent=1, sort_keys=True)


def _bar_color(frame: Frame, rect) -> list:
    x, y, w, h = rect
    band = frame.flat((x, y, min(3, w), h))  # leftmost columns: filled whenever HP/MP > 0
    return [round(c) for c in mean_rgb(band)]


def _check_gaps(what, refs):
    names = list(refs)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if dist(refs[a], refs[b]) < MIN_TEMPLATE_GAP:
                raise CalibrationError(f"{what}: '{a}' and '{b}' screenshots are indistinguishable")


def build_profile(spec: dict, shots: Dict[str, Frame]) -> Profile:
    """spec: {frame_size, ui_scale, regions{anchor,hp_bar,mp_bar,target,slots{},states{}}}.
    shots: 'normal' (bars non-empty, enemy targeted, all slots ready), 'cooldown' (all slots on
    cooldown), 'no_target', optional 'friendly_target', plus optional one shot per state name (dialog/loading/death...)."""
    size = tuple(spec["frame_size"])
    reg = spec["regions"]
    for need in ("normal", "cooldown", "no_target"):
        if need not in shots:
            raise CalibrationError(f"missing screenshot '{need}'")
    for name, fr in shots.items():
        if (fr.width, fr.height) != size:
            raise CalibrationError(
                f"screenshot '{name}' is {fr.width}x{fr.height} but regions are defined for full frames of "
                f"{size[0]}x{size[1]}; crops are not accepted")
    normal, cd, nt = shots["normal"], shots["cooldown"], shots["no_target"]

    def tpl(r):
        try:
            return list(normal.flat(tuple(r)))
        except ValueError as e:
            raise CalibrationError(str(e))

    t = {"anchor": tpl(reg["anchor"]), "hp_color": _bar_color(normal, tuple(reg["hp_bar"])),
         "mp_color": _bar_color(normal, tuple(reg["mp_bar"]))}
    tr = tuple(reg["target"])
    t["target"] = {"enemy": [round(c, 2) for c in mean_rgb(normal.flat(tr))],
                   "none": [round(c, 2) for c in mean_rgb(nt.flat(tr))]}
    if "friendly_target" in shots:  # optional: lets the assist refuse heals when an ally is targeted
        t["target"]["friendly"] = [round(c, 2) for c in mean_rgb(shots["friendly_target"].flat(tr))]
    _check_gaps("target", t["target"])
    t["slots"] = {}
    for sid, r in reg["slots"].items():
        ready = [round(v, 2) for v in mean_vs(normal.flat(tuple(r)))]
        cool = [round(v, 2) for v in mean_vs(cd.flat(tuple(r)))]
        t["slots"][sid] = {"ready": ready, "cooldown": cool}
        _check_gaps(f"slot {sid}", t["slots"][sid])
    t["states"] = {}
    regions_states = {}
    for name, r in reg.get("states", {}).items():
        if name in shots:
            t["states"][name] = list(shots[name].flat(tuple(r)))
            regions_states[name] = list(r)
    data = {"version": 1, "frame_size": list(size), "ui_scale": spec.get("ui_scale", 1.0),
            "regions": {**{k: list(v) for k, v in reg.items() if k not in ("slots", "states")},
                        "slots": {k: list(v) for k, v in reg["slots"].items()}, "states": regions_states},
            "thresholds": {"anchor": 0.92, "state": 0.95, "bar_tol": 30},
            "templates": t}
    data["hash"] = compute_hash(data)
    return Profile(data)
