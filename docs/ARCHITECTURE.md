# Architecture

## Unit of work

The intended primitive is a task workspace: a task, an owning agent, an execution
contract, writable working files, resource limits and declared outputs. Images
supply the environment. A task may eventually span many attempts and checkpoints;
this alpha supports one attempt for each unique run ID.

```text
Operator -> validated task manifest -> durable local run record
                                   -> nerdctl -> containerd -> workload
Operator <- observed run state      <- inspection / custody checks
Operator <- output file hashes      <- terminated workspace
```

The runtime is a client and policy layer over an existing engine. The engine,
host and any VM are separately provisioned trust boundaries. Reusing OCI tooling
preserves the existing image ecosystem while allowing agent-oriented lifecycle
and permission APIs to evolve independently.

## Current components

`validate_manifest` checks a versioned strict schema and produces detached,
canonical JSON. `build_plan` returns the argv and policies without side effects.
`Runtime` stores intent and observations in SQLite before engine operations.
The JSON CLI exposes validate, plan, run, list, status, inspect, stop, recover and
outputs. The browser preview is a separate convenience interface; it cannot
execute work and is not an authorization boundary.

Each run records its engine prefix and selected routing environment. Random
custody labels bind later operations to the recorded task, manifest and container.
These checks guard accidental misrouting; they do not authenticate an engine or
turn arbitrary caller-provided owner labels into agent identity.

## Failure model

A submitted run ID is never recreated automatically, even after uncertainty.
Missing or inconsistent engine observations remain unknown. An explicit recover
operation reconciles state and can stop an overdue, verified owned container.
A new execution needs a new ID. There is no cross-host exactly-once guarantee.

The controller enforces wall-time only while alive, with bounded engine calls and
stop confirmation after the deadline. Process death, engine outage and disk
exhaustion remain operational risks. A future guardian, durable lease and admission
controller must address them before unattended multi-tenant use.

## Proposed next boundaries

An authenticated broker should own the engine socket, trusted state and capability
grants. Agent clients should receive narrow task-scoped APIs rather than host shell
or arbitrary engine access. Durable artifacts should be separately authorized,
immutable and quota-controlled. Build services, runtime workers and deployment
controllers should have separate permissions and failure domains. None of these
services is delivered by this alpha.
