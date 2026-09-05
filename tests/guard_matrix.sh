#!/usr/bin/env bash
# Behavioural matrix for guard_tool_use.py. Feeds each command to the hook as
# the PreToolUse JSON Claude Code sends and checks the exit code: 0 = allow,
# 2 = block. Lives in a FILE on purpose — a Bash command whose text contains
# `git add -A` is itself refused by the live guard, which is how the guard once
# blocked the very command that was trying to test it.
#
# The gcloud rule depends on the real token on this machine, so the two live
# probes at the end report rather than assert.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/../guard_tool_use.py"
fail=0; n=0

run() {  # run <expected-exit> <command...>
  local expect="$1"; shift; local cmd="$*"
  local payload; payload=$(python3 -c 'import json,sys;print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$cmd")
  local err; err=$(printf '%s' "$payload" | python3 "$GUARD" 2>&1 >/dev/null); local code=$?
  n=$((n+1))
  if [ "$code" = "$expect" ]; then
    printf 'ok   exit=%s  %s\n' "$code" "$(printf '%s' "$cmd" | head -1 | cut -c1-72)"
  else
    fail=$((fail+1))
    printf 'FAIL exit=%s want=%s  %s\n     %s\n' "$code" "$expect" "$(printf '%s' "$cmd" | head -1 | cut -c1-72)" "$(printf '%s' "$err" | head -1 | cut -c1-90)"
  fi
}

echo "── rule 1: broad git add ──"
run 2 'git add -A'
run 2 'git add .'
run 2 'git add --all && git commit -m x'
run 2 'git -C ElementalAlloy add -A'
run 2 'bash -c "git add -A && git commit -m x"'          # quoted COMMAND is still a command
run 0 'git add .gitignore'
run 0 'git add -u'
run 0 'git add server/agent/nlq.ts scripts/x.sh'

echo "── rule 2: verification with a masked exit code ──"
run 2 'npm run check 2>&1 | tail -5'
run 2 'npm run check 2>&1 | grep -c "error TS"'
run 2 'npx vitest run server/x.test.ts 2>&1 | tail -12'
run 2 'npx tsx --test server/tests/a.test.ts | head -20'
run 2 'pytest -q | tail -3'
run 0 'set -o pipefail; npm run check 2>&1 | tail -5'
run 0 'npm run check; echo exit=$?'
run 0 'npm run build 2>&1 | grep stamping'               # build is not a verification
run 0 'tail -f /tmp/run-suite.log'
run 0 'git log --oneline | head -3'

echo "── prose is not a command ──"
run 0 "$(printf 'git commit -q -F - <<'"'"'MSG'"'"'\nTalks about gcloud / bq, git add -A, and tsc | tail in prose.\nMSG\necho done')"
run 0 'git commit -m "mention gcloud compute and git add -A in a message"'
run 0 "git commit -m 'npm run check | tail was the bug'"
run 0 "$(printf 'cat <<EOF > notes.md\nrun npm test | head to see it\nEOF')"

echo "── contract: non-Bash and garbage pass through ──"
for payload in '{"tool_name":"Read","tool_input":{"file_path":"x"}}' 'not json' ''; do
  n=$((n+1)); printf '%s' "$payload" | python3 "$GUARD" >/dev/null 2>&1; code=$?
  if [ "$code" = 0 ]; then printf 'ok   exit=0  passthrough: %s\n' "${payload:-<empty>}"; else fail=$((fail+1)); printf 'FAIL exit=%s passthrough: %s\n' "$code" "${payload:-<empty>}"; fi
done

echo "── rule 3: gcloud (reports against the REAL token; exempt form must pass) ──"
run 0 'gcloud auth login'
for c in 'gcloud compute instances list --project x' 'bq query --dry_run "select 1"'; do
  payload=$(python3 -c 'import json,sys;print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$c")
  printf '%s' "$payload" | python3 "$GUARD" >/dev/null 2>&1; code=$?
  printf 'info exit=%s  %s\n' "$code" "$c"
done
gcloud auth print-access-token >/dev/null 2>&1 && echo "info token: VALID (expect exit=0 above)" || echo "info token: STALE (expect exit=2 above)"

echo
if [ "$fail" = 0 ]; then echo "MATRIX PASS ($n cases)"; else echo "MATRIX FAIL ($fail of $n)"; exit 1; fi
