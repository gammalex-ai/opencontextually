#!/usr/bin/env bash
# Apply branch protection to main.
#
# This cannot run while the repository is private: on this plan the
# protection and rulesets APIs return 403 ("Upgrade to GitHub Pro or make
# this repository public"). So it is a script rather than a settings change
# already made -- run it in the same sitting as flipping the repository
# public, because between those two moments main is unprotected.
#
#     ./.github/protect-main.sh
#
# Idempotent: re-running it just re-applies the same settings.
set -euo pipefail

REPO="${1:-gammalex-ai/opencontextually}"

echo "Protecting main on $REPO"

# Required checks are the four matrix legs by their job names. If the
# Python matrix in .github/workflows/test.yml changes, this list changes
# with it, or merges will block on a check that no longer runs.
gh api -X PUT "repos/$REPO/branches/main/protection" --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["test (3.10)", "test (3.11)", "test (3.12)", "test (3.13)"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true,
  "block_creations": false,
  "lock_branch": false,
  "allow_fork_syncing": true
}
JSON

# A fork's first pull request should not run workflows until someone has
# looked at the diff. Private repositories reject this call.
gh api -X PUT "repos/$REPO/actions/permissions/fork-pr-contributor-approval" \
  -f approval_policy=first_time_contributors || \
  echo "note: fork PR approval policy not applied (repo may still be private)"

echo
echo "Applied. Verify:  gh api repos/$REPO/branches/main/protection --jq 'keys'"
echo
echo "Two things this deliberately does NOT do:"
echo "  * enforce_admins is false -- a solo maintainer needs a way to fix"
echo "    main when CI itself is broken. Turn it on once there are two"
echo "    people who can review."
echo "  * required_approving_review_count is 1, which blocks self-merge on"
echo "    a solo repo. If you are still the only maintainer, either set it"
echo "    to 0 (checks still required, review not) or keep it and merge"
echo "    with admin override."
