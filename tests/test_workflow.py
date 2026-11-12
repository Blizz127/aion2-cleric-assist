import copy
import dataclasses
import json
import math
import os
import tempfile
import unittest
from pathlib import Path

from aion2_assist.config import ConfigError
from aion2_assist.frame import Reading
from aion2_assist.profile import compute_hash
from aion2_assist.workflow import Journal, Workflow, WorkflowError, WorkflowRunner, calibrate_workflow
from aion2_assist.workflow_demo import RECT, demo_recipe, marker_frame, run_demo
from tests.helpers import DMG_ONLY, PROFILE, Rig, cfg


def recipe(stage=0, change=None):
    profile, full = demo_recipe()
    data = copy.deepcopy(full.data)
    data["steps"] = [data["steps"][stage]]
    if change:
        change(data)
    data["hash"] = compute_hash(data)
    return Workflow(data, profile)


class WorkflowRig:
    def __init__(self, stage=0, journal_path=None):
        self.stage = stage
        self.recipe = recipe(stage)
        self.journal = Journal(self.recipe, journal_path)
        self.runner = WorkflowRunner(self.recipe, self.journal)
        self.rig = Rig(DMG_ONLY, watchdog_ms=100000)
        self.rig.ctl.workflow = self.runner
        self.rig.owner.press("toggle")

    def tick(self, t, after=False, marker=None, age=0, **kw):
        s = self.recipe.steps[0]
        state = self.recipe.checkpoints[s["after"] if after else s["before"]]["state"]
        state = kw.pop("state", state)
        self.rig.clock.t = t
        self.rig.src.frame = marker_frame(round(t-age, 6), marker if marker is not None else self.stage*2+(2 if after else 1),
                                         state=state, **kw)
        return self.rig.ctl.tick()


