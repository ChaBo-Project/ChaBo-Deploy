#!/usr/bin/env bash
# Renders one component's HF Space content into <out-dir>. This action's own source
# lives under hf-spaces/ at the ChaBo-Deploy repo root (three levels up from this script,
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

app_port=7860
# Space thumbnail gradient — one consistent ChaBo look across every rendered Space,
# not per-instance customizable (see hf-spaces/README.md's boilerplate section). Values
# must be from HF's fixed color set: red, yellow, green, blue, indigo, purple, pink, gray.
color_from=gray
color_to=gray

case "$component" in
  orchestrator)
    : "${image_tag:?image_tag is required for component=orchestrator}"
    sed "s|{{TAG}}|${image_tag}|g" "${action_root}/hf-spaces/orchestrator.Dockerfile" > "${out_dir}/Dockerfile"
    if [ -n "$extra_content_path" ]; then
      cp -r "${GITHUB_WORKSPACE}/${extra_content_path}" "${out_dir}/instance_config"
    fi
    ;;
  qdrant)
    cp -r "${action_root}/hf-spaces/qdrant/." "${out_dir}/"
    ;;
  chatui)
    # cp only the runtime files — hf-spaces/chatui/env.local.template is a reference for
    # setting the DOTENV_LOCAL Space secret by hand, not something to push into the Space.
    # image_tag is optional here (unlike orchestrator, where it's required) — defaults to
    # the last-known-good hf-chat-ui tag so an instance can stay on the default without
    # having to pass anything.
    chatui_tag="${image_tag:-0.9.4-chabo}"
    sed "s|{{TAG}}|${chatui_tag}|g" "${action_root}/hf-spaces/chatui/Dockerfile" > "${out_dir}/Dockerfile"
    cp "${action_root}/hf-spaces/chatui/custom_startup.sh" "${out_dir}/custom_startup.sh"
    app_port=3000
    color_from=yellow
    ;;
  *)
    echo "Unknown component: $component (expected orchestrator, qdrant, or chatui)" >&2
    exit 1
    ;;
esac

sed "s|{{TITLE}}|${title}|; s|{{APP_PORT}}|${app_port}|; s|{{COLOR_FROM}}|${color_from}|; s|{{COLOR_TO}}|${color_to}|" \
  "${action_root}/hf-spaces/boilerplate/README.md.template" > "${out_dir}/README.md"
cp "${action_root}/hf-spaces/boilerplate/.gitattributes" "${out_dir}/.gitattributes"
