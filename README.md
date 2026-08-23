# OpenRappter Release Train

This repository is the authority for five maintained **pointers**, not five
copies of OpenRappter:

```
nightly -> alpha -> canary -> beta -> stable
```

* **nightly** is a vetted snapshot of canonical `main`.
* **alpha** is an explicit early promotion selected from nightly.
* **canary** requires a real smoke test or limited rollout of that exact source.
* **beta** is a prerelease candidate.
* **stable** is the production release in `kody-w/openrappter`.

Every pointer uses the closed `openrappter-ring/v1` contract in
[`schema/openrappter-ring-v1.schema.json`](schema/openrappter-ring-v1.schema.json).
Release identity is an exact 40-hex canonical commit, optional immutable tag,
exact version, artifact URL, and SHA-256. A branch URL such as `main` is never a
release identity.

## Validate and promote

```sh
python scripts/ringctl.py validate path/to/manifest.json --ring nightly
python scripts/ringctl.py promote path/to/nightly.json --to alpha
python -m unittest discover -s tests -v
```

Promotion advances exactly one ring, rejects version downgrade, can check Git
ancestry, and emits a closed dispatch payload. The reusable
`promote.yml` workflow refuses to dispatch unless `RING_AUTHORITY_TOKEN` is
present. It does not invent or claim signatures: receipts preserve the SHA-256
and GitHub provenance that was actually measured.

Satellite repositories keep only their current manifest, validation, CI, and
human semantics. Source and build work remains canonical.
