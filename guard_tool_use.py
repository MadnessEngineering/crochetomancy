#!/usr/bin/env python3
"""
Guard rails for Bash tool calls — the three mechanical mistakes that cost us most.

Measured against 92 sessions (insights report 2026-09-05):

  1. `git add -A` / `git add .` swallowed uncommitted, unrelated work into two
     commits and needed a rebase to split back out. Stage explicit paths.
  2. Verification piped through `tail`/`head`/`grep` masks the exit code. `tsc`
     and `npm run check` were reported "clean" twice this way; one false claim
     reached a commit message. Run bare, or `set -o pipefail` first.
  3. `gcloud` / `bq` with an expired token fail in ways that read as success
     downstream. Deploys are already protected (deploy-gce.sh caches the VM IP);
     this covers every other gcloud/bq call.

Contract (matches the rest of this hooks directory):
  - exit 0  → allow. ANY internal error also exits 0: a broken guard must never
              block the prompt (see README, "Hooks exit 0 on any internal error").
  - exit 2  → block. stderr is what the agent reads, so it names the rule and
              the fix.
  - Each rule has a flag in ~/.claude/hooks_config.json (default on) so a rule
    that misfires can be switched off without editing code.

Pure stdlib, no Mongo/MQTT import — this runs on every Bash call and must stay
cheap. Only the gcloud rule shells out, and only when the command mentions
gcloud or bq.

Tests: tests/guard_matrix.sh (block/allow both directions, prose cases).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HOOKS_CFG = Path.home() / ".claude" / "hooks_config.json"


def _cfg(key: str, default: bool = True) -> bool:
    try:
        return json.loads(_HOOKS_CFG.read_text()).get(key, default) if _HOOKS_CFG.exists() else default
    except Exception:
        return default


# Prose is not a command. A commit message written through a heredoc, or passed
# with -m, can legitimately SAY "gcloud" or "git add -A" — the first version of
# this guard blocked its own commit for exactly that reason. Strip heredoc bodies
# and -m/--message payloads before matching; the command structure around them
# survives, so `bq query "select 1"` and `bash -c "git add -A"` still match.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)", re.S)
_MSG_ARG = re.compile(r"(?:\s-m|\s--message)(?:=|\s+)(?:\"(?:[^\"\\]|\\.)*\"|'[^']*')")


def _strip_prose(cmd: str) -> str:
    cmd = _HEREDOC.sub(" ", cmd)
    return _MSG_ARG.sub(" -m ''", cmd)


def _block(rule: str, why: str, fix: str) -> None:
    sys.stderr.write(f"[guard:{rule}] BLOCKED — {why}\n  → {fix}\n")
    sys.stderr.flush()
    sys.exit(2)


# ── rule 1: broad git add ──────────────────────────────────────────────────
# `git add -A`, `git add --all`, `git add .` (bare dot as a path — `.gitignore`
# is fine, hence the trailing boundary). `-u` is allowed: tracked files only.
_GIT_ADD_ALL = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?add\b(?:\s+-\w+)*\s+(?:-A|--all|\.)(?=\s|;|&|\||$)"
)

# ── rule 2: verification with its exit code masked ─────────────────────────
# A verification command followed, later in the same pipeline, by a filter that
# replaces its exit status with its own. `set -o pipefail` or a PIPESTATUS read
# anywhere in the command is the honest way to keep the filter, so either lifts
# the block.
_VERIFY = re.compile(
    r"(?:\btsc\b|\bnpm\s+run\s+(?:check|test(?::\w+)?|lint|typecheck)\b|\bnpm\s+test\b"
    r"|\b(?:npx\s+)?vitest\b|\bpytest\b|\bnode\s+--test\b|\btsx\s+--test\b|\bjest\b"
    r"|\bcargo\s+(?:test|check)\b|\bgo\s+(?:test|vet)\b)"
)
_MASKING_FILTER = re.compile(r"\|\s*(?:tail|head|grep|wc|cut|awk|sed)\b")
_HONEST = re.compile(r"pipefail|PIPESTATUS")

# ── rule 3: gcloud / bq with a stale token ─────────────────────────────────
_GCLOUD = re.compile(r"(?<![\w/.-])(?:gcloud|bq)\s+(?!auth\b)")


def _gcloud_token_ok() -> bool:
    """True if gcloud can mint a token right now. Unknown → True (fail open)."""
    try:
        r = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        return r.returncode == 0
    except FileNotFoundError:
        return True  # no gcloud here at all; the command will say so itself
    except Exception:
        return True


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = str((data.get("tool_input") or {}).get("command") or "")
    if not cmd.strip():
        return
    cmd = _strip_prose(cmd)

    if _cfg("guard_git_add_all") and _GIT_ADD_ALL.search(cmd):
        _block(
            "git-add-all",
            "`git add -A` / `git add .` stages whatever else is dirty in the tree — it has "
            "swallowed unrelated in-progress work before.",
            "run `git status --short`, then `git add <explicit paths>`.",
        )

    if _cfg("guard_piped_verification"):
        m = _VERIFY.search(cmd)
        if m and _MASKING_FILTER.search(cmd, m.end()) and not _HONEST.search(cmd):
            _block(
                "piped-verification",
                f"`{m.group(0)}` is piped into a filter, so the exit code you see is the "
                "filter's, not the check's. This produced two false 'clean' reports.",
                "run it bare and read the exit code, or prefix `set -o pipefail;` to keep the filter honestly.",
            )

    if _cfg("guard_gcloud_preflight") and _GCLOUD.search(cmd) and not _gcloud_token_ok():
        _block(
            "gcloud-preflight",
            "gcloud cannot mint an access token — the credential is expired. This fails "
            "silently downstream more often than it fails loudly.",
            "ask the user to run `gcloud auth login` (it is interactive), then retry.",
        )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass  # fail open, by contract
    sys.exit(0)
