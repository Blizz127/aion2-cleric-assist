"""Default-refusing live gates. `run` starts only if EVERY gate passes; otherwise the missing ones are listed."""
import os
import stat
from dataclasses import dataclass
from typing import List, Optional

from .config import Config, ConfigError, load_config
from .profile import Profile, ProfileMismatch, load_profile

DEFAULT_GATES_FILE = "~/.config/aion2-assist/LIVE-APPROVED"


@dataclass
class Gate:
    name: str
    ok: bool
    why: str = ""


def read_gates_file(path: str, profile_hash: str):
    """-> (ok, why). Owner-created file, not writable by others, containing the profile hash on its own line."""
    p = os.path.expanduser(path)
    try:
        st = os.stat(p)
    except OSError:
        return False, f"{p} does not exist (owner creates it: `aion2_assist approve ...` or write the profile hash into it)"
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
        return False, f"{p} must be a regular file owned by you and not group/world-writable"
    with open(p) as f:
        lines = [l.split("#")[0].strip() for l in f.read().splitlines()]
    if profile_hash not in lines:
        return False, f"{p} does not contain this profile's hash {profile_hash[:12]}... (profile changed since approval?)"
    return True, ""


def evaluate_static(accept_flag: bool, config_path: Optional[str], profile_path: Optional[str],
                    gates_file: str = DEFAULT_GATES_FILE):
    """All gates except the self-check (which needs the live read-only monitor). -> (gates, cfg, profile)."""
    gates: List[Gate] = [Gate("accept_live_risk", accept_flag, "" if accept_flag else "pass --i-accept-live-risk")]
    cfg: Optional[Config] = None
    prof: Optional[Profile] = None
    why_cfg = ""
    if not config_path:
        why_cfg = "pass --config"
    else:
        try:
            cfg = load_config(config_path)
            cfg.validate_for_run()
        except (ConfigError, OSError, ValueError) as e:
            why_cfg, cfg = str(e) or type(e).__name__, None
    gates.append(Gate("config_valid", cfg is not None, why_cfg or ""))
    why_p = ""
    if not profile_path:
        why_p = "pass --profile"
    else:
        try:
            prof = load_profile(profile_path)
        except (OSError, ValueError, ProfileMismatch, KeyError) as e:
            why_p = f"profile unreadable or its hash does not match its contents: {e}"
    gates.append(Gate("profile_valid", prof is not None, why_p))
    pin_ok, pin_why = False, ""
    if cfg is None or prof is None:
        pin_why = "not evaluated (needs a valid config and profile)"
    elif cfg.profile_hash != prof.hash:
        pin_why = f"config profile_hash ({cfg.profile_hash and cfg.profile_hash[:12]}) must equal the profile hash ({prof.hash[:12]}...)"
    else:
        pin_ok = True
    gates.append(Gate("profile_hash_pinned_and_matches", pin_ok, pin_why))
    wm = bool(cfg and "window_match" in cfg.explicit_keys and cfg.window_match.strip())
    gates.append(Gate("window_match_set", wm, "" if wm else "set an explicit non-empty window_match in the config"))
    ic = bool(cfg and "ignore_codes" in cfg.explicit_keys and cfg.ignore_codes)
    gates.append(Gate("ignore_codes_set", ic, "" if ic else
                      "set ignore_codes (evdev codes of the toggle/emergency/hold hotkeys) in the config"))
    if prof is None:
        gates.append(Gate("gates_file", False, "not evaluated (needs a valid profile)"))
    else:
        ok, why = read_gates_file(gates_file, prof.hash)
        gates.append(Gate("gates_file", ok, why))
    return gates, cfg, prof


def self_check_gate(note: str, ok: bool) -> Gate:
    return Gate("self_check", ok, "" if ok else note)


def not_attempted_self_check() -> Gate:
    return Gate("self_check", False, "not evaluated: runs only after every other gate passes (live read-only monitor)")


def report(gates: List[Gate]) -> str:
    missing = [g for g in gates if not g.ok]
    lines = [f"REFUSING TO START: {len(missing)} gate(s) missing"] if missing else ["all gates passed"]
    lines += [f"  [{'MISSING' if not g.ok else 'ok     '}] {g.name}" + (f": {g.why}" if g.why else "") for g in gates]
    return "\n".join(lines)