class Workflows(unittest.TestCase):
    def test_feedback_demo_all_seven_stages_and_emergency_heal(self):
        sink, records, status = run_demo()
        self.assertTrue(status["complete"])
        self.assertEqual(status["confirmed"], 7)
        self.assertEqual(status["purchase_total_reserved"], 5)
        self.assertEqual(sink.keys, ["q", "w", "1", "4", "f", "q", "b", "s"])
        self.assertTrue(any(r["reason"].startswith("emergency_heal:") for r in records))

    def test_sent_key_is_not_success_two_distinct_frames_required(self):
        r = WorkflowRig()
        r.tick(0)
        self.assertTrue(r.runner.pending)
        self.assertFalse(r.runner.complete)
        r.tick(0.3, after=True)
        r.tick(0.4, after=True, age=0.1)  # same frame, no second confirmation
        self.assertFalse(r.runner.complete)
        r.tick(0.6, after=True)
        self.assertTrue(r.runner.complete)
        r.tick(0.9)
        self.assertEqual(r.rig.keys, ["q"])

    def test_disjoint_confirmation_is_required(self):
        r = WorkflowRig()
        r.tick(0, after=True)
        self.assertEqual(r.runner.blocked, "postcondition_already_present")
        self.assertEqual(r.rig.keys, [])
        r = WorkflowRig()
        r.recipe.checkpoints[r.recipe.steps[0]["after"]] = r.recipe.checkpoints[r.recipe.steps[0]["before"]]
        r.tick(0)
        self.assertEqual(r.runner.blocked, "ambiguous_checkpoints")
        self.assertEqual(r.rig.keys, [])

    def test_unchanged_vendor_screen_times_out_without_retry(self):
        r = WorkflowRig(5)
        for t in (0, 0.3, 1, 4, 10, 11):
            r.tick(t)
        self.assertEqual(r.runner.blocked, "outcome_timeout")
        self.assertEqual(r.rig.keys, ["b"])
        self.assertEqual(r.runner.spent, 5)

    def test_unknown_dialog_death_and_combat_context_never_send_workflow_key(self):
        for kw in (dict(marker=31), dict(state="death"), dict(state="loading"), dict(target="enemy"), dict(hp=0.4)):
            r = WorkflowRig(5)
            r.tick(0, **kw)
            self.assertEqual(r.rig.keys, [], kw)
        r = WorkflowRig(1)  # normal-world navigation cannot opt into any dialog
        self.assertEqual(r.tick(0, state="dialog")["reason"], "unsafe_state:dialog")

    def test_safety_controls_still_apply_in_recognized_vendor_dialog(self):
        for event, reason in (("manual", "manual_pause"), ("emergency", "emergency_stop")):
            r = WorkflowRig(5)
            r.rig.owner.press(event)
            self.assertEqual(r.tick(0)["reason"], reason)
            self.assertEqual(r.rig.keys, [])
        r = WorkflowRig(5)
        r.rig.owner.hold = False
        self.assertEqual(r.tick(0)["reason"], "hold_released")
        r = WorkflowRig(5)
        self.assertEqual(r.tick(1, age=0.5)["reason"], "stale_frame")
        self.assertEqual(r.rig.keys, [])

    def test_trade_validation(self):
        changes = [lambda d: d.update(budget=4),
                   lambda d: d["checkpoints"][d["steps"][0]["before"]].update(threshold=0.99),
                   lambda d: d["steps"][0]["trade"].update(currency="premium"),
                   lambda d: d["steps"][0]["trade"].update(quantity=0),
                   lambda d: d["steps"][0]["trade"].update(total=math.nan),
                   lambda d: d["steps"][0].update(timeout_s=math.inf)]
        for change in changes:
            with self.assertRaises(WorkflowError):
                recipe(5, change)
        with self.assertRaises(WorkflowError):
            recipe(6, lambda d: d["steps"][0]["trade"].update(protected=True))

    def test_calibration_positive_negative_pngs_and_hash(self):
        from aion2_assist.png import write_png
        with tempfile.TemporaryDirectory() as directory:
            r = recipe()
            data = copy.deepcopy(r.data)
            for i, name in enumerate((data["steps"][0]["before"], data["steps"][0]["after"])):
                c = data["checkpoints"][name]
                write_png(str(Path(directory) / f"{i}.png"), marker_frame(0, i+1, state="dialog"))
                c.update(image=f"{i}.png", absent=f"{1-i}.png")
            data["checkpoints"] = {n: data["checkpoints"][n] for n in (data["steps"][0]["before"], data["steps"][0]["after"])}
            calibrated = calibrate_workflow(data, PROFILE, directory)
            self.assertEqual(calibrated.data["profile_hash"], PROFILE.hash)
            self.assertEqual(calibrated.hash, compute_hash(calibrated.data))
            data["checkpoints"][data["steps"][0]["before"]]["absent"] = "0.png"
            with self.assertRaises(WorkflowError):
                calibrate_workflow(data, PROFILE, directory)


class DurableProgress(unittest.TestCase):
    def test_restart_after_dispatch_refuses_repeat_and_keeps_reserved_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "journal.jsonl")
            r = WorkflowRig(5, path)
            r.tick(0)
            r.journal.close()
            second = WorkflowRig(5, path)
            try:
                second.tick(5)
                self.assertEqual(second.runner.blocked, "unresolved_dispatch_after_restart")
                self.assertEqual(second.runner.spent, 5)
                self.assertEqual(second.rig.keys, [])
            finally:
                second.journal.close()

    def test_resume_confirmed_progress_never_repeats_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "journal.jsonl")
            r = WorkflowRig(5, path)
            r.tick(0)
            r.tick(0.3, after=True)
            r.tick(0.6, after=True)
            r.journal.close()
            second = WorkflowRig(5, path)
            try:
                second.tick(5)
                self.assertTrue(second.runner.complete)
                self.assertEqual(second.rig.keys, [])
            finally:
                second.journal.close()

    def test_dispatch_is_durable_before_sink_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "journal.jsonl")
            r = WorkflowRig(5, path)
            r.rig.sink.hold_error = RuntimeError("input acknowledgement lost")
            self.assertEqual(r.tick(0)["reason"], "exception_fail_closed")
            records = [json.loads(line) for line in Path(path).read_text().splitlines()]
            self.assertEqual(records[-1]["event"], "dispatch")
            self.assertEqual(r.rig.sink.down, set())
            r.journal.close()

    def test_single_writer_mismatch_and_incomplete_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "journal.jsonl")
            j = Journal(recipe(5), path)
            with self.assertRaises(BlockingIOError):
                Journal(recipe(5), path)
            j.close()
            with self.assertRaises(WorkflowError):
                Journal(recipe(6), path)
            with open(path, "a") as f:
                f.write('{"event":')
            with self.assertRaises(WorkflowError):
                Journal(recipe(5), path)


