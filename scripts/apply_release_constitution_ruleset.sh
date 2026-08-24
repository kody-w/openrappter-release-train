#!/usr/bin/env bash
set -euo pipefail

# Run only after the Release Constitution check exists on merged main.
repo="${1:-kody-w/openrappter}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ruleset="$root/rulesets/release-constitution.json"
existing_ids="$(
  gh api "repos/$repo/rulesets" \
    --jq '.[] | select(.name == "Release Constitution") | .id'
)"
count="$(printf '%s\n' "$existing_ids" | grep -c '[0-9]' || true)"
if [[ "$count" -gt 1 ]]; then
  echo "Refusing to update: multiple Release Constitution rulesets exist." >&2
  exit 1
fi
existing_id="$(printf '%s\n' "$existing_ids" | head -n 1)"
if [[ -n "$existing_id" ]]; then
  gh api "repos/$repo/rulesets/$existing_id" \
    --method PUT \
    --input "$ruleset"
else
  gh api "repos/$repo/rulesets" \
    --method POST \
    --input "$ruleset"
fi
