"""Runtime contract tests. The container engine is simulated, never started."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from agentrooms_runtime import Runtime, RuntimeError, build_plan, validate_manifest
from agentrooms_runtime.__main__ import main
from agentrooms_runtime.core import CommandResult, LABEL_PREFIX, MAX_CAPTURE_BYTES, execute


def manifest():
    return {
        "schemaVersion": 1, "id": "test-task", "taskId": "task-123", "ownerAgentId": "agent-456",
        "image": "example/workload@sha256:" + "a" * 64, "argv": ["/bin/sh", "-c", "printf hello > result.txt"],
        "limits": {"cpus": 1, "memoryMiB": 256, "pids": 64, "wallSeconds": 30},
        "network": "none", "outputs": ["result.txt"],
    }


class FakeEngine:
    """Minimal Docker-shaped inspect responses; no isolation claims implied."""

    def __init__(self):
        self.calls = []
        self.container = None
        self.complete = True
        self.inspect_error = None
        self.stop_error = False
        self.run_error = None
        self.interrupt_run = False
        self.crash_run = False
        # Config and manifest digests are deliberately different identities.
        self.image = {"Id": "sha256:" + "f" * 64, "RepoDigests": [manifest()["image"]], "Config": {}}
        self.image_error = None
        self.image_callback = None
        self.inspection_responses = []

    def __call__(self, command, *, env, timeout):
        self.calls.append((list(command), dict(env), timeout))
        args = command[command.index("--namespace") + 2:]
        if args[0] == "image":
            if self.image_callback:
                self.image_callback()
            return self.image_error or CommandResult(0, json.dumps([self.image]))
        if args[0] == "run":
            if self.run_error:
                return self.run_error
            labels = {}
            for index, part in enumerate(args):
                if part == "--label":
                    key, value = args[index + 1].split("=", 1)
                    labels[key] = value
            self.container = {"Id": "b" * 64, "Name": "/" + args[args.index("--name") + 1],
                              "Config": {"Labels": labels},
                              "State": {"Running": not self.complete, "Status": "exited" if self.complete else "running", "ExitCode": 0}}
            if self.interrupt_run:
                raise KeyboardInterrupt
            if self.crash_run:
                raise SystemExit("simulated controller crash")
            return CommandResult(0, "b" * 64 + "\n")
        if args[0] == "inspect":
            if self.inspection_responses:
                response = self.inspection_responses.pop(0)
                if response is not None:
                    return response
            if self.inspect_error:
                return self.inspect_error
            if self.container is None:
                return CommandResult(1, stderr="no such container")
            return CommandResult(0, json.dumps([self.container]))
        if args[0] == "stop":
            if self.stop_error:
                return CommandResult(1, stderr="engine connection lost", error="engineUnavailable")
            self.container["State"].update(Running=False, Status="exited", ExitCode=137)
            return CommandResult(0, "b" * 64)
        raise AssertionError(f"unexpected fake engine action: {args}")

    def count(self, action):
        return sum(command[command.index("--namespace") + 2] == action for command, _, _ in self.calls)


class ManifestTests(unittest.TestCase):
    def test_valid_and_detached_from_input(self):
        data = manifest()
        checked = validate_manifest(data)
        checked["limits"]["cpus"] = 2
        self.assertEqual(data["limits"]["cpus"], 1)

    def test_unknown_fields_and_unsafe_options_rejected(self):
        for key in ("secrets", "mounts", "privileged", "env", "entrypoint", "dockerSocket"):
            with self.subTest(key=key):
                data = manifest()
                data[key] = True
                with self.assertRaises(RuntimeError):
                    validate_manifest(data)
        data = manifest()
        data["limits"]["swap"] = 1
        with self.assertRaises(RuntimeError):
            validate_manifest(data)

    def test_pins_and_bounds(self):
        bad = [ ("image", "alpine:latest"), ("image", "sha256:" + "a" * 64),
                ("image", "alpine:latest@sha256:" + "a" * 64),
                ("image", "example.com//alpine@sha256:" + "a" * 64),
                ("image", "example.com:port/alpine@sha256:" + "a" * 64),
                ("image", "Upper/alpine@sha256:" + "a" * 64),
                ("id", "../escape"), ("network", "host"),
                ("schemaVersion", True), ("argv", []), ("argv", ["echo", "\0"]),
                ("outputs", ["../escape"]), ("outputs", ["/tmp/file"]),
                ("outputs", ["a//b"]), ("outputs", ["a/./b"]),
                ("outputs", ["same", "same"]), ("outputs", ["a\\b"]) ]
        for key, value in bad:
            with self.subTest(key=key, value=value):
                data = manifest()
                data[key] = value
                with self.assertRaises(RuntimeError):
                    validate_manifest(data)
        for key, value in (("cpus", True), ("cpus", float("nan")), ("cpus", 0), ("cpus", 9), ("cpus", 10 ** 1000),
                           ("pids", 0), ("memoryMiB", 99999), ("wallSeconds", -1)):
            with self.subTest(limit=key, value=value):
                data = manifest()
                data["limits"][key] = value
                with self.assertRaises(RuntimeError):
                    validate_manifest(data)

    def test_oversized_cpu_integer_is_structured_cli_error(self):
        with tempfile.TemporaryDirectory() as parent:
            task = manifest()
            task["limits"]["cpus"] = 10 ** 1000
            path = Path(parent) / "task.json"
            path.write_text(json.dumps(task))
            result = subprocess.run([sys.executable, "-m", "agentrooms_runtime", "validate", str(path)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"], "invalidManifest")
            self.assertEqual(result.stdout, "")

    def test_plan_is_pure_and_overrides_image_entrypoint(self):
        with tempfile.TemporaryDirectory() as parent:
            state = Path(parent) / "not-created"
            prefix = ["colima", "-p", "ar", "ssh", "--", "sudo", "nerdctl"]
            data = manifest()
            data["argv"] = ["/bin/echo", "--", "$(touch /tmp/must-not-run)", "with spaces", ""]
            plan = build_plan(data, state, prefix)
            command = plan["argv"]
            self.assertFalse(state.exists())
            self.assertEqual(command[:len(prefix)], prefix)
            self.assertEqual(command[command.index("--entrypoint") + 1], "/bin/echo")
            self.assertEqual(command[command.index(data["image"]) + 1:], ["--", *data["argv"][1:]])
            for flag, value in (("--namespace", "agentrooms"), ("--pull", "never"), ("--network", "none"),
                                ("--user", "65534:65534"), ("--cap-drop", "ALL"),
                                ("--security-opt", "no-new-privileges"), ("--memory-swap", "256m")):
                self.assertEqual(command[command.index(flag) + 1], value)
            self.assertIn("--read-only", command)
            self.assertIn("--no-healthcheck", command)
            self.assertEqual(command[command.index("--mount") + 1], f"type=bind,src={state}/workspaces/test-task,dst=/workspace,rw")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "runtime"
        self.engine = FakeEngine()
        self.runtime = Runtime(self.state, ["nerdctl"], executor=self.engine, poll_seconds=0.01)

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def _running_after_loss(self):
        self.engine.complete = False
        self.engine.crash_run = True
        with self.assertRaises(SystemExit):
            self.runtime.run(manifest())
        self.engine.crash_run = False
        self.engine.inspect_error = CommandResult(1, stderr="temporarily unavailable", error="engineUnavailable")
        self.assertEqual(self.runtime.inspect("test-task")["state"], "unknown")
        self.engine.inspect_error = None

    def test_completed_task_and_artifact_hashes(self):
        result = self.runtime.run(manifest())
        self.assertEqual(result["state"], "exited")
        self.assertEqual(self.engine.count("run"), 1)
        workspace = Path(result["workspace"])
        (workspace / "result.txt").write_bytes(b"hello\n")
        artifact = self.runtime.outputs("test-task")["outputs"][0]
        self.assertEqual(artifact, {"path": "result.txt", "bytes": 6, "sha256": hashlib.sha256(b"hello\n").hexdigest()})
        self.assertEqual(self.runtime.list()[0]["id"], "test-task")

    def test_idempotency_refuses_same_and_changed_manifest(self):
        self.runtime.run(manifest())
        with self.assertRaises(RuntimeError) as same:
            self.runtime.run(manifest())
        self.assertEqual(same.exception.code, "runAlreadyRecorded")
        changed = manifest()
        changed["argv"] = ["/bin/false"]
        with self.assertRaises(RuntimeError) as conflict:
            self.runtime.run(changed)
        self.assertEqual(conflict.exception.code, "manifestIdentityConflict")
        self.assertEqual(self.engine.count("run"), 1)

    def test_unavailable_or_unsupported_engine_never_retries(self):
        self.engine.run_error = CommandResult(None, stderr="missing executable", error="engineUnavailable")
        result = self.runtime.run(manifest())
        self.assertEqual((result["state"], result["errorCode"]), ("failed", "engineUnavailable"))
        changed = manifest()
        changed["id"] = "unsupported"
        self.engine.run_error = CommandResult(1, stderr="unknown flag --pids-limit", error="engineUnsupported")
        result = self.runtime.run(changed)
        self.assertEqual((result["state"], result["errorCode"]), ("unknown", "engineUnsupported"))
        self.assertEqual(self.engine.count("run"), 2)

    def test_image_volume_metadata_rejected_before_container_creation(self):
        self.engine.image["Config"]["Volumes"] = {"/extra-writable": {}}
        result = self.runtime.run(manifest())
        self.assertEqual((result["state"], result["errorCode"]), ("failed", "imageVolumesUnsupported"))
        self.assertEqual(self.engine.count("run"), 0)
        self.assertFalse((self.state / "workspaces").exists())

    def test_image_identity_and_malformed_inspection_rejected(self):
        for index, image in enumerate((
                {"Id": "sha256:" + "a" * 64, "RepoDigests": ["other@sha256:" + "c" * 64], "Config": {}},
                {"Id": "sha256:" + "a" * 64, "RepoDigests": [manifest()["image"]]},
                {"Id": "not-an-id", "RepoDigests": [manifest()["image"]], "Config": {}},
                {"Id": "sha256:" + "a" * 64, "RepoDigests": [manifest()["image"]], "Config": {"Volumes": []}})):
            self.engine.image = image
            data = manifest()
            data["id"] = "invalid-image-" + str(index)
            result = self.runtime.run(data)
            self.assertEqual(result["state"], "failed")
        self.assertEqual(self.engine.count("run"), 0)

    def test_bare_digest_rejected_without_engine_query(self):
        data = manifest()
        data["image"] = "sha256:" + "a" * 64
        with self.assertRaises(RuntimeError) as caught:
            self.runtime.run(data)
        self.assertEqual(caught.exception.code, "invalidManifest")
        self.assertEqual(self.engine.calls, [])

    def test_qualified_and_familiar_repository_aliases_are_admitted(self):
        pairs = [
            ("docker.io/library/alpine", "alpine"),
            ("alpine", "docker.io/library/alpine"),
            ("docker.io/alpine", "library/alpine"),
            ("index.docker.io/library/alpine", "alpine"),
            ("index.docker.io/alpine", "docker.io/library/alpine"),
            ("docker.io/team/alpine", "team/alpine"),
            ("localhost:5000/team/alpine", "localhost:5000/team/alpine"),
        ]
        for index, (requested, observed) in enumerate(pairs):
            with self.subTest(requested=requested, observed=observed):
                data = manifest()
                data["id"] = "alias-" + str(index)
                data["image"] = requested + "@sha256:" + "a" * 64
                self.engine.image["RepoDigests"] = [observed + "@sha256:" + "a" * 64]
                self.assertNotEqual(self.engine.image["Id"], "sha256:" + "a" * 64)
                self.assertEqual(self.runtime.run(data)["state"], "exited")
        self.assertEqual(self.engine.count("run"), len(pairs))

    def test_unrelated_repositories_ports_and_digests_stay_distinct(self):
        pairs = [
            ("docker.io/library/alpine", "other.example/library/alpine", "a"),
            ("docker.io/library/alpine", "registry-1.docker.io/library/alpine", "a"),
            ("docker.io/library/alpine", "docker.io/team/alpine", "a"),
            ("docker.io/library/alpine", "docker.io/library/busybox", "a"),
            ("docker.io/library/alpine", "alpine", "b"),
            ("registry.example:5000/team/alpine", "registry.example:5001/team/alpine", "a"),
            ("registry.example:443/team/alpine", "registry.example/team/alpine", "a"),
            ("localhost/alpine", "docker.io/localhost/alpine", "a"),
            ("docker.io/library/alpine", "docker.io:443/library/alpine", "a"),
            ("docker.io/library/alpine", "index.docker.io:443/library/alpine", "a"),
        ]
        for index, (requested, observed, digest_char) in enumerate(pairs):
            with self.subTest(requested=requested, observed=observed, digest=digest_char):
                data = manifest()
                data["id"] = "mismatch-" + str(index)
                data["image"] = requested + "@sha256:" + "a" * 64
                self.engine.image["RepoDigests"] = [observed + "@sha256:" + digest_char * 64]
                self.assertEqual(self.runtime.run(data)["errorCode"], "imageIdentityMismatch")
        self.assertEqual(self.engine.count("run"), 0)

    def test_live_supervisor_survives_transient_inspection_until_deadline(self):
        self.engine.complete = False
        self.engine.inspection_responses = [None, CommandResult(1, stderr="transient", error="engineUnavailable"), None]
        data = manifest()
        data["limits"]["wallSeconds"] = 1
        result = self.runtime.run(data)
        self.assertEqual(result["state"], "timed_out")
        self.assertEqual(self.engine.count("run"), 1)
        self.assertEqual(self.engine.count("stop"), 1)
        self.assertGreaterEqual(self.engine.count("inspect"), 4)

    def test_image_admission_that_consumes_deadline_never_launches(self):
        clock = [100.0]
        self.engine.image_callback = lambda: clock.__setitem__(0, 102.0)
        data = manifest()
        data["limits"]["wallSeconds"] = 1
        with patch("agentrooms_runtime.core.time.time", side_effect=lambda: clock[0]):
            result = self.runtime.run(data)
        self.assertEqual((result["state"], result["errorCode"]), ("timed_out", "deadlineBeforeLaunch"))
        self.assertEqual(self.engine.count("run"), 0)
        self.assertFalse((self.state / "workspaces").exists())

    def test_live_deadline_with_unconfirmed_stop_is_json_safe_unknown(self):
        self.engine.complete = False
        self.engine.stop_error = True
        data = manifest()
        data["limits"]["wallSeconds"] = 1
        result = self.runtime.run(data)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(json.loads(json.dumps(result))["errorCode"], "engineUnavailable")
        self.assertEqual(self.engine.count("run"), 1)
        self.assertEqual(self.engine.count("stop"), 1)

    def test_reopening_preserves_no_replay_and_recorded_engine(self):
        self.runtime.run(manifest())
        self.runtime.close()
        self.runtime = Runtime(self.state, executor=self.engine)
        self.assertEqual(self.runtime.status("test-task")["state"], "exited")
        with self.assertRaises(RuntimeError) as caught:
            self.runtime.run(manifest())
        self.assertEqual(caught.exception.code, "runAlreadyRecorded")
        self.assertEqual(self.engine.count("run"), 1)

    def test_stop_checks_labels_before_engine_mutation(self):
        self._running_after_loss()
        self.engine.container["Config"]["Labels"][LABEL_PREFIX + "owner-agent-id"] = "another-owner"
        result = self.runtime.stop("test-task")
        self.assertEqual((result["state"], result["errorCode"]), ("unknown", "custodyMismatch"))
        self.assertEqual(self.engine.count("stop"), 0)

    def test_stop_checks_recorded_full_id(self):
        self._running_after_loss()
        self.assertEqual(self.runtime.inspect("test-task")["state"], "running")
        self.engine.container["Id"] = "c" * 64
        self.assertEqual(self.runtime.stop("test-task")["errorCode"], "custodyMismatch")
        self.assertEqual(self.engine.count("stop"), 0)

    def test_changed_engine_prefix_refused_before_query(self):
        self._running_after_loss()
        calls = len(self.engine.calls)
        self.runtime.requested_prefix = ["another-engine"]
        with self.assertRaises(RuntimeError) as caught:
            self.runtime.stop("test-task")
        self.assertEqual(caught.exception.code, "engineIdentityMismatch")
        self.assertEqual(len(self.engine.calls), calls)

    def test_recorded_routing_environment_is_reused(self):
        with patch.dict(os.environ, {"COLIMA_HOME": str(Path(self.temp.name) / "dedicated-engine")}):
            self.runtime.run(manifest())
        with patch.dict(os.environ, {"COLIMA_HOME": "/unrelated-engine"}):
            self.runtime.inspect("test-task")
        self.assertEqual(self.engine.calls[-1][1]["COLIMA_HOME"], str(Path(self.temp.name) / "dedicated-engine"))

    def test_crash_leaves_intent_and_recovery_stops_overdue_owned_run(self):
        self.engine.complete = False
        self.engine.crash_run = True
        with self.assertRaises(SystemExit):
            self.runtime.run(manifest())
        self.assertEqual(self.runtime.status("test-task")["state"], "starting")
        self.runtime._update("test-task", deadline=time.time() - 1)
        result = self.runtime.recover("test-task")
        self.assertEqual(result["state"], "timed_out")
        self.assertEqual(self.engine.count("run"), 1)
        self.assertEqual(self.engine.count("stop"), 1)
        self.assertEqual(self.engine.calls[-2][0][-1], "b" * 64)

    def test_cancel_only_claimed_after_confirmed_stop(self):
        self._running_after_loss()
        self.engine.stop_error = True
        result = self.runtime.stop("test-task")
        self.assertEqual(result["state"], "unknown")
        self.assertTrue(self.engine.container["State"]["Running"])
        self.engine.stop_error = False
        self.assertEqual(self.runtime.recover("test-task")["state"], "cancelled")

    def test_keyboard_interrupt_attempts_checked_stop(self):
        self.engine.complete = False
        self.engine.interrupt_run = True
        with self.assertRaises(KeyboardInterrupt):
            self.runtime.run(manifest())
        self.assertEqual(self.runtime.status("test-task")["state"], "cancelled")
        self.assertEqual(self.engine.count("stop"), 1)

    def test_missing_container_never_claimed_stopped(self):
        self._running_after_loss()
        self.engine.container = None
        result = self.runtime.stop("test-task")
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(self.engine.count("stop"), 0)

    def test_malformed_and_truncated_inspection_fail_closed(self):
        self.runtime.run(manifest())
        for response in (CommandResult(0, "{}"), CommandResult(0, "[]"), CommandResult(0, "[]", truncated=True)):
            self.engine.inspect_error = response
            self.assertEqual(self.runtime.inspect("test-task")["state"], "unknown")

    def test_preexisting_workspace_rejected_without_launch(self):
        (self.state / "workspaces" / "test-task").mkdir(parents=True)
        with self.assertRaises(RuntimeError):
            self.runtime.run(manifest())
        self.assertEqual(self.engine.count("run"), 0)

    def test_symlink_workspace_root_rejected(self):
        target = Path(self.temp.name) / "elsewhere"
        target.mkdir()
        (self.state / "workspaces").symlink_to(target, target_is_directory=True)
        with self.assertRaises(RuntimeError) as caught:
            self.runtime.run(manifest())
        self.assertEqual(caught.exception.code, "unsafeWorkspace")
        self.assertEqual(self.engine.count("run"), 0)

    def test_outputs_reject_symlink_and_hardlink_escape(self):
        result = self.runtime.run(manifest())
        workspace = Path(result["workspace"])
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("private")
        output = workspace / "result.txt"
        output.symlink_to(outside)
        with self.assertRaises(RuntimeError) as caught:
            self.runtime.outputs("test-task")
        self.assertEqual(caught.exception.code, "unsafeOutput")
        output.unlink()
        os.link(outside, output)
        with self.assertRaises(RuntimeError):
            self.runtime.outputs("test-task")

    def test_outputs_reject_symlink_parent_and_fifo(self):
        data = manifest()
        data["outputs"] = ["nested/result.txt"]
        result = self.runtime.run(data)
        workspace = Path(result["workspace"])
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "result.txt").write_text("private")
        (workspace / "nested").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            self.runtime.outputs("test-task")
        (workspace / "nested").unlink()
        (workspace / "nested").mkdir()
        os.mkfifo(workspace / "nested" / "result.txt")
        with self.assertRaises(RuntimeError):
            self.runtime.outputs("test-task")

    def test_outputs_refuse_running_container(self):
        self._running_after_loss()
        with self.assertRaises(RuntimeError) as caught:
            self.runtime.outputs("test-task")
        self.assertEqual(caught.exception.code, "outputsNotReady")


class ProcessAndCliTests(unittest.TestCase):
    def test_subprocess_capture_is_bounded_and_drains_both_streams(self):
        result = execute([sys.executable, "-c", "import os; os.write(1,b'x'*1048576); os.write(2,b'y'*1048576)"], env=dict(os.environ), timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), MAX_CAPTURE_BYTES)
        self.assertEqual(len(result.stderr), MAX_CAPTURE_BYTES)
        self.assertTrue(result.truncated)

    def test_subprocess_timeout_and_missing_engine_are_explicit(self):
        started = time.monotonic()
        result = execute([sys.executable, "-c", "import time; time.sleep(30)"], env=dict(os.environ), timeout=0.1)
        self.assertEqual(result.error, "engineTimeout")
        self.assertLess(time.monotonic() - started, 5)
        result = execute(["/nonexistent/agentrooms-test-engine"], env=dict(os.environ), timeout=1)
        self.assertEqual(result.error, "engineUnavailable")

    def test_cli_validate_plan_duplicate_json_and_required_state(self):
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent) / "manifest.json"
            path.write_text(json.dumps(manifest()))
            state = Path(parent) / "state"
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(["validate", str(path)]), 0)
            self.assertTrue(json.loads(stdout.getvalue())["valid"])
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(["--state-dir", str(state), "plan", str(path)]), 0)
            self.assertFalse(state.exists())
            self.assertIn("argv", json.loads(stdout.getvalue()))
            with redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(main(["plan", str(path)]), 2)
            self.assertEqual(json.loads(stderr.getvalue())["error"], "invalidStateDirectory")
            path.write_text('{"id":"one","id":"two"}')
            with redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(main(["validate", str(path)]), 2)
            self.assertIn("duplicate JSON key", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