class DispatchSafety(unittest.TestCase):
    def test_slow_capture_and_cancellation_during_capture(self):
        for reason in ("stale_frame", "disabled_before_dispatch", "emergency_stop", "focus_not_game"):
            r = Rig(DMG_ONLY)
            original = r.src.grab
            def grab():
                if reason == "stale_frame":
                    r.clock.t = 2
                elif reason == "disabled_before_dispatch":
                    r.ctl.enabled = False
                elif reason == "emergency_stop":
                    r.owner.press("emergency")
                else:
                    r.focus.title = "Firefox"
                return original()
            r.src.grab = grab
            self.assertEqual(r.enable()["reason"], reason)
            self.assertEqual(r.keys, [])

    def test_emergency_during_final_focus_rpc(self):
        r = Rig(DMG_ONLY)
        calls = []
        def focus():
            calls.append(True)
            if len(calls) == 2:
                r.owner.press("emergency")
            return "AION2"
        r.focus.active_window = focus
        self.assertEqual(r.enable()["reason"], "emergency_stop")
        self.assertEqual(r.keys, [])

    def test_nonfinite_timestamps_and_confidence_fail_closed(self):
        for value in (math.nan, math.inf):
            r = Rig(DMG_ONLY)
            r.src.frame = marker_frame(value, 1)
            self.assertEqual(r.enable(frame=False)["reason"], "invalid_timestamp")
            r = Rig(DMG_ONLY)
            read = r.ctl.perceiver.read
            r.ctl.perceiver.read = lambda f: dataclasses.replace(read(f), hp=Reading(1, value, f.ts))
            self.assertEqual(r.enable()["reason"], "low_confidence:hp")
            self.assertEqual(r.keys, [])

    def test_pending_cooldown_deadline_cannot_be_overwritten(self):
        r = Rig({"s4": dict(key="4", role="dmg", cooldown=0.1)}, desync_n=1, response_ms=600)
        r.enable()
        r.tick(0.3)
        rec = r.tick(0.6)
        self.assertEqual(rec["reason"], "desync_latched")
        self.assertEqual(r.keys, ["4"])

    def test_invalid_config_limits_rejected(self):
        for name, value in (("max_keys_per_sec", math.nan), ("max_stale_ms", math.inf), ("manual_pause_ms", -1),
                            ("hp_max", 0), ("mp_max", math.inf), ("desync_n", 1.5), ("hp_samples", 0)):
            with self.assertRaises(ConfigError, msg=name):
                cfg(**{name: value})

    def test_hash_valid_but_invalid_profile_is_refused(self):
        from aion2_assist.profile import Profile, ProfileMismatch
        for key, value in (("state", math.nan), ("anchor", math.inf), ("bar_tol", -1)):
            data = copy.deepcopy(PROFILE.data)
            data["thresholds"][key] = value
            data["hash"] = compute_hash(data)
            with self.assertRaises(ProfileMismatch):
                Profile(data)


