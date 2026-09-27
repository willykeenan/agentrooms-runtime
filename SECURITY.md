# Security and trust model

This experimental release is not a verified security boundary for hostile code or
untrusted tenants. It has no authenticated network API. Run it only as a trusted
operator against a separately provisioned engine. Do not give agents the host's
engine socket or permission to change the state database or engine configuration.

The engine, image contents, operator environment and host/VM setup are trusted.
Only a fresh workspace should be shared with a workload; never mount runtime state,
credentials, unrelated files or configuration. Owner/task labels are supplied by
the caller and do not prove identity or authorization.

Requested restrictions include no network, a read-only root, a fixed nonroot UID,
no Linux capabilities, no-new-privileges and bounded cgroup resources. These flags
are not proof of enforcement. The supported engine/version matrix is not yet
established by live integration tests. Unsupported flags must fail, not be removed.

Known gaps include workspace and aggregate disk quotas, a guardian surviving the
controller, authenticated engine identity, multi-tenant admission, verified seccomp
policy, immutable artifacts and encrypted secret delivery. A crashed controller
can leave work running. Do not treat a failed stop or missing engine as a successful
termination. Local output hashes do not grant permission to publish their contents.

Report vulnerabilities using GitHub's private vulnerability reporting if available:
https://github.com/willykeenan/agentrooms-runtime/security/advisories/new
If unavailable, open an issue requesting a private reporting channel without
including exploit details, secrets or private task content. No response-time or
support guarantee is offered for this alpha.
