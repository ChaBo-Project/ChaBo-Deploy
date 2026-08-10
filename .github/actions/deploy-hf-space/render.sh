#!/usr/bin/env bash
# Renders one component's HF Space content into <out-dir>. This action's own templates/
# vendored source live at the ChaBo-Deploy repo root (three levels up from this script,
# .github/actions/deploy-hf-space/) — GITHUB_ACTION_PATH points here, not at the CALLING
# repo, which is what's actually checked out as cwd when a composite-action step runs.
set -euo pipefail

component="${1:?Usage: render.sh <component> <out-dir> <image-tag> <title> [extra-content-path]}"
out_dir="${2:?}"
image_tag="${3:-}"
title="${4:?}"
extra_content_path="${5:-}"

action_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
mkdir -p "$out_dir"

case "$component" in
  orchestrator)
    : "${image_tag:?image_tag is required for component=orchestrator}"
    sed "s|{{TAG}}|${image_tag}|g" "${action_root}/templates/orchestrator.Dockerfile" > "${out_dir}/Dockerfile"
    if [ -n "$extra_content_path" ]; then
      cp -r "${GITHUB_WORKSPACE}/${extra_content_path}" "${out_dir}/instance_config"
    fi
    ;;
  qdrant)
    cp -r "${action_root}/vendored/qdrant/." "${out_dir}/"
    ;;
  *)
    echo "Unknown component: $component (expected orchestrator or qdrant)" >&2
    exit 1
    ;;
esac

sed "s|{{TITLE}}|${title}|; s|{{APP_PORT}}|7860|" \
  "${action_root}/packaging/hf-spaces/boilerplate/README.md.template" > "${out_dir}/README.md"
cp "${action_root}/packaging/hf-spaces/boilerplate/.gitattributes" "${out_dir}/.gitattributes"
