# Third-party notices

## distribution/reference v0.6.0

The repository grammar and `_canonical_image_reference` in
`agentrooms_runtime/core.py` adapt the normalization rules in
[`normalize.go`](https://github.com/distribution/reference/blob/v0.6.0/normalize.go)
and the grammar in
[`regexp.go`](https://github.com/distribution/reference/blob/v0.6.0/regexp.go).

Upstream project: https://github.com/distribution/reference/tree/v0.6.0

The upstream project is licensed under the Apache License, Version 2.0. The full license text is included in [LICENSE](LICENSE); the upstream
license is https://github.com/distribution/reference/blob/v0.6.0/LICENSE.

Changes: translated a restricted normalization subset into Python, required an
explicit SHA-256 manifest digest, rejected tags, uppercase repositories and IPv6
literals, and added runtime-specific admission checks. This is not the complete
upstream parser and does not claim full Docker reference compatibility.

## External execution tools

nerdctl, containerd and optional VM managers are separately installed dependencies.
Their binaries, images and configuration are not included in this repository.
Their respective projects retain their own licenses and trademarks. Agentrooms
Runtime is an independent project and is not endorsed by Docker or those projects.
