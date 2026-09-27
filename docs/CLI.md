# Agentrooms task runtime, experimental first slice

This stdlib Python package makes a task manifest, owning agent, bounded execution
intent, durable result, and declared output hashes the primary interface to an
existing containerd engine. It does not provision a VM or implement a kernel,
container image builder, Docker compatibility layer, or production sandbox.

## Manifest

```json
{
  "schemaVersion": 1,
  "id": "example-task-01",
  "taskId": "task-example",
  "ownerAgentId": "agent-example",
  "image": "example/workload@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "argv": ["/bin/sh", "-c", "printf 'hello\\n' > result.txt"],
  "limits": {"cpus": 1, "memoryMiB": 256, "pids": 64, "wallSeconds": 30},
  "network": "none",
  "outputs": ["result.txt"]
}
```

The digest above is an illustrative placeholder, not an available image. Images
must already be loaded into the selected engine; execution never pulls images.
Only `repository@sha256:<64 hex>` references are accepted. Bare digest references
are rejected because nerdctl's manifest target and inspected config image ID are
different identities. Admission compares normalized repositories and the exact
manifest digest from `RepoDigests`; it never equates that digest with config `Id`.
Docker Hub familiar names (`alpine`, `library/alpine`, `docker.io/alpine`, and
`docker.io/library/alpine`) and the legacy `index.docker.io` hostname normalize to
the same repository. Other registry names, ports, and repository namespaces stay
distinct. This stage accepts lowercase DNS/IPv4 registry names with optional
numeric ports; tagged digest references and IPv6 literals are unsupported.
`argv[0]` replaces the
image entrypoint, and subsequent entries are passed as individual arguments.
The adapter inserts nerdctl's consumed `--` delimiter after the image so an
actual leading `--` in the workload arguments is preserved.
There is no host shell evaluation. A workload may explicitly run its own shell
inside its container, as in this example.

All fields are required and additional fields are rejected. CPU is 0.1–8;
memory is 64–16384 MiB; PID count is 16–1024; wall time is 1–86400 seconds.
Outputs are up to 100 unique, normalized relative paths. Verification accepts
only single-link regular files up to 1 GiB each, rejecting symlink traversal,
special files, and files that change while hashing.

## Commands

Install as described in the repository README. Global options precede commands.
Choose an absolute local state directory that remains under the operator's control.

```sh
agentrooms-runtime validate examples/task.json
agentrooms-runtime --state-dir "$PWD/.agentrooms-state" plan examples/task.json
agentrooms-runtime --state-dir "$PWD/.agentrooms-state" run my-task.json
agentrooms-runtime --state-dir "$PWD/.agentrooms-state" inspect my-run-id
agentrooms-runtime --state-dir "$PWD/.agentrooms-state" outputs my-run-id
```

`validate` and `plan` do not create directories, mutate SQLite, contact an engine
or start a process. `plan` includes a placeholder for the fresh custody token.
For a separately provisioned remote or VM engine, `--engine-prefix-json` accepts
an argv array; no host shell is evaluated. The default is `["nerdctl"]`.
The engine must see each workspace at the same absolute path as the client.

`status ID` and `list` return saved evidence, explicitly marked as such.
`inspect ID` obtains a fresh engine observation. `stop ID` only stops a container
whose name, full ID when known, manifest hash, task, owner, and random custody
token match the durable record. `recover ID` reconciles the same evidence and
stops owned work whose deadline passed or whose recorded cancellation remains
pending. It never recreates a workload.

`run` returns the execution result; it does not automatically verify declared
outputs. Run `outputs ID` separately to collect their hashes after confirmed
termination. A zero execution exit code alone does not establish artifact
completeness.

Only the `<state-dir>/workspaces` subtree is shared to the VM at the identical host
path. Keep `<state-dir>/runtime.sqlite3` and all engine/config/cache directories
outside the guest share. Verify the guest's nonroot UID can write the workspace
with the actual host/guest file-sharing driver before admitting a workload.

The engine prefix and relevant routing environment (`COLIMA_HOME`,
`CONTAINERD_ADDRESS`, `NERDCTL_TOML`, `XDG_RUNTIME_DIR`, `XDG_CONFIG_HOME`,
`XDG_CACHE_HOME`, `TMPDIR`) are stored per run and
reused for later commands. A supplied prefix that differs from the original is
rejected. These are routing pins and custody checks, not cryptographic engine
attestation. State directories and engine configuration must remain under the
trusted operator's control. Do not mount the state database into a workload.

## Execution and recovery guarantees

- Namespace `agentrooms`, network disabled, read-only root filesystem, all Linux
  capabilities dropped, no-new-privileges, nonroot UID/GID 65534, bounded cgroup
  resources, 64 MiB `/tmp`, and one writable `/workspace` bind are mandatory.
- Before creating a container, image inspection must match the exact pinned
  identity and contain valid image configuration. Images declaring `VOLUME`
  paths are rejected because nerdctl otherwise adds writable anonymous volumes.
  Image healthchecks are disabled with `--no-healthcheck`; only the declared
  workload argv is requested. An elapsed deadline prevents container creation.
- The writable workspace is newly created for each ID. Its parent is private to
  the operator; the bind root is mode 1777 to support the fixed nonroot UID.
- Detached container logs request `json-file`, 1 MiB rotation and one retained
  file. Client subprocess capture is bounded to 128 KiB per stream and drains
  excess output. Unsupported engine flags fail closed; flags are never removed
  to obtain a successful launch.
- SQLite records prepared/starting intent before invoking the engine and refuses
  every repeated run ID. Reusing an ID with different bytes is an identity
  conflict. A completed, failed, interrupted, or uncertain run cannot be replayed
  through `run`; use a new explicitly chosen task-run ID for new work.
- States are prepared, starting, running, exited, failed, timed_out, cancelled,
  and unknown. Missing engine access, malformed inspection, custody mismatches,
  or unconfirmed stop remain unknown. Killing the controller does not imply that
  its container stopped.
- Transient inspection failures after creation do not end the live supervisor:
  it continues bounded reconciliation until termination or the deadline, then
  attempts a custody-checked stop. It never repeats container creation.

## Deliberate limitations and proof gaps

The synchronous controller requests a stop at wallSeconds only while alive.
It does not provide a hard elapsed-time bound: custody inspection, engine stop,
and confirmation each allow up to 10 seconds, and an unreachable engine leaves
the outcome unknown. A killed controller can leave its container running; an explicit `recover` reconciles
and stops overdue owned work. Crash-proof deadlines require an independently
supervised guardian or engine-enforced lease and are not delivered here.

Workspace storage has no quota in this slice. No host secret or arbitrary mount
fields exist, but image contents and the configured engine are trusted inputs.
Image scanning, image acquisition, authenticated engine identity, seccomp policy
verification, cross-run admission, VM disk quotas, agent capability grants,
automatic service recovery, UI integration, checkpoints, egress brokering,
and production hostile-code isolation remain separate work.

There is no automatic container/image/workspace deletion. Output hashes are
point-in-time evidence, not immutable artifact storage. Local state does not
authorize publishing or consuming those artifacts elsewhere.

Tests use a simulated engine and real bounded local subprocesses. Passing them
does not establish that a live nerdctl/Colima version accepts the flags, enforces
cgroups, rotates logs, or supplies the expected inspect fields. Those claims
require live integration checks against the exact provisioned engine.
