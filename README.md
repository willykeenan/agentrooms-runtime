---
title: Agentrooms Runtime
emoji: 🧩
colorFrom: blue
colorTo: green
sdk: static
app_file: site/index.html
pinned: false
license: apache-2.0
short_description: Experimental task workspaces for agents and a local manifest preview
---

# Agentrooms Runtime

**Task workspaces for agents. Open source by [KE Studios](https://kestudios.dev).**

Agentrooms Runtime explores a container workflow built around an agent's task:
who owns the work, what may run, how long it may run, what happened, and which
outputs were produced. The long-term aim is an agent-oriented alternative to the
usual build, run and deploy workflow, integrated into Agentrooms.

**0.1.0a1 is an experimental foundation.** It contains a Python manifest validator,
execution planner and durable adapter for a separately provisioned nerdctl /
containerd engine. It is not a complete Docker replacement, an authenticated agent
service or a production hostile-code sandbox. Backend isolation and performance
advantages have not been demonstrated. Agentrooms app integration is planned.

[Try the manifest preview](https://huggingface.co/spaces/willykeenan/agentrooms-runtime)
· [CLI guide](docs/CLI.md) · [Architecture](docs/ARCHITECTURE.md)
· [Roadmap](docs/ROADMAP.md) · [Security](SECURITY.md)

## Start without a container engine

Python 3.9+ on Linux or macOS is required. The runtime has no third-party Python
runtime dependencies. Install from this repository in a virtual environment:

```sh
git clone https://github.com/willykeenan/agentrooms-runtime.git
cd agentrooms-runtime
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
agentrooms-runtime validate examples/task.json
agentrooms-runtime --state-dir "$PWD/.agentrooms-state" plan examples/task.json
```

`validate` and `plan` are local, do not start an engine, and do not create state.
The example image digest is intentionally a placeholder; it cannot run as-is.
The Hugging Face Space previews JSON locally in the browser and downloads manifests.
It does not execute containers or send task data to a server. The Python CLI is the
authoritative validator.

## What is implemented

- A strict task contract: run ID, task ID, owning-agent label, digest-pinned image,
  explicit argv, CPU/memory/PID/wall-time limits and declared output paths.
- An inspectable execution plan with networking disabled, read-only root,
  nonroot user, dropped capabilities and one writable workspace.
- Durable execution intent in SQLite, one attempt per run ID and custody checks
  before inspecting or stopping recorded containers.
- Bounded client output capture, requested container log rotation, cancellation
  and explicit recovery of uncertain or overdue work.
- Output file hashes with symlink, hardlink and unsafe-path rejection.

These are implemented policies and adapter behavior. A configured engine must
still be verified to enforce them. Agent labels are metadata, not authentication.

## Running work

Provision and verify a dedicated engine separately. Preload a digest-pinned image;
this runtime never pulls images. Confirm the engine sees the new workspace at the
same absolute path and UID 65534 can write it. Keep the state database and host
configuration outside any VM share. See the [CLI guide](docs/CLI.md) before running.

```sh
agentrooms-runtime --state-dir "$PWD/.agentrooms-state" run my-task.json
agentrooms-runtime --state-dir "$PWD/.agentrooms-state" inspect my-run-id
agentrooms-runtime --state-dir "$PWD/.agentrooms-state" outputs my-run-id
```

A successful process exit does not prove the requested outputs exist. Collect
output evidence separately. A controller crash can leave a container running;
`recover` reconciles an existing run and never starts it again.

## Current limits

No persistent guardian, workspace disk quota, multi-user authentication, agent
capability broker, build service, checkpoint/fork support, cross-host scheduler,
Docker/Compose compatibility layer or Agentrooms UI integration is included yet.
Wall-time stopping depends on a live controller and reachable engine. Output hashes
are point-in-time evidence, not immutable storage. Do not use this alpha to isolate
hostile or untrusted tenants. See [SECURITY.md](SECURITY.md).

## Development

```sh
python -m unittest discover -s tests -v
node --test site/*.test.mjs
```

Runtime tests use a simulated engine and bounded local subprocesses. They do not
establish live engine compatibility or security isolation. Contributions that add
reproducible integration evidence are particularly useful; see
[CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under [Apache-2.0](LICENSE). Third-party adaptations are recorded in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
