"""Binds file: slot -> key/role. The real skill binds are NOT in the game's local config files (server-side), so
the owner fills this in by hand; we never guess. Blank/UNSET keys, unknown key names, duplicates are refused."""
from .config import ROLES
from .keys import normalize_key
from .profile import Profile

FIELDS = ("key", "role", "cooldown", "cast", "gcd", "mp_cost", "priority", "duration", "enabled")


def make_template(profile: Profile) -> dict:
    slots = {}
    for i, (sid, rect) in enumerate(sorted(profile["regions"]["slots"].items())):
        slots[sid] = {"_position_xywh": rect, "_frame_size": profile["frame_size"],
                      "key": "", "role": "", "cooldown": 0, "cast": 0, "gcd": 0, "mp_cost": 0,
                      "priority": 10 * (i + 1), "duration": 0, "enabled": False}
    return {"_comment": "Fill key (real key name, e.g. '1', 'q', 'F5') and role (%s) for each slot you can use; "
                        "set enabled=true. Leave level-locked slots enabled=false." % "/".join(ROLES),
            "_profile_hash": profile.hash, "slots": slots}


def validate_binds(doc: dict, profile: Profile = None) -> list:
    problems, seen = [], {}
    slots = doc.get("slots")
    if not isinstance(slots, dict) or not slots:
        return ["no slots"]
    if profile and doc.get("_profile_hash") not in (None, profile.hash):
        problems.append("file was made for a different profile hash")
    for sid, s in slots.items():
        if profile and sid not in profile["regions"]["slots"]:
            problems.append(f"{sid}: no such slot in the profile")
        bad = [k for k in s if k not in FIELDS and not k.startswith("_")]
        if bad:
            problems.append(f"{sid}: unknown fields {bad}")
        if not s.get("enabled", False):
            continue  # disabled (e.g. level-locked) slots may stay blank
        raw = s.get("key", "")
        key = normalize_key(raw)
        if not raw or str(raw).upper().startswith("UNSET"):
            problems.append(f"{sid}: key is blank/UNSET")
        elif key is None:
            problems.append(f"{sid}: {raw!r} is not a real key name")
        elif key in seen:
            problems.append(f"{sid}: key {key!r} already used by {seen[key]}")
        else:
            seen[key] = sid
        if s.get("role") not in ROLES:
            problems.append(f"{sid}: role must be one of {ROLES}")
    return problems
