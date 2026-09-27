# SPDX-License-Identifier: Apache-2.0
# Image reference normalization adapts distribution/reference v0.6.0.
# See THIRD_PARTY_NOTICES.md for attribution, source, and changes.
"""A small, fail-closed task manifest and durable nerdctl execution adapter.

The engine is a separately provisioned trust boundary. This package does not
install an engine, grant access to host credentials, or establish VM isolation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import sqlite3
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any


NAMESPACE = "agentrooms"
MAX_CAPTURE_BYTES = 128 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024 * 1024
TERMINAL = frozenset({"exited", "failed", "timed_out", "cancelled"})
LABEL_PREFIX = "io.agentrooms."
ENV_KEYS = ("COLIMA_HOME", "CONTAINERD_ADDRESS", "NERDCTL_TOML", "XDG_RUNTIME_DIR",
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR")
REPOSITORY_COMPONENT_RE = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*\Z")
REGISTRY_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*(?::[0-9]+)?\Z")


class RuntimeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _reject(code: str, message: str) -> None:
    raise RuntimeError(code, message)


def _exact_fields(value: Any, keys: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        _reject("invalidManifest", f"{where} requires exactly: {', '.join(sorted(keys))}")


def _text(value: Any, where: str, maximum: int = 256) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        _reject("invalidManifest", f"{where} must be a nonempty string of at most {maximum} characters")


def _relative_output(value: Any) -> str:
    _text(value, "output path", 512)
    if value.startswith("/") or "\\" in value or any(p in ("", ".", "..") for p in value.split("/")):
        _reject("invalidManifest", "output paths must be normalized, safe relative paths")
    return value


def _canonical_image_reference(value: Any) -> str:
    """Normalize the supported lowercase, tag-free Docker repository subset.

    Follow distribution/reference splitDockerDomain: an unqualified name uses
    Docker Hub, single-component Hub repositories use library/, and only the
    legacy index.docker.io hostname aliases docker.io. Preserve other registries,
    explicit ports, every namespace component, and the exact manifest digest.
    Config image IDs are a different digest and never identify this reference.
    """
    if not isinstance(value, str) or len(value) > 512 or value.count("@") != 1:
        raise ValueError("image must be pinned as repository@sha256:<64 lowercase hex>")
    repository, digest = value.split("@")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("image digest must be sha256:<64 lowercase hex>")
    if "/" not in repository:
        domain, remote = "docker.io", "library/" + repository
    else:
        first, remainder = repository.split("/", 1)
        if first == "localhost" or "." in first or ":" in first:
            domain, remote = first, remainder
        else:
            domain, remote = "docker.io", repository
        if domain == "index.docker.io":
            domain = "docker.io"
        if domain == "docker.io" and "/" not in remote:
            remote = "library/" + remote
    if not REGISTRY_RE.fullmatch(domain) or any(not REPOSITORY_COMPONENT_RE.fullmatch(part) for part in remote.split("/")):
        raise ValueError("image repository must use lowercase Docker repository syntax without tags or IPv6 literals")
    canonical = domain + "/" + remote
    if len(canonical) > 255:
        raise ValueError("canonical image repository exceeds 255 characters")
    return canonical + "@" + digest


def validate_manifest(value: Any) -> dict[str, Any]:
    _exact_fields(value, {"schemaVersion", "id", "taskId", "ownerAgentId", "image", "argv", "limits", "network", "outputs"}, "manifest")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        _reject("invalidManifest", "schemaVersion must be integer 1")
    if not isinstance(value["id"], str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?", value["id"]):
        _reject("invalidManifest", "id must be a lowercase alphanumeric slug, at most 48 characters")
    for key in ("taskId", "ownerAgentId"):
        _text(value[key], key)
    try:
        _canonical_image_reference(value["image"])
    except ValueError as exc:
        _reject("invalidManifest", str(exc))
    argv = value["argv"]
    if not isinstance(argv, list) or not 1 <= len(argv) <= 128:
        _reject("invalidManifest", "argv must contain 1 to 128 strings")
    for argument in argv:
        if not isinstance(argument, str) or "\0" in argument or len(argument) > 8192:
            _reject("invalidManifest", "argv entries must be strings without NUL, at most 8192 characters")
    if not argv[0]:
        _reject("invalidManifest", "argv[0] must name an executable")
    limits = value["limits"]
    _exact_fields(limits, {"cpus", "memoryMiB", "pids", "wallSeconds"}, "limits")
    cpus = limits["cpus"]
    if type(cpus) not in (int, float) or not 0.1 <= cpus <= 8 or not math.isfinite(cpus):
        _reject("invalidManifest", "limits.cpus must be a finite number from 0.1 to 8")
    for key, low, high in (("memoryMiB", 64, 16384), ("pids", 16, 1024), ("wallSeconds", 1, 86400)):
        if type(limits[key]) is not int or not low <= limits[key] <= high:
            _reject("invalidManifest", f"limits.{key} must be an integer from {low} to {high}")
    if value["network"] != "none":
        _reject("invalidManifest", "only network='none' is supported")
    if not isinstance(value["outputs"], list) or len(value["outputs"]) > 100:
        _reject("invalidManifest", "outputs must contain at most 100 file paths")
    for output in value["outputs"]:
        _relative_output(output)
    if len(set(value["outputs"])) != len(value["outputs"]):
        _reject("invalidManifest", "output paths must be unique")
    # Canonical JSON both detaches caller-owned dictionaries and bounds the input.
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > MAX_MANIFEST_BYTES:
        _reject("invalidManifest", "manifest exceeds 64 KiB")
    return json.loads(encoded)


def canonical_manifest(manifest: dict[str, Any]) -> tuple[str, str]:
    encoded = json.dumps(validate_manifest(manifest), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return encoded, hashlib.sha256(encoded.encode()).hexdigest()


def validate_prefix(prefix: Any) -> list[str]:
    if not isinstance(prefix, list) or not 1 <= len(prefix) <= 32:
        _reject("invalidEngine", "engine prefix must be a nonempty JSON array with at most 32 entries")
    for argument in prefix:
        if not isinstance(argument, str) or not argument or "\0" in argument or len(argument) > 4096:
            _reject("invalidEngine", "engine prefix entries must be nonempty strings without NUL")
    return list(prefix)


def _state_path(state_dir: str | Path) -> Path:
    path = Path(state_dir)
    if not path.is_absolute() or any(part == ".." for part in path.parts):
        _reject("invalidStateDirectory", "--state-dir must be an explicit absolute path without '..'")
    # Resolve once, before using this path in mounts or durable records.
    path = path.resolve()
    if any(c in str(path) for c in (",", "\n", "\r", "\0")):
        _reject("invalidStateDirectory", "state directory contains unsupported mount characters")
    return path


def build_plan(manifest: dict[str, Any], state_dir: str | Path, engine_prefix: list[str] | None = None, *, custody_token: str = "RUNTIME_GENERATED_TOKEN") -> dict[str, Any]:
    """Pure planning: no mkdir, SQLite connection, engine query, or process start."""
    manifest = validate_manifest(manifest)
    prefix = validate_prefix(engine_prefix if engine_prefix is not None else ["nerdctl"])
    _, digest = canonical_manifest(manifest)
    workspace = _state_path(state_dir) / "workspaces" / manifest["id"]
    limits = manifest["limits"]
    labels = {
        "runtime": "v1", "run-id": manifest["id"], "task-id": manifest["taskId"],
        "owner-agent-id": manifest["ownerAgentId"], "manifest-sha256": digest, "custody-token": custody_token,
    }
    command = prefix + ["--namespace", NAMESPACE, "run", "--detach", "--name", "ar-" + manifest["id"],
                        "--pull", "never", "--network", "none", "--read-only", "--no-healthcheck", "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges", "--pids-limit", str(limits["pids"]),
                        "--cpus", str(limits["cpus"]), "--memory", f'{limits["memoryMiB"]}m',
                        "--memory-swap", f'{limits["memoryMiB"]}m', "--user", "65534:65534",
                        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
                        "--mount", f"type=bind,src={workspace},dst=/workspace,rw",
                        "--workdir", "/workspace", "--log-driver", "json-file",
                        "--log-opt", "max-size=1m", "--log-opt", "max-file=1",
                        "--entrypoint", manifest["argv"][0]]
    for key, value in labels.items():
        command.extend(["--label", LABEL_PREFIX + key + "=" + value])
    # nerdctl consumes a first "--" after the image as its transport delimiter.
    # Supply our own so a user-provided leading "--" remains a real argument.
    command.extend([manifest["image"], "--", *manifest["argv"][1:]])
    return {"schemaVersion": 1, "namespace": NAMESPACE, "manifestSha256": digest,
            "workspace": str(workspace), "argv": command, "network": "none",
            "requiresPreloadedImage": True, "startsEngine": False,
            "wallDeadlineRequiresLiveSupervisor": True}


@dataclass
class CommandResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    truncated: bool = False


def execute(command: list[str], *, env: dict[str, str], timeout: float) -> CommandResult:
    """Drain both pipes continuously; retain at most 128 KiB per stream."""
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    except (OSError, ValueError) as exc:
        return CommandResult(None, stderr=str(exc)[:4096], error="engineUnavailable")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + max(0.01, timeout)
    truncated = False
    error = None
    selector = selectors.DefaultSelector()
    for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error = "engineTimeout"
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = buffers[key.data]
                room = MAX_CAPTURE_BYTES - len(buffer)
                buffer.extend(chunk[:room])
                truncated = truncated or len(chunk) > room
        if error is None:
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                error = "engineTimeout"
    finally:
        if error is not None or process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        selector.close()
        process.stdout.close()
        process.stderr.close()
    stdout = buffers["stdout"].decode("utf-8", "replace")
    stderr = buffers["stderr"].decode("utf-8", "replace")
    if error is None and process.returncode and re.search(r"unknown flag|unknown shorthand flag|unknown command|flag provided but not defined", stderr, re.I):
        error = "engineUnsupported"
    return CommandResult(process.returncode, stdout, stderr, error, truncated)


class Runtime:
    """One-attempt execution and evidence reconciliation for a caller-owned engine.

    The executor parameter is a test seam. Production uses the bounded subprocess
    executor above, with no shell invocation and no retry/fallback backend.
    """

    def __init__(self, state_dir: str | Path, engine_prefix: list[str] | None = None, *, executor=execute, poll_seconds: float = 0.25):
        self.state_dir = _state_path(state_dir)
        self.requested_prefix = validate_prefix(engine_prefix) if engine_prefix is not None else None
        self.executor = executor
        self.poll_seconds = max(0.01, min(poll_seconds, 5))
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.state_dir / "runtime.sqlite3", timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
            prefix_json TEXT NOT NULL, environment_json TEXT NOT NULL, custody_token TEXT NOT NULL,
            state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            deadline REAL NOT NULL, container_id TEXT, exit_code INTEGER, stop_reason TEXT,
            error_code TEXT, detail TEXT, engine_status TEXT, outputs_json TEXT
        )""")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def _get(self, run_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            _reject("runNotFound", f"no recorded run: {run_id}")
        return dict(row)

    def _update(self, run_id: str, **fields: Any) -> dict[str, Any]:
        fields["updated_at"] = time.time()
        # All field names are internal constants, never manifest or CLI inputs.
        self.db.execute("UPDATE runs SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?", [*fields.values(), run_id])
        self.db.commit()
        return self._get(run_id)

    def _public(self, record: dict[str, Any]) -> dict[str, Any]:
        manifest = json.loads(record["manifest_json"])
        return {"id": record["id"], "taskId": manifest["taskId"], "ownerAgentId": manifest["ownerAgentId"],
                "manifestSha256": record["manifest_sha256"], "state": record["state"],
                "createdAt": record["created_at"], "updatedAt": record["updated_at"], "deadline": record["deadline"],
                "containerId": record["container_id"], "exitCode": record["exit_code"],
                "engineStatus": record["engine_status"], "errorCode": record["error_code"], "detail": record["detail"],
                "workspace": str(self.state_dir / "workspaces" / record["id"]),
                "outputs": json.loads(record["outputs_json"]) if record["outputs_json"] else None}

    def list(self) -> list[dict[str, Any]]:
        return [self._public(dict(row)) for row in self.db.execute("SELECT * FROM runs ORDER BY created_at,id")]

    def status(self, run_id: str) -> dict[str, Any]:
        """Saved evidence only. Use inspect for a fresh engine observation."""
        result = self._public(self._get(run_id))
        result["observation"] = "saved; use inspect for current engine state"
        return result

    def _engine(self, record: dict[str, Any], arguments: list[str], timeout: float = 10) -> CommandResult:
        prefix = json.loads(record["prefix_json"])
        if self.requested_prefix is not None and prefix != self.requested_prefix:
            _reject("engineIdentityMismatch", "requested engine prefix differs from the recorded engine; no action taken")
        environment = dict(os.environ)
        # Reapply recorded routing, including removing values absent at creation.
        for key in ENV_KEYS:
            environment.pop(key, None)
        environment.update(json.loads(record["environment_json"]))
        return self.executor(prefix + ["--namespace", NAMESPACE, *arguments], env=environment, timeout=timeout)

    def _observe(self, record: dict[str, Any], *, timeout: float = 10) -> tuple[str, dict[str, Any] | None, str | None]:
        result = self._engine(record, ["inspect", "ar-" + record["id"]], timeout=timeout)
        if result.error or result.returncode != 0 or result.truncated:
            if not result.error and not result.truncated and re.search(r"no such container|container .* not found", result.stderr, re.I):
                return "missing", None, result.stderr[:4096]
            return result.error or "engineUnavailable", None, result.stderr[:4096]
        try:
            payload = json.loads(result.stdout)
            if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
                raise ValueError("expected exactly one inspected container")
            obj = payload[0]
            labels = obj["Config"]["Labels"]
            expected = {"runtime": "v1", "run-id": record["id"], "manifest-sha256": record["manifest_sha256"],
                        "custody-token": record["custody_token"]}
            manifest = json.loads(record["manifest_json"])
            expected.update({"owner-agent-id": manifest["ownerAgentId"], "task-id": manifest["taskId"]})
            if not isinstance(labels, dict) or any(labels.get(LABEL_PREFIX + key) != value for key, value in expected.items()):
                return "custodyMismatch", None, "container labels do not match this exact recorded run"
            container_id = obj.get("Id", obj.get("ID"))
            if not isinstance(container_id, str) or not re.fullmatch(r"[a-f0-9]{64}", container_id):
                raise ValueError("invalid full container ID")
            if obj.get("Name", "").lstrip("/") != "ar-" + record["id"]:
                return "custodyMismatch", None, "container name does not match"
            if record["container_id"] and record["container_id"] != container_id:
                return "custodyMismatch", None, "container ID changed since the last verified observation"
            state = obj["State"]
            if not isinstance(state, dict) or type(state.get("Running")) is not bool:
                raise ValueError("engine did not report a boolean running state")
            if not isinstance(state.get("Status"), str):
                raise ValueError("engine did not report a status")
            if not state["Running"] and state["Status"].lower() in ("exited", "dead") and type(state.get("ExitCode")) is not int:
                raise ValueError("engine did not report an exit code")
            return "found", {"containerId": container_id, "state": state}, None
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return "invalidEngineResponse", None, str(exc)

    def _admit_image(self, record: dict[str, Any]) -> None:
        """Prevent implicit image VOLUMEs from widening the writable mount set."""
        manifest = json.loads(record["manifest_json"])
        reference = manifest["image"]
        result = self._engine(record, ["image", "inspect", reference], timeout=min(10, max(0.01, record["deadline"] - time.time())))
        if result.error or result.returncode != 0 or result.truncated:
            _reject(result.error or "imageUnavailable", result.stderr[:4096] or "cannot inspect the preloaded image")
        try:
            payload = json.loads(result.stdout)
            if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
                raise ValueError("expected exactly one inspected image")
            obj = payload[0]
            image_id = obj.get("Id", obj.get("ID"))
            if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise ValueError("image inspection did not contain a full sha256 image ID")
            config = obj["Config"]
            if not isinstance(config, dict):
                raise ValueError("image inspection did not contain a Config object")
            volumes = config.get("Volumes")
            if volumes is not None and (not isinstance(volumes, dict) or volumes):
                _reject("imageVolumesUnsupported", "images declaring VOLUME paths are not admitted; they create additional writable mounts")
            digests = obj.get("RepoDigests")
            if not isinstance(digests, list) or any(not isinstance(item, str) for item in digests):
                raise ValueError("image inspection did not contain valid RepoDigests")
            canonical_digests = [_canonical_image_reference(item) for item in digests]
            if _canonical_image_reference(reference) not in canonical_digests:
                _reject("imageIdentityMismatch", "inspected RepoDigests do not match the pinned repository and exact manifest digest")
        except (ValueError, KeyError, TypeError) as exc:
            _reject("invalidImageResponse", str(exc))

    def inspect(self, run_id: str, *, timeout: float = 10) -> dict[str, Any]:
        record = self._get(run_id)
        outcome, observed, detail = self._observe(record, timeout=timeout)
        if outcome != "found":
            # A missing/dead engine is not evidence that its workloads stopped.
            if outcome == "missing" and record["state"] in TERMINAL and record["container_id"]:
                return self._public(self._update(run_id, error_code="containerMissing", detail="last verified terminal result retained; container is now absent"))
            return self._public(self._update(run_id, state="unknown", error_code=outcome, detail=detail))
        state = observed["state"]
        engine_status = state["Status"].lower()
        fields = {"container_id": observed["containerId"], "engine_status": engine_status, "error_code": None, "detail": None}
        if state["Running"] or engine_status == "paused":
            fields.update(state="running", exit_code=None)
        elif engine_status in ("exited", "dead"):
            latest = self._get(run_id)
            reason = latest["stop_reason"]
            fields.update(state=reason if reason in ("timed_out", "cancelled") else ("exited" if state["ExitCode"] == 0 else "failed"), exit_code=state["ExitCode"])
        elif engine_status in ("created", "creating"):
            fields.update(state="starting")
        else:
            fields.update(state="unknown", error_code="unrecognizedEngineState", detail=engine_status)
        return self._public(self._update(run_id, **fields))

    def run(self, manifest: dict[str, Any]) -> dict[str, Any]:
        manifest = validate_manifest(manifest)
        encoded, digest = canonical_manifest(manifest)
        run_id = manifest["id"]
        prefix = self.requested_prefix or ["nerdctl"]
        now = time.time()
        environment = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
        try:
            self.db.execute("""INSERT INTO runs (id,manifest_json,manifest_sha256,prefix_json,environment_json,custody_token,state,created_at,updated_at,deadline)
                               VALUES (?,?,?,?,?,?,?,?,?,?)""", (run_id, encoded, digest, json.dumps(prefix), json.dumps(environment), uuid.uuid4().hex, "prepared", now, now, now + manifest["limits"]["wallSeconds"]))
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            existing = self._get(run_id)
            if existing["manifest_sha256"] != digest:
                _reject("manifestIdentityConflict", "this id already belongs to a different manifest")
            _reject("runAlreadyRecorded", f"run {run_id} is already {existing['state']}; automatic replay is forbidden")
        record = self._get(run_id)
        try:
            self._admit_image(record)
        except RuntimeError as exc:
            return self._public(self._update(run_id, state="failed", error_code=exc.code, detail=str(exc)[:4096]))
        if time.time() >= record["deadline"]:
            return self._public(self._update(run_id, state="timed_out", error_code="deadlineBeforeLaunch", detail="wall deadline elapsed during image admission; no container creation attempted"))
        workspace_root = self.state_dir / "workspaces"
        workspace = workspace_root / run_id
        try:
            if workspace_root.is_symlink() or workspace.is_symlink():
                _reject("unsafeWorkspace", "workspace paths must not be symbolic links")
            workspace_root.mkdir(mode=0o700, exist_ok=True)
            # A preexisting directory has unknown custody, even if empty.
            workspace.mkdir(mode=0o1777, exist_ok=False)
            workspace.chmod(0o1777)
            plan = build_plan(manifest, self.state_dir, prefix, custody_token=record["custody_token"])
        except (OSError, RuntimeError) as exc:
            self._update(run_id, state="failed", error_code=getattr(exc, "code", "workspaceUnavailable"), detail=str(exc)[:4096])
            raise RuntimeError(getattr(exc, "code", "workspaceUnavailable"), str(exc)) from exc
        if time.time() >= record["deadline"]:
            return self._public(self._update(run_id, state="timed_out", error_code="deadlineBeforeLaunch", detail="wall deadline elapsed during workspace preparation; no container creation attempted"))
        record = self._update(run_id, state="starting")
        # Commit intent before invoking the engine. Never retry this command.
        try:
            result = self._engine(record, plan["argv"][len(prefix) + 2:], timeout=min(30, max(0.01, record["deadline"] - time.time())))
            if result.error == "engineUnavailable" and result.returncode is None:
                return self._public(self._update(run_id, state="failed", error_code=result.error, detail=result.stderr[:4096]))
            if result.error or result.returncode != 0:
                self._update(run_id, state="unknown", error_code=result.error or "engineRunFailed", detail=result.stderr[:4096])
                # Even a command error can follow container creation. Reconcile,
                # but never assume that a missing result means no side effect.
                checked = self.inspect(run_id)
                if checked["state"] in TERMINAL:
                    return checked
                if result.error == "engineUnsupported" and checked["state"] not in ("running", "starting"):
                    self._update(run_id, error_code=result.error or "engineRunFailed", detail=result.stderr[:4096])
                    return self.status(run_id)
            while True:
                if time.time() >= record["deadline"]:
                    return self.stop(run_id, reason="timed_out")
                checked = self.inspect(run_id, timeout=max(0.01, min(10, record["deadline"] - time.time())))
                if time.time() >= record["deadline"] and checked["state"] not in TERMINAL:
                    return self.stop(run_id, reason="timed_out")
                if checked["state"] in TERMINAL:
                    return checked
                time.sleep(min(self.poll_seconds, max(0.01, record["deadline"] - time.time())))
        except KeyboardInterrupt:
            # Cancellation is only claimed after an owned container is observed
            # stopped. A crashed/killed controller simply leaves durable intent.
            self.stop(run_id)
            raise

    def stop(self, run_id: str, *, reason: str = "cancelled") -> dict[str, Any]:
        if reason not in ("cancelled", "timed_out"):
            _reject("invalidStopReason", "stop reason must be cancelled or timed_out")
        record = self._get(run_id)
        outcome, observed, detail = self._observe(record)
        if outcome != "found":
            return self._public(self._update(run_id, state="unknown", error_code=outcome, detail=detail))
        if not observed["state"]["Running"] and observed["state"]["Status"].lower() in ("exited", "dead"):
            return self.inspect(run_id)
        # Pin the full ID before action; never stop an unverified same-name run.
        record = self._update(run_id, stop_reason=reason, container_id=observed["containerId"])
        result = self._engine(record, ["stop", "--time", "2", observed["containerId"]], timeout=10)
        checked = self.inspect(run_id)
        if checked["state"] not in TERMINAL:
            return self._public(self._update(run_id, state="unknown", error_code=result.error or "stopUnconfirmed", detail=result.stderr[:4096] or "engine has not confirmed that the container stopped"))
        return checked

    def recover(self, run_id: str) -> dict[str, Any]:
        """Never replay creation; only reconcile custody and stop expired work."""
        checked = self.inspect(run_id)
        record = self._get(run_id)
        if checked["state"] in ("starting", "running") and time.time() >= record["deadline"]:
            return self.stop(run_id, reason="timed_out")
        if checked["state"] in ("starting", "running") and record["stop_reason"] in ("cancelled", "timed_out"):
            return self.stop(run_id, reason=record["stop_reason"])
        return checked

    def outputs(self, run_id: str) -> dict[str, Any]:
        checked = self.inspect(run_id)
        if checked["state"] not in TERMINAL or checked["errorCode"]:
            _reject("outputsNotReady", "output verification requires a fresh, owned, stopped container observation")
        record = self._get(run_id)
        manifest = json.loads(record["manifest_json"])
        workspace = self.state_dir / "workspaces" / run_id
        artifacts = []
        root_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for relative in manifest["outputs"]:
                parent_fd = os.dup(root_fd)
                descriptor = None
                try:
                    parts = relative.split("/")
                    for part in parts[:-1]:
                        next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                        os.close(parent_fd)
                        parent_fd = next_fd
                    descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
                    before = os.fstat(descriptor)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_OUTPUT_BYTES:
                        _reject("unsafeOutput", "outputs must be single-link regular files, at most 1 GiB each")
                    digest = hashlib.sha256()
                    total = 0
                    while True:
                        chunk = os.read(descriptor, 1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_OUTPUT_BYTES:
                            _reject("unsafeOutput", "output grew beyond the 1 GiB verification limit")
                        digest.update(chunk)
                    after = os.fstat(descriptor)
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or total != after.st_size:
                        _reject("outputChanged", "output changed during verification")
                    artifacts.append({"path": relative, "bytes": total, "sha256": digest.hexdigest()})
                except OSError as exc:
                    raise RuntimeError("unsafeOutput", f"cannot safely open output {relative}: {exc}") from exc
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
                    os.close(parent_fd)
        finally:
            os.close(root_fd)
        self._update(run_id, outputs_json=json.dumps(artifacts, sort_keys=True))
        return {"id": run_id, "manifestSha256": record["manifest_sha256"], "verifiedAt": time.time(), "outputs": artifacts}
