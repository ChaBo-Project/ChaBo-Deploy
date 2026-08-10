#!/usr/bin/env bash
# Pushes rendered content to an HF Space: clone fresh, wipe non-.git content, copy the
# render dir in, commit, plain (non-force) push. A diverged HF-Space-side history (e.g.
# someone edited via the HF UI) fails loudly here rather than being silently overwritten.
set -euo pipefail

hf_space="${1:?Usage: push.sh <org/space-name> <render-dir>}"
render_dir="${2:?}"

: "${HF_TOKEN:?HF_TOKEN not set}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "==> Pushing to https://huggingface.co/spaces/${hf_space}"
git clone "https://user:${HF_TOKEN}@huggingface.co/spaces/${hf_space}" "${work}/repo"
find "${work}/repo" -mindepth 1 -maxdepth 1 -not -name '.git' -exec rm -rf {} +
cp -r "${render_dir}"/. "${work}/repo"/

cd "${work}/repo"
git add -A
if git diff --cached --quiet; then
  echo "    no changes, skipping push"
else
  git -c user.name="chabo-deploy-bot" -c user.email="deploy@chabo-project.org" \
    commit -m "deploy: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git push
fi
