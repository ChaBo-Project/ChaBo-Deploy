#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/security/scan-dockerfile.sh PATH" >&2
  exit 2
fi

dockerfile=$1
if [[ ! -f "$dockerfile" || ! -s "$dockerfile" ]]; then
  echo "Dockerfile is missing or empty: $dockerfile" >&2
  exit 2
fi

work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT

# Give every input a standard name so the same check covers rendered templates.
cp -- "$dockerfile" "$work_dir/Dockerfile"

trivy config \
  --include-non-failures \
  --format json \
  --output "$work_dir/report.json" \
  "$work_dir"

python3 - "$work_dir/report.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as report:
    results = json.load(report).get("Results") or []

checks = [
    check
    for result in results
    if result.get("Type") == "dockerfile"
    for check in (result.get("Misconfigurations") or [])
]
if not any(check.get("Status") in {"PASS", "FAIL"} for check in checks):
    sys.exit("Scan incomplete: no Dockerfile policy checks were evaluated.")

for check in checks:
    if check.get("Status") == "FAIL" and check.get("Severity") != "CRITICAL":
        print(f"Finding: {check['Severity']} {check['ID']}")

critical = sorted({
    check["ID"] for check in checks
    if check.get("Status") == "FAIL" and check.get("Severity") == "CRITICAL"
})
if critical:
    sys.exit("Critical Dockerfile checks failed: " + ", ".join(critical))

print("PASS: Dockerfile policies evaluated; no critical misconfigurations found.")
PY
