#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ruleset="$root/rulesets/ring-pointer-history.json"
allowed=(
  kody-w/openrappter-nightly
  kody-w/openrappter-alpha
  kody-w/openrappter-canary
  kody-w/openrappter-beta
)
repos=("$@")
if [[ "${#repos[@]}" -eq 0 ]]; then
  repos=("${allowed[@]}")
fi

for repo in "${repos[@]}"; do
  permitted=false
  for candidate in "${allowed[@]}"; do
    if [[ "$repo" == "$candidate" ]]; then
      permitted=true
      break
    fi
  done
  if [[ "$permitted" != true ]]; then
    echo "Refusing unknown ring repository: $repo" >&2
    exit 1
  fi

  existing_ids="$(
    gh api "repos/$repo/rulesets" \
      --jq '.[] | select(.name == "Ring Pointer History") | .id'
  )"
  count="$(printf '%s\n' "$existing_ids" | grep -c '[0-9]' || true)"
  if [[ "$count" -gt 1 ]]; then
    echo "Refusing to update duplicate Ring Pointer History rulesets in $repo." >&2
    exit 1
  fi
  existing_id="$(printf '%s\n' "$existing_ids" | head -n 1)"
  if [[ -n "$existing_id" ]]; then
    gh api "repos/$repo/rulesets/$existing_id" --method PUT --input "$ruleset"
  else
    gh api "repos/$repo/rulesets" --method POST --input "$ruleset"
  fi
done
