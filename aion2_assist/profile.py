"""Calibration profile: frame size, UI scale, regions, reference templates, hash."""
import hashlib
import json
import math
import os
import tempfile
from typing import Dict

from .frame import Frame, dist, mean_rgb, mean_vs

MIN_TEMPLATE_GAP = 10.0  # ready vs cooldown (or target vs absent) must differ at least this much
BAR_FILLS = ("left", "right")  # the end a bar's fill is anchored to; it empties toward the other end
REQUIRED_REGIONS = ("anchor", "hp_bar", "mp_bar", "target", "slots")


def _check_bar_fill(value):
    """Optional {"hp": "left"|"right", "mp": ...}; a missing bar means "left" (the original behaviour)."""
    if not isinstance(value, dict) or not set(value) <= {"hp", "mp"} or any(v not in BAR_FILLS for v in value.values()):
        raise ValueError(f"bar_fill must map 'hp'/'mp' to one of {BAR_FILLS}")


class ProfileMismatch(Exception):
    """Frame size / anchor / profile identity does not match the calibration."""


class CalibrationError(ValueError):
    pass


def compute_hash(d: dict) -> str:
    body = {k: v for k, v in d.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Profile:
    def __init__(self, data: dict):
        if not isinstance(data, dict):
            raise ProfileMismatch("profile must be an object")
        self.data = data
        if data.get("hash") != compute_hash(data):
            raise ProfileMismatch("profile hash does not match its contents (edited or corrupt)")
        try:
            self._validate(data)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ProfileMismatch(f"invalid calibration profile: {e}") from e
        self.frame_size = tuple(data["frame_size"])
        self.hash = data["hash"]

    @staticmethod
    def _validate(d):
        def numeric(v, low, high):
            if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
                raise ValueError(f"calibration number must be finite in [{low},{high}]")

        def vector(v, count):
            if not isinstance(v, list) or len(v) != count:
                raise ValueError("calibration vector length mismatch")
            for n in v:
                numeric(n, 0, 255)

        if d["version"] != 1:
            raise ValueError("unsupported profile version")
        size = d["frame_size"]
        if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v < 1 for v in size):
            raise ValueError("invalid frame_size")
        numeric(d["ui_scale"], 0.01, 100)
        if "bar_fill" in d:
            _check_bar_fill(d["bar_fill"])
        thresholds, regions, templates = d["thresholds"], d["regions"], d["templates"]
        numeric(thresholds["anchor"], 0.7, 1)
        numeric(thresholds["state"], 0.7, 1)
        numeric(thresholds["bar_tol"], 0, 255)
        def rect(r):
            if (not isinstance(r, list) or len(r) != 4 or any(type(v) is not int for v in r)
                    or min(r[:2]) < 0 or min(r[2:]) < 1 or r[0]+r[2] > size[0] or r[1]+r[3] > size[1]):
                raise ValueError("invalid full-frame rectangle")
            return r[2]*r[3]*3
        for key in ("anchor", "hp_bar", "mp_bar", "target"):
            rect(regions[key])
        vector(templates["anchor"], rect(regions["anchor"]))
        vector(templates["hp_color"], 3)
        vector(templates["mp_color"], 3)
        targets = templates["target"]
        if not {"enemy", "none"} <= targets.keys() or not targets.keys() <= {"enemy", "none", "friendly"}:
            raise ValueError("invalid target references")
        for v in targets.values():
            vector(v, 3)
        _check_gaps("target", targets)
        for group in ("slots", "states"):
            if set(regions[group]) != set(templates[group]):
                raise ValueError(f"{group} regions/templates differ")
            for name, r in regions[group].items():
                count = rect(r)
                t = templates[group][name]
                if group == "states":
                    vector(t, count)
                else:
                    if set(t) != {"ready", "cooldown"}:
                        raise ValueError("invalid slot references")
                    for v in t.values():
                        vector(v, 2)
                    _check_gaps(name, t)

    def __getitem__(self, k):
        return self.data[k]

    def bar_fill(self, bar: str) -> str:
        """'left' or 'right': the end the hp/mp bar's fill is anchored to."""
        return self.data.get("bar_fill", {}).get(bar, "left")

    def check_frame(self, frame: Frame):
        if (frame.width, frame.height) != self.frame_size:
            raise ProfileMismatch(f"frame {frame.width}x{frame.height} != profile {self.frame_size[0]}x{self.frame_size[1]}")


def load_profile(path: str) -> Profile:
    with open(path) as f:
        return Profile(json.load(f))


def save_profile(profile: Profile, path: str):
    """Publish a complete profile at a new path; never replace existing files or symlinks."""
    data = json.dumps(profile.data, indent=1, sort_keys=True)
    # Do not resolve the final component: even a dangling symlink must count as an existing destination.
    destination = os.path.abspath(path)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(destination),
                                     prefix=".aion2-profile-", suffix=".tmp") as staged:
        staged.write(data)
        staged.flush()
        os.fsync(staged.fileno())
        # Same-directory hard linking is atomic and fails if another writer claimed the name first.
        # There is deliberately no replace/rename fallback that could clobber an existing profile.
        os.link(staged.name, destination)


def _bar_color(frame: Frame, rect, fill="left") -> list:
    x, y, w, h = rect
    n = min(3, w)
    band = frame.flat((x if fill == "left" else x + w - n, y, n, h))  # anchored end: filled whenever HP/MP > 0
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
    for key in ("frame_size", "regions"):
        if key not in spec:
            raise CalibrationError(f"regions spec has no '{key}'")
    size = tuple(spec["frame_size"])
    reg = spec["regions"]
    missing = [k for k in REQUIRED_REGIONS if k not in reg]
    if missing:
        raise CalibrationError(f"regions spec does not set {', '.join(missing)} yet; measure "
                               f"{'it' if len(missing) == 1 else 'them'} on a real full-frame screenshot first")
    fill = spec.get("bar_fill")
    if fill is not None:
        try:
            _check_bar_fill(fill)
        except ValueError as e:
            raise CalibrationError(str(e))
    fill = dict(fill or {})
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

    t = {"anchor": tpl(reg["anchor"]), "hp_color": _bar_color(normal, tuple(reg["hp_bar"]), fill.get("hp", "left")),
         "mp_color": _bar_color(normal, tuple(reg["mp_bar"]), fill.get("mp", "left"))}
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
    if fill:  # stored only when given, so profiles without it keep their contents and hashes
        data["bar_fill"] = fill
    data["hash"] = compute_hash(data)
    return Profile(data)
