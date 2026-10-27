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
# masking forms the regex version missed or only half-caught
run 2 'npx tsc --noEmit | tee tsc.log'
run 2 'n=$(npm test 2>&1 | grep -c FAIL); echo $n'
run 2 'bash -c "pytest -q | tail -3"'
run 2 'CI=true npx react-scripts test --watchAll=false 2>&1 | tail -20'
run 2 'uv run -q pytest tests/ | tail -5'
run 2 '.venv/bin/python -m pytest -q | grep passed'
run 2 'pnpm lint:fix | tail -3'
run 2 "$(printf 'git commit -q -F - <<'"'"'EOF'"'"' && npm test | tail -3\nmsg body\nEOF')"   # heredoc keeps its opening line

echo "── piped-verification false positives (git archaeology 2026-10-21, 36 of 36 were these) ──"
run 0 'npx tsc --noEmit -p . > $S/tsc17.txt 2>&1; echo tsc_exit=$?; git status --short | grep -v claude'
run 0 'npm test > /tmp/repro.txt 2>&1; echo "EXIT=$?"; grep -E "not ok [0-9]" /tmp/repro.txt | head'
run 0 "grep -n -e '\"lint' -e '\"tsc' -e '\"test' package.json | head; ls scripts | head -30"
run 0 'cat jest.config.* | head -40; git ls-files | grep -E "\.test\.tsx?$"'
run 0 "git grep -ln \"jest.mock('three'\" -- src | head"
run 0 'ls vitest*.config.* 2>/dev/null; grep -nE "setupFiles|env" vitest*.config.* | head'
run 0 'cat $S/tsc.txt; git stash list | head -2'
run 0 'pytest -q 2>&1 | grep -E "passed|failed"; echo "exit=${pipestatus[1]}"'   # zsh spelling
run 0 '(time npx jest > $S/base.log 2>&1; echo "jest exit=$?") 2>&1 | tail -6'
run 0 'npx tsc --version | head -1'
run 0 'npx jest --listTests | wc -l'
run 0 "$(printf '# 1) run the suite (read-only git show)\nnpx jest x.test.ts > $SP/out.txt 2>&1; echo exit=$?\nls server/zz-* 2>&1 | head -2')"

echo "── git-add-all: fresh repos created by the same command ──"
run 0 'cd $SP && rm -rf lit && mkdir lit && cd lit && git init -q && echo a > f && git add . && git commit -qm x'
run 0 'git clone -q https://example.com/r.git $P; cd $P && git add -A && git status --short | wc -l'
run 2 'git add -A && git init'                                   # init AFTER the add exempts nothing
run 2 'git -c user.name=t add -A'
run 2 'git add -Av'
run 2 'git add :/'
run 0 'git add -- src/a.ts'
run 0 'git add --dry-run -A >/dev/null 2>&1; echo "dry-run exit=$?"'   # lists, stages nothing
run 0 'git add -nA'

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
