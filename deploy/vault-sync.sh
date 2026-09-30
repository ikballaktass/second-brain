#!/usr/bin/env bash
# Sync the Obsidian vault with its git remote: commit local changes, rebase onto the
# remote, push. Safe by design: never forces, never resets. On a conflict the rebase
# is aborted, the local commit is kept, and the script exits 1 so the failure shows
# up in `systemctl status vault-sync` / journalctl until a human resolves it.
#
# Usage: vault-sync.sh [VAULT_DIR]   (default: $VAULT_PATH)
# Env:   VAULT_SYNC_REMOTE (default origin), VAULT_SYNC_BRANCH (default: current branch)
set -euo pipefail

vault="${1:-${VAULT_PATH:-}}"
if [[ -z "$vault" ]]; then
  echo "vault-sync: no vault given (argument or VAULT_PATH)" >&2
  exit 2
fi
cd "$vault"

if [[ ! -d .git ]]; then
  echo "vault-sync: $vault is not a git repository" >&2
  exit 2
fi
if [[ -d .git/rebase-merge || -d .git/rebase-apply || -f .git/MERGE_HEAD ]]; then
  echo "vault-sync: a rebase/merge is already in progress in $vault; resolve it by hand" >&2
  exit 2
fi

remote="${VAULT_SYNC_REMOTE:-origin}"
branch="${VAULT_SYNC_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"

git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "vault: sync from $(hostname) $(date -Iseconds)"
fi

if ! git pull -q --rebase "$remote" "$branch"; then
  git rebase --abort 2>/dev/null || true
  echo "vault-sync: conflict with $remote/$branch; local commits kept, resolve by hand" >&2
  exit 1
fi

git push -q "$remote" "HEAD:$branch"
echo "vault-sync: ok ($(git rev-parse --short HEAD))"
