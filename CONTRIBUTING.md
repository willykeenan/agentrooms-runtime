# Contributing

Use Python 3.9+ on Linux or macOS. Install the package in a virtual environment and
run `python -m unittest discover -s tests -v`. The static preview uses browser
JavaScript without a bundler; run its tests with `node --test site/*.test.mjs`.

Keep changes bounded, add tests for meaningful behavior, and distinguish simulated
engine results from actual integration evidence. Never silently relax resource,
network, custody or path restrictions to make a backend work. For engine findings,
include exact versions, sanitized manifests, requested flags and observed behavior.
Do not commit runtime state, image contents, host paths, credentials or task data.

Pull requests should state the user-visible behavior, test evidence and remaining
limits. Code and documentation contributions are provided under Apache-2.0. Follow
upstream attribution requirements when adapting third-party code. Be respectful
and keep discussion focused on reproducible behavior.
