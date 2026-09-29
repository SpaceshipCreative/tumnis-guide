#!/usr/bin/env bash
# Require every CI job on main (P0-03). Run once, and again whenever
# .github/required-checks.txt changes, with a gh login that has admin on the repo:
#
#   scripts/ci/branch_protection.sh [owner/repo]      (default: the current gh repo)
#
# Branch protection on a private repository needs GitHub Pro, Team or Enterprise; on
# GitHub Free the API answers 403 ("Upgrade to GitHub Pro or make this repository
# public"). REQUIRE_CODE_OWNER_REVIEWS=false drops the review rule (a sole maintainer
# cannot approve their own PR); ENFORCE_ADMINS=false lets admins bypass the checks.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
repo="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
branch="${BRANCH:-main}"
reviews="${REQUIRE_CODE_OWNER_REVIEWS:-true}"
admins="${ENFORCE_ADMINS:-true}"

contexts="$(grep -v '^[[:space:]]*#' "$root/.github/required-checks.txt" | sed '/^[[:space:]]*$/d' \
  | jq -R . | jq -s .)"
if [ "$(jq length <<<"$contexts")" -eq 0 ]; then
  echo "no checks in .github/required-checks.txt" >&2
  exit 1
fi

if [ "$reviews" = "true" ]; then
  review_rule='{"require_code_owner_reviews": true, "required_approving_review_count": 1, "dismiss_stale_reviews": true}'
else
  review_rule='null'
fi

body="$(jq -n \
  --argjson contexts "$contexts" \
  --argjson reviews "$review_rule" \
  --argjson admins "$admins" \
  '{
    required_status_checks: {strict: true, contexts: $contexts},
    enforce_admins: $admins,
    required_pull_request_reviews: $reviews,
    restrictions: null,
    required_linear_history: false,
    allow_force_pushes: false,
    allow_deletions: false
  }')"

echo "Protecting $repo:$branch with $(jq length <<<"$contexts") required checks"
gh api -X PUT "repos/$repo/branches/$branch/protection" \
  -H "Accept: application/vnd.github+json" --input - <<<"$body" >/dev/null
gh api "repos/$repo/branches/$branch/protection/required_status_checks" --jq '.contexts[]'
