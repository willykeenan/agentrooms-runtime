# Roadmap

The ambition is a complete agent-oriented environment lifecycle. This list records
planned work; it is not a claim that these capabilities are already available.

| Area | Alpha state | Next acceptance evidence |
| --- | --- | --- |
| Task manifests and lifecycle | Implemented; simulated-engine tests | Live engine compatibility and failure injection |
| Isolation and limits | Adapter requests restrictions | Verified VM/container boundary, quotas and guardian deadlines |
| Agent permissions | Owner labels only | Authenticated, revocable, task-scoped capabilities |
| Environments and builds | Preloaded pinned images only | Reproducible cached builds, provenance and policy admission |
| Workspaces and artifacts | Fresh directories and output hashes | Quotas, immutable exports and authorized imports |
| Checkpoints and branching | Planned | Verified snapshot, restore and fork semantics |
| Network and secrets | Network disabled; no secret API | Scoped egress, short-lived secret injection and redaction |
| Scheduling | One local operator | Admission, fair allocation, queue recovery and budget enforcement |
| Local and remote compute | Separately configured engine | Verified transport, placement and resource reporting |
| Agentrooms integration | Planned | Real app controls, task activity and artifact views |
| Build, preview and deployment | Planned | Separate preview/release permissions, promotion and rollback |
| Compatibility and operations | Limited nerdctl adapter | Tested backend matrix, portability, upgrades and recovery |

Prioritize the complete loop: submit one authorized task, isolate it, survive a
controller failure, enforce limits, verify artifacts and show its result in
Agentrooms. Then measure startup time, resource overhead, reproducibility,
recovery correctness and end-to-end task completion against declared baselines.
No “faster than Docker” or stronger-isolation claim is made without those results.
