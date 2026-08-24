#!/usr/bin/env bash
set -euo pipefail

# Run only after the Release Constitution check exists on merged main.
repo="${1:-kody-w/openrappter}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gh api "repos/$repo/rulesets" \
  --method POST \
  --input "$root/rulesets/release-constitution.json"
