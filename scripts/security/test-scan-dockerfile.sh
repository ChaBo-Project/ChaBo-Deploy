#!/usr/bin/env bash
set -euo pipefail

scanner="$(dirname "$0")/scan-dockerfile.sh"
fixtures=$(mktemp -d)
trap 'rm -rf -- "$fixtures"' EXIT

# Check both the exit code and the reason. An unrelated error must fail the test.
expect_result() {
  local expected_code=$1 expected_text=$2 target=$3
  local actual_code=0 output
  output=$(bash "$scanner" "$target" 2>&1) || actual_code=$?
  if [[ $actual_code -ne $expected_code || "$output" != *"$expected_text"* ]]; then
    printf 'FAIL: expected exit %s and "%s"; got exit %s\n%s\n' \
      "$expected_code" "$expected_text" "$actual_code" "$output" >&2
    return 1
  fi
  printf 'PASS: exit %s, found "%s"\n' "$actual_code" "$expected_text"
}

printf '%s\n' 'FROM scratch' 'USER 65532:65532' 'CMD ["/app"]' > "$fixtures/safe.Dockerfile"
printf '%s\n' 'FROM alpine:3.20' 'USER root' > "$fixtures/root.Dockerfile"

expect_result 2 'Dockerfile is missing or empty' "$fixtures/missing.Dockerfile"
expect_result 0 'no critical misconfigurations found' "$fixtures/safe.Dockerfile"
expect_result 0 'Finding: HIGH DS-0002' "$fixtures/root.Dockerfile"

# Real Trivy ran above. Use a controlled report to test the CRITICAL threshold.
mkdir "$fixtures/mock-bin"
cat > "$fixtures/mock-bin/trivy" <<'MOCK'
#!/usr/bin/env bash
set -eu
while [[ $# -gt 0 ]]; do
  if [[ $1 == --output ]]; then
    printf '%s\n' '{"Results":[{"Type":"dockerfile","Misconfigurations":[{"ID":"TEST-CRITICAL","Status":"FAIL","Severity":"CRITICAL"}]}]}' > "$2"
    exit 0
  fi
  shift
done
exit 2
MOCK
chmod +x "$fixtures/mock-bin/trivy"
PATH="$fixtures/mock-bin:$PATH" expect_result 1 'Critical Dockerfile checks failed: TEST-CRITICAL' "$fixtures/safe.Dockerfile"