class WorkflowIntegration(unittest.TestCase):
    def test_live_entry_refuses_unapproved_workflow_before_adapters(self):
        from argparse import Namespace
        from aion2_assist.orchestrator import Factories, live_main
        from tests.test_orchestrator import write_files
        with tempfile.TemporaryDirectory() as directory:
            config, profile, gates = write_files(directory)
            path = Path(directory) / "workflow.json"
            path.write_text(json.dumps(recipe().data))
            def forbidden(*a, **kw):
                self.fail("must not contact real/fake adapters before workflow approval")
            args = Namespace(config=config, profile=profile, gates_file=gates, i_accept_live_risk=True,
                             workflow=str(path), workflow_journal=str(Path(directory)/"progress.jsonl"),
                             tick_ms=50, self_check_s=0.1)
            errors = []
            self.assertEqual(live_main(args, Factories(forbidden, forbidden, forbidden), err=errors.append), 2)
            self.assertIn("workflow_explicit_approval", errors[0])
            self.assertFalse(Path(args.workflow_journal).exists())

    def test_orchestrator_workflow_completes_through_fake_portals_and_teardown(self):
        from tests.test_orchestrator import Rig as LiveRig
        r = LiveRig()
        w = recipe()
        runner = WorkflowRunner(w, Journal(w))
        r.o.workflow = runner
        factory = r.o.f.reader
        def reader(clock):
            obj = factory(clock)
            obj.read = lambda *args: marker_frame(clock(), 2 if r.notes() else 1, state="dialog")
            return obj
        r.o.f.reader = reader
        reason = r.o.run(max_ticks=20, after_tick=lambda i, rec: r.enable_hotkeys() if i == 0 else None)
        self.assertEqual(reason, "workflow_complete")
        self.assertTrue(runner.complete)
        self.assertEqual(r.notes(), [(16, 1), (16, 0)])  # q down/up, no combat keys in dialog
        self.assertFalse(r.o.ctl.enabled)
        self.assertEqual(r.o.teardown_errors, [])

    def test_owner_approval_backs_up_existing_config_and_pins_workflow(self):
        from tests.test_cli_modes import run
        from tests.test_orchestrator import write_files
        with tempfile.TemporaryDirectory() as directory:
            _, profile, gates = write_files(directory)
            before = Path(gates).read_bytes()
            w = recipe()
            path = Path(directory) / "workflow.json"
            path.write_text(json.dumps(w.data))
            rc, out, err = run("approve", "--profile", profile, "--workflow", str(path),
                               "--gates-file", gates, "--i-accept-live-risk")
            self.assertEqual(rc, 0, err)
            self.assertIn(w.hash, Path(gates).read_text())
            backups = list(Path(directory).glob("LIVE-APPROVED.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), before)

    def test_cli_demo_and_incomplete_screenshot_replay_exit_codes(self):
        from tests.test_cli_modes import run
        from tests.test_orchestrator import write_files
        from aion2_assist.png import write_png
        rc, out, err = run("workflow", "demo")
        self.assertEqual(rc, 0, err)
        summary = json.loads(out.splitlines()[-1])
        self.assertEqual(summary["evidence"], "synthetic")
        self.assertFalse(summary["live_verified"])
        with tempfile.TemporaryDirectory() as directory:
            config, profile, _ = write_files(directory, config={"window_match": "AION2"})
            path = Path(directory) / "workflow.json"
            path.write_text(json.dumps(recipe().data))
            frames = Path(directory) / "frames"
            frames.mkdir()
            write_png(str(frames / "0.png"), marker_frame(0, 1, state="dialog"))
            args = ["workflow", "replay", "--recipe", str(path), "--profile", profile,
                    "--config", config, "--frames", str(frames), "--dt", "0.3"]
            rc, out, err = run(*args)
            self.assertEqual(rc, 4, err)
            self.assertFalse(json.loads(out.splitlines()[-1])["complete"])
            for i in (1, 2):
                write_png(str(frames / f"{i}.png"), marker_frame(0, 2, state="dialog"))
            self.assertEqual(run(*args)[0], 0)
