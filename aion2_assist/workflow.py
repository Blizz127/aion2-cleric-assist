"""Visual, bounded quest/navigation/vendor workflows. No device or bus access here.

Each key is a one-shot attempt, followed by a separately observed screen change.
An unresolved attempt is never replayed on process restart. Recipes contain only
calibrated pixels and declared keys; they do not infer game binds or item values.
"""
import copy
import fcntl
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .frame import template_score
from .keys import normalize_key
from .profile import compute_hash


class WorkflowError(ValueError):
    pass


KINDS = {"navigate", "quest", "loot", "buy", "sell", "combat", "wait"}
PASSIVE = {"combat", "wait"}


def _number(value, name, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise WorkflowError(f"{name} must be finite in [{low}, {high}]")


def _integer(value, name, low=0):
    if type(value) is not int or value < low:
        raise WorkflowError(f"{name} must be an integer >= {low}")


class Workflow:
    def __init__(self, data, profile):
        if not isinstance(data, dict) or data.get("hash") != compute_hash(data):
            raise WorkflowError("workflow hash does not match contents")
        self.data = copy.deepcopy(data)
        self.hash = data["hash"]
        if data.get("version") != 1 or data.get("profile_hash") != profile.hash:
            raise WorkflowError("workflow version or pinned combat profile differs")
        self.checkpoints = self.data.get("checkpoints")
        self.steps = self.data.get("steps")
        self.budget = data.get("budget", 0)
        _integer(self.budget, "budget")
        if not isinstance(self.checkpoints, dict) or not self.checkpoints:
            raise WorkflowError("checkpoints must be a nonempty object")
        w, h = profile.frame_size
        for name, c in self.checkpoints.items():
            if not isinstance(c, dict):
                raise WorkflowError(f"checkpoint {name}: must be an object")
            r = c.get("rect")
            if (not isinstance(r, list) or len(r) != 4 or any(type(v) is not int for v in r)
                    or min(r[:2]) < 0 or min(r[2:]) <= 0 or r[0] + r[2] > w or r[1] + r[3] > h):
                raise WorkflowError(f"checkpoint {name}: invalid full-frame rectangle")
            t = c.get("template")
            if (not isinstance(t, list) or len(t) != r[2] * r[3] * 3
                    or any(type(v) is not int or not 0 <= v <= 255 for v in t)):
                raise WorkflowError(f"checkpoint {name}: invalid RGB template")
            _number(c.get("threshold"), f"checkpoint {name}.threshold", 0.9, 1)
            _number(c.get("absent_score"), f"checkpoint {name}.absent_score", 0, c["threshold"] - 0.05)
            if "state" not in c or c["state"] not in (None, "dialog"):
                raise WorkflowError(f"checkpoint {name}: state must be null or dialog")
            if c["state"] == "dialog" and "dialog" not in profile["templates"]["states"]:
                raise WorkflowError("dialog checkpoints require a calibrated dialog detector")
        if not isinstance(self.steps, list) or not self.steps:
            raise WorkflowError("steps must be a nonempty array")
        ids, total = set(), 0
        for s in self.steps:
            if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not s["id"] or s["id"] in ids:
                raise WorkflowError("step ids must be nonempty and unique")
            ids.add(s["id"])
            if s.get("kind") not in KINDS:
                raise WorkflowError(f"step {s['id']}: unsupported kind")
            for field in ("before", "after"):
                if s.get(field) not in self.checkpoints:
                    raise WorkflowError(f"step {s['id']}: unknown {field} checkpoint")
            if s["before"] == s["after"]:
                raise WorkflowError("before and after must be different checkpoints")
            _number(s.get("timeout_s"), "timeout_s", 0.1, 3600)
            if s["kind"] in PASSIVE:
                if "key" in s:
                    raise WorkflowError("combat/wait stages cannot have workflow keys")
                if s["kind"] == "combat" and self.checkpoints[s["before"]]["state"] is not None:
                    raise WorkflowError("combat cannot start inside a dialog")
            elif normalize_key(s.get("key", "")) is None:
                raise WorkflowError(f"step {s['id']}: missing/invalid key")
            else:
                s["key"] = normalize_key(s["key"])
            if s["kind"] in {"buy", "sell"}:
                trade = s.get("trade", {})
                if not isinstance(trade, dict) or trade.get("currency") != "in_game":
                    raise WorkflowError("trade currency must be in_game (no premium/real-money actions)")
                if not isinstance(trade.get("item"), str) or not trade["item"].strip():
                    raise WorkflowError("trade requires an explicit item allowlist entry")
                _integer(trade.get("quantity"), "trade.quantity", 1)
                _integer(trade.get("total"), "trade.total")
                if self.checkpoints[s["before"]]["state"] != "dialog":
                    raise WorkflowError("trade requires a calibrated vendor dialog checkpoint")
                if any(self.checkpoints[s[side]]["threshold"] != 1 for side in ("before", "after")):
                    raise WorkflowError("trade checkpoints must match exactly (threshold 1); approximate price/item matches are refused")
                if s["kind"] == "sell" and trade.get("protected") is not False:
                    raise WorkflowError("selling requires an explicitly unprotected item")
                if s["kind"] == "buy":
                    total += trade["total"]
        if total > self.budget:
            raise WorkflowError(f"planned purchase total {total} exceeds budget {self.budget}")

    def matches(self, name, frame, observation):
        c = self.checkpoints[name]
        return (observation.state.value == c["state"]
                and template_score(frame.flat(tuple(c["rect"])), c["template"]) >= c["threshold"])


def load_workflow(path, profile):
    with open(path) as f:
        return Workflow(json.load(f), profile)


def calibrate_workflow(spec, profile, base):
    """Compile positive AND negative full-frame PNG evidence into a pinned recipe."""
    from .png import load_frame
    from .perception import Perceiver
    data = copy.deepcopy(spec)
    checkpoints = data.get("checkpoints")
    if not isinstance(checkpoints, dict) or not checkpoints:
        raise WorkflowError("checkpoints must be a nonempty object")
    for name, c in checkpoints.items():
        if not isinstance(c, dict) or not {"image", "absent", "rect", "state"} <= c.keys():
            raise WorkflowError(f"checkpoint {name}: needs image, absent, rect, state")
        positive = load_frame(str(Path(base) / c.pop("image")))
        negative = load_frame(str(Path(base) / c.pop("absent")))
        profile.check_frame(positive)
        profile.check_frame(negative)
        if Perceiver(profile).read(positive).state.value != c["state"]:
            raise WorkflowError(f"checkpoint {name}: declared state differs from screenshot")
        c["template"] = list(positive.flat(tuple(c["rect"])))
        c["absent_score"] = template_score(negative.flat(tuple(c["rect"])), c["template"])
        c.setdefault("threshold", 0.97)
    data.update(version=1, profile_hash=profile.hash)
    data["hash"] = compute_hash(data)
    return Workflow(data, profile)


class Journal:
    """Append+fsync before dispatch, exclusive nonblocking process lock for live runs.

    path=None is an in-memory journal for mock replays only. An incomplete or
    corrupt journal is refused, never truncated, repaired, or silently reset.
    """
    def __init__(self, workflow, path=None):
        self.file = None
        self.records = []
        if path is not None:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
                    raise WorkflowError("journal must be owned by you and not writable by others")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.file = os.fdopen(fd, "a+", encoding="utf-8")
            except BaseException:
                os.close(fd)
                raise
            try:
                # Ensure creation of the journal survives a crash before sending a key.
                parent = os.open(str(Path(path).resolve().parent), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
                self.file.seek(0)
                for line in self.file:
                    if not line.endswith("\n"):
                        raise WorkflowError("journal has an incomplete record; action outcome is unknown")
                    self.records.append(json.loads(line))
            except BaseException:
                self.close()
                raise
        identity = {"event": "identity", "workflow": workflow.hash}
        if not self.records:
            self.append(identity)
        if self.records[0] != identity:
            self.close()
            raise WorkflowError("journal belongs to a different workflow")

    def append(self, record):
        if self.file:
            self.file.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
            self.file.flush()
            os.fsync(self.file.fileno())
        self.records.append(record)

    def close(self):
        if self.file:
            self.file.close()
            self.file = None


@dataclass(frozen=True)
class Intent:
    reason: str
    key: str = None
    combat: bool = False


class WorkflowRunner:
    def __init__(self, workflow, journal):
        self.workflow, self.journal = workflow, journal
        self.index = self.spent = 0
        self.pending = False
        self.blocked = None
        self.started = self.sent_at = self.last_frame = None
        self.confirmations = 0
        for r in journal.records[1:]:
            s = self.step
            if s is None or r.get("step") != s["id"] or self.blocked:
                raise WorkflowError("journal transition is invalid")
            if r.get("event") == "dispatch" and not self.pending:
                self.pending = True
                self.spent += self._cost()
            elif r.get("event") == "confirm" and self.pending:
                self.index += 1
                self.pending = False
            elif r.get("event") == "block":
                self.blocked = r.get("reason", "journal_blocked")
            else:
                raise WorkflowError("journal transition is invalid")
        if self.pending and not self.blocked:
            self.blocked = "unresolved_dispatch_after_restart"

    @property
    def step(self):
        return self.workflow.steps[self.index] if self.index < len(self.workflow.steps) else None

    @property
    def complete(self):
        return self.step is None

    def _cost(self):
        return self.step["trade"]["total"] if self.step["kind"] == "buy" else 0

    def status(self):
        return {"workflow_hash": self.workflow.hash, "confirmed": self.index, "total": len(self.workflow.steps),
                "step": self.step["id"] if self.step else None, "pending": self.pending,
                "blocked": self.blocked, "complete": self.complete, "purchase_total_reserved": self.spent,
                "budget": self.workflow.budget}

    def _block(self, reason):
        self.journal.append({"event": "block", "step": self.step["id"], "reason": reason})
        self.blocked = reason
        return Intent("workflow_blocked:" + reason)

    def allows_state(self, state):
        if self.blocked or not self.step or state not in (None, "dialog"):
            return False
        names = (self.step["before"], self.step["after"])
        return any(self.workflow.checkpoints[n]["state"] == state for n in names)

    def decide(self, frame, obs, now):
        if self.blocked:
            return Intent("workflow_blocked:" + self.blocked)
        if self.complete:
            return Intent("workflow_complete")
        if self.started is None:
            self.started = now
        if now - self.started >= self.step["timeout_s"]:
            return self._block("outcome_timeout" if self.pending else "checkpoint_timeout")
        if self.last_frame is not None and frame.ts <= self.last_frame:
            return Intent("workflow_wait_fresh_frame")
        self.last_frame = frame.ts
        before = self.workflow.matches(self.step["before"], frame, obs)
        after = self.workflow.matches(self.step["after"], frame, obs)
        if before and after:
            return self._block("ambiguous_checkpoints")
        if not self.pending:
            # Existing postconditions are never proof of this run's action.
            if after:
                return self._block("postcondition_already_present")
            if not before:
                return Intent("workflow_wait_checkpoint")
            if self.step["kind"] in PASSIVE:
                self.dispatch(now)
                return Intent("workflow_stage_started", combat=self.step["kind"] == "combat")
            return Intent("workflow_action:" + self.step["id"], key=self.step["key"])
        if after and frame.ts > self.sent_at:
            self.confirmations += 1
            if self.confirmations >= 2:
                sid = self.step["id"]
                self.journal.append({"event": "confirm", "step": sid, "t": now})
                self.index += 1
                self.pending = False
                self.started = self.sent_at = None
                self.confirmations = 0
                return Intent("workflow_confirmed:" + sid)
            return Intent("workflow_confirming")
        self.confirmations = 0
        # Combat remains bounded to the same calibrated encounter context.
        return Intent("workflow_wait_outcome", combat=self.step["kind"] == "combat" and before)

    def dispatch(self, now):
        if self.pending or self.blocked or self.complete:
            raise WorkflowError("workflow is not ready to dispatch")
        cost = self._cost()
        if self.spent + cost > self.workflow.budget:
            raise WorkflowError("purchase budget exhausted")
        self.journal.append({"event": "dispatch", "step": self.step["id"], "t": now})
        self.pending, self.sent_at = True, now
        self.spent += cost
