# OpenRappter Release Constitution

Machine contract: [`openrappter-release-constitution/v1`](contracts/release-constitution-v1.json)  
Required check: **Release Constitution**

Every distributable OpenRappter commit must traverse, in order:

```
nightly -> alpha -> canary -> beta
```

Each ring must have a finalized immutable `promotion` receipt binding the same
canonical source commit, version, artifact/install URL, provenance, and
SHA-256. Only then may any workflow create or move stable, a Git tag, GitHub
release, npm/PyPI package, or installer channel/manifest.

There is no direct stable path, skipped ring, mutable receipt, hostname-only
artifact trust, or bypass flag. Emergency rollback may select only an exact
artifact already covered by a complete finalized chain and must create a
separate immutable rollback receipt.

The only publishable bytes are the inner files of the exact immutable
`github-candidate-bundle-sha256` bundle finalized by beta. Registry, release,
and Pages jobs compare their local files against that bundle and never rebuild
after the gate.

Candidate URLs obey the closed `openrappter-candidate-url/v1` grammar:
`https://raw.githubusercontent.com/kody-w/openrappter/<40hex-ref>/candidates/<40hex-source>/<snapshot|release>/<ASCII-candidate-id>/<64hex-sha>.tar.gz`.
IDs are 1–128 ASCII characters matching `[A-Za-z0-9][A-Za-z0-9._-]*`;
percent-encoding, Unicode, slashes, credentials, queries, fragments, extra
segments, `.` and `..` are rejected.

Local builds, tests, development, and the release-ring selector are outside
this distribution gate. Prose explains the rule; `release_gate.py` and the
static workflow tests enforce it.
