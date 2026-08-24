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

Promotion is pull/observe only. `request-promotion.yml` validates and commits
one immutable request with this repository's scoped `GITHUB_TOKEN`. A target's
scheduled/manual workflow reads that exact request and writes only its own
repository. `finalize-promotion.yml` observes the target acknowledgement and
commits one receipt here. No PAT, shared secret, or cross-repository write token
exists. Requests are not trusted by clients; only finalized immutable receipts
authorize a manifest.

Satellite repositories keep only their current manifest, validation, CI, and
human semantics. Source and build work remains canonical.

## Real-time practice

`observe-main.yml` runs twice hourly and manually. It reads the exact current
`kody-w/openrappter` `main` SHA, requires every check in
`config/required-main-checks.json` to be completed successfully for that exact
SHA, rechecks HEAD immediately before writing, and creates one deterministic
nightly request. A red, pending, missing, or superseded check creates nothing.
It never writes the nightly repository and never requests alpha or later.

Immediately after a release-relevant merge:

The generated helper reads live package manifests, request indexes, authority
heads, and immutable acknowledgement paths instead of copying IDs:

```sh
./scripts/release-ring-journey.sh --channel-version 0.1.0-beta.11
```

```sh
candidate_commit="$(gh api repos/kody-w/openrappter/commits/main --jq .sha)"
package_version="$(gh api "repos/kody-w/openrappter/contents/typescript/package.json?ref=$candidate_commit" --jq .content | base64 --decode | jq -r .version)"
intended_release_tag="v$package_version"
gh workflow run build-candidate.yml -R kody-w/openrappter \
  -f source_commit="$candidate_commit" \
  -f channel_version=0.1.0-beta.11 \
  -f intended_release_tag="$intended_release_tag" \
  -f candidate_kind=release
gh run watch -R kody-w/openrappter \
  "$(gh run list -R kody-w/openrappter --workflow build-candidate.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh workflow run observe-main.yml -R kody-w/openrappter-release-train \
  -f candidate_kind=release -f candidate_id="$intended_release_tag"
gh run watch -R kody-w/openrappter-release-train \
  "$(gh run list -R kody-w/openrappter-release-train --workflow observe-main.yml --limit 1 --json databaseId --jq '.[0].databaseId')"

index="$(gh api repos/kody-w/openrappter-release-train/contents/request-index/nightly.json --jq .content | base64 --decode)"
request_path="$(printf '%s' "$index" | jq -r '.entries[-1].path')"
request_sequence="$(printf '%s' "$index" | jq -r '.entries[-1].sequence')"
request_commit="$(gh api "repos/kody-w/openrappter-release-train/commits?path=$request_path&per_page=1" \
  --jq '.[0].sha')"

gh workflow run apply-promotion.yml -R kody-w/openrappter-nightly \
  -F request_sequence="$request_sequence"
gh run watch -R kody-w/openrappter-nightly \
  "$(gh run list -R kody-w/openrappter-nightly --workflow apply-promotion.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh workflow run finalize-promotion.yml -R kody-w/openrappter-release-train \
  -f request_commit="$request_commit" -f request_path="$request_path"
gh run watch -R kody-w/openrappter-release-train \
  "$(gh run list -R kody-w/openrappter-release-train --workflow finalize-promotion.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Promote the chosen finalized beta.11 snapshot explicitly, one ring at a time:

```sh
# Repeat with: "nightly alpha", then "alpha canary", then "canary beta".
set -- nightly alpha
source_ring="$1"; target_ring="$2"
source_repo="kody-w/openrappter-$source_ring"
source_head="$(gh api "repos/kody-w/openrappter-release-train/contents/heads/$source_ring.json" --jq .content | base64 --decode)"
source_commit="$(printf '%s' "$source_head" | jq -r .target_manifest_commit)"
source_url="https://raw.githubusercontent.com/$source_repo/$source_commit/.ring/manifest.json"

gh workflow run request-promotion.yml -R kody-w/openrappter-release-train \
  -f source_manifest_url="$source_url" -f target_ring="$target_ring"
gh run watch -R kody-w/openrappter-release-train \
  "$(gh run list -R kody-w/openrappter-release-train --workflow request-promotion.yml --limit 1 --json databaseId --jq '.[0].databaseId')"

index="$(gh api "repos/kody-w/openrappter-release-train/contents/request-index/$target_ring.json" --jq .content | base64 --decode)"
request_path="$(printf '%s' "$index" | jq -r '.entries[-1].path')"
request_sequence="$(printf '%s' "$index" | jq -r '.entries[-1].sequence')"
request_commit="$(gh api "repos/kody-w/openrappter-release-train/commits?path=$request_path&per_page=1" \
  --jq '.[0].sha')"
gh workflow run apply-promotion.yml -R "kody-w/openrappter-$target_ring" \
  -F request_sequence="$request_sequence"
gh run watch -R "kody-w/openrappter-$target_ring" \
  "$(gh run list -R "kody-w/openrappter-$target_ring" --workflow apply-promotion.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh workflow run finalize-promotion.yml -R kody-w/openrappter-release-train \
  -f request_commit="$request_commit" -f request_path="$request_path"
gh run watch -R kody-w/openrappter-release-train \
  "$(gh run list -R kody-w/openrappter-release-train --workflow finalize-promotion.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Each next request is accepted only after the preceding ring has a finalized
immutable receipt. Stable remains a separate later decision.

**Before merging openrappter#437**, switch Pages from legacy `main/docs` to
workflow deployment:

```sh
gh api repos/kody-w/openrappter/pages --method PUT \
  --input <(printf '%s\n' '{"build_type":"workflow"}')
```

GitHub retains the last successful Pages deployment when workflow source is
selected but `pages.yml` is not yet present or a later constitution check
fails; this command does not delete the current site. After #437 merges, the
first deployment occurs only if finalized stable candidate installer bytes
match exactly.

Latest-state authority is `heads/<ring>.json`, not a target repository's
mutable `main`. Each head advances a monotonic sequence and names one immutable
receipt plus the exact target manifest commit it authorized. Finalization
validates the target acknowledgement before creating or reusing a receipt, then
repairs/advances the head idempotently. Ring-repository current files are
informational mirrors only.

Distribution is separately governed by
[`RELEASE_CONSTITUTION.md`](RELEASE_CONSTITUTION.md). The named **Release
Constitution** check must verify the exact finalized nightly→alpha→canary→beta
chain before any stable/tag/registry/release/installer publication. The
ruleset payload is intentionally committed but not applied; apply it only after
the check workflow has merged and appeared on `main`.
