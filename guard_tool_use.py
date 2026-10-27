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

Rules 1 and 2 match COMMAND POSITION, not text. The first version matched
regexes over the whole command line, and the git archaeology probe (2026-10-21)
found it was our biggest source of agent errors: 36 of 54 blocked calls were
false positives — `tsc17.txt` and `jest.config.*` read as a run of tsc/jest,
and `npx tsc > f 2>&1; echo $?; git status | grep x` read as "tsc piped into
grep" because the filter was searched for anywhere after the match, across
`;`, `&&` and newlines. Now the command is split into pipelines and stages
outside quotes, and a stage counts only by its command word.

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

Tests: tests/guard_matrix.sh (block/allow both directions, prose cases, and the
archaeology false positives replayed).
"""
import json
import os
import re
import shlex
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
# and -m/--message payloads before matching. The rest of the heredoc's opening
# line survives (`git commit -F - <<'EOF' && git push` keeps the push), so
# `bq query "select 1"` and `bash -c "git add -A"` still match.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n.*?\n\s*\2\s*(?=\n|$)", re.S)
_MSG_ARG = re.compile(r"(?:\s-m|\s--message)(?:=|\s+)(?:\"(?:[^\"\\]|\\.)*\"|'[^']*')")


def _strip_prose(cmd: str) -> str:
    cmd = _HEREDOC.sub(lambda m: m.group(3) + "\n", cmd)
    return _MSG_ARG.sub(" -m ''", cmd)


def _block(rule: str, why: str, fix: str) -> None:
    sys.stderr.write(f"[guard:{rule}] BLOCKED — {why}\n  → {fix}\n")
    sys.stderr.flush()
    sys.exit(2)


# ── shell structure ────────────────────────────────────────────────────────
# Not a shell parser — a splitter good enough to tell a command word from an
# argument. Splits at ; && || & and newlines (pipelines) and at | (stages),
# never inside quotes, backticks or $(...). The inside of each $(...) is
# returned as a command of its own, so `X=$(tsc | wc -l)` is still seen.
# Bare ( ) { } are dropped: `(npm test > log; echo $?) | tail` splits at the
# `;`, which is the right answer — the pipe carries echo's status, not npm's.

def _split(cmd: str):
    """→ (pipelines: list[list[str]], nested: list[str])"""
    pipelines, stages, buf, nested = [], [], [], []
    i, n = 0, len(cmd)

    def end_stage():
        stages.append("".join(buf).strip())
        buf.clear()

    def end_pipeline():
        end_stage()
        kept = [s for s in stages if s]
        if kept:
            pipelines.append(kept)
        stages.clear()

    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n:
            buf.append(cmd[i:i + 2]); i += 2; continue
        if c == "'":
            j = cmd.find("'", i + 1); j = n - 1 if j < 0 else j
            buf.append(cmd[i:j + 1]); i = j + 1; continue
        if c == '"' or c == "`":
            # Copy the quoted span; harvest any $(...) inside double quotes.
            j = i + 1
            while j < n and cmd[j] != c:
                if cmd[j] == "\\":
                    j += 2; continue
                if c == '"' and cmd.startswith("$(", j):
                    k = _close_paren(cmd, j + 2)
                    nested.append(cmd[j + 2:k]); j = k + 1; continue
                j += 1
            if c == "`":
                nested.append(cmd[i + 1:j])
            buf.append(cmd[i:j + 1]); i = j + 1; continue
        if cmd.startswith("$(", i):
            k = _close_paren(cmd, i + 2)
            nested.append(cmd[i + 2:k])
            buf.append(cmd[i:k + 1]); i = k + 1; continue
        if c == "#" and (not buf or buf[-1].isspace()):
            j = cmd.find("\n", i); i = n if j < 0 else j; continue
        if c in "\n;":
            end_pipeline(); i += 1; continue
        if c == "&":
            if cmd.startswith("&&", i):
                end_pipeline(); i += 2; continue
            if (buf and buf[-1] in "<>") or cmd.startswith("&>", i):
                buf.append(c); i += 1; continue  # 2>&1, &>file
            end_pipeline(); i += 1; continue  # background
        if c == "|":
            if cmd.startswith("||", i):
                end_pipeline(); i += 2; continue
            end_stage(); i += 2 if cmd.startswith("|&", i) else 1; continue
        if c in "(){}" and not (buf and not buf[-1].isspace() and c in "{}"):
            buf.append(" "); i += 1; continue  # grouping; ${var} braces survive
        buf.append(c); i += 1
    end_pipeline()
    return pipelines, nested


def _close_paren(s: str, i: int) -> int:
    depth = 1
    while i < len(s):
        if s[i] == "\\":
            i += 2; continue
        if s[i] == "'":
            j = s.find("'", i + 1); i = len(s) if j < 0 else j + 1; continue
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(s)


def _words(stage: str) -> list:
    try:
        return shlex.split(stage)
    except ValueError:
        return stage.split()


_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
# Words that run the NEXT word as the command. Values are how many following
# non-flag words are their own arguments (timeout 30 cmd → 1).
_WRAPPERS = {"time": 0, "env": 0, "exec": 0, "nice": 0, "command": 0, "sudo": 0,
             "nohup": 0, "npx": 0, "bunx": 0, "pnpx": 0, "timeout": 1, "gtimeout": 1}
_RUNNERS = {"uv": "run", "poetry": "run", "pipenv": "run", "pnpm": "exec", "yarn": "exec",
            "bundle": "exec", "dotenv": "--"}


def _argv(stage: str) -> list:
    """The stage's words with env assignments and wrappers peeled off, so
    argv[0] is the program that actually runs (basename only)."""
    w = _words(stage)
    i = 0
    while i < len(w):
        tok = w[i]
        if tok in ("!",) or _ASSIGN.match(tok):
            i += 1; continue
        base = os.path.basename(tok)
        if base in _WRAPPERS:
            i += 1
            while i < len(w) and (w[i].startswith("-") or _ASSIGN.match(w[i])):
                i += 1
            i += _WRAPPERS[base]; continue
        if base in _RUNNERS and i + 1 < len(w) and w[i + 1] == _RUNNERS[base]:
            i += 2
            while i < len(w) and w[i].startswith("-"):
                i += 1
            continue
        if re.fullmatch(r"python[\d.]*", base) and i + 2 < len(w) and w[i + 1] == "-m":
            i += 2; continue  # python -m pytest → pytest
        break
    argv = w[i:]
    if argv:
        argv[0] = os.path.basename(argv[0])
    return argv


def _commands(cmd: str, depth: int = 0):
    """Yield each pipeline as a list of argv lists, recursing into $(...),
    backticks and `bash -c "..."` / `eval "..."`."""
    if depth > 3:
        return
    pipelines, nested = _split(cmd)
    for pl in pipelines:
        argvs = [_argv(s) for s in pl]
        yield argvs
        for a in argvs:
            if a and a[0] in ("bash", "sh", "zsh", "dash") and "-c" in a[1:-1]:
                yield from _commands(a[a.index("-c") + 1], depth + 1)
            elif a and a[0] == "eval":
                yield from _commands(" ".join(a[1:]), depth + 1)
    for inner in nested:
        yield from _commands(inner, depth + 1)


# ── rule 1: broad git add ──────────────────────────────────────────────────
# `git add -A`, `--all`, `.`, `:/` (and -A in a flag cluster like -Av). `-u`
# is allowed: tracked files only. A tree the same command just created with
# `git init` / `git clone` has nothing unrelated in it to swallow, so it's
# exempt — scratch-repo repros were 3 of the 4 git-add-all blocks we measured.
_GIT_GLOBAL_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
_BROAD_PATHS = {".", "./", ":/", ":/:", ":(top)"}


def _git_sub(argv: list):
    """→ (subcommand, args) for a git argv, else (None, [])."""
    if not argv or argv[0] != "git":
        return None, []
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in _GIT_GLOBAL_WITH_ARG else 1
    return (argv[i], argv[i + 1:]) if i < len(argv) else (None, [])


def _broad_add(args: list) -> bool:
    if "--dry-run" in args or any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) for a in args):
        return False  # -n / --dry-run only lists what would be staged
    for a in args:
        if a in ("-A", "--all") or a in _BROAD_PATHS:
            return True
        if re.fullmatch(r"-[a-zA-Z]*A[a-zA-Z]*", a):
            return True
    return False


def _git_add_all(cmd: str) -> bool:
    fresh = False
    for pl in _commands(cmd):
        for argv in pl:
            sub, args = _git_sub(argv)
            if sub in ("init", "clone"):
                fresh = True
            elif sub == "add" and _broad_add(args) and not fresh:
                return True
    return False


# ── rule 2: verification with its exit code masked ─────────────────────────
# A verification stage that is not the last stage of its pipeline: the pipeline
# reports the last stage's status. Redirecting to a file (`tsc > f; echo $?`)
# keeps the status and is the pattern we WANT, so it passes. `set -o pipefail`
# or a PIPESTATUS / zsh pipestatus read anywhere lifts the block.
_HONEST = re.compile(r"pipefail|pipestatus", re.I)
_INFO_FLAGS = {"--version", "-v", "-V", "--help", "-h", "--showConfig", "--listTests",
               "--listFiles", "--collect-only", "--co", "--init"}
_PKG_VERIFY_SCRIPT = re.compile(r"(?:check|test|lint|typecheck|type-check)(?::[\w:-]+)?")


def _verification(argv: list):
    """→ display name if argv runs a check whose exit code is the verdict."""
    if not argv:
        return None
    p, rest = argv[0], argv[1:]
    if any(a in _INFO_FLAGS for a in rest):
        return None
    if p in ("tsc", "vue-tsc", "vitest", "jest", "pytest", "py.test"):
        return p
    if p == "react-scripts" and rest[:1] == ["test"]:
        return "react-scripts test"
    if p in ("npm", "pnpm", "yarn", "bun"):
        args = [a for a in rest if not a.startswith("-")]
        if args[:1] in (["test"], ["t"]):
            return f"{p} test"
        if args[:1] == ["run"] and len(args) > 1 and _PKG_VERIFY_SCRIPT.fullmatch(args[1]):
            return f"{p} run {args[1]}"
        if p in ("pnpm", "yarn", "bun") and args and _PKG_VERIFY_SCRIPT.fullmatch(args[0]):
            return f"{p} {args[0]}"
    if p in ("node", "tsx") and "--test" in rest:
        return f"{p} --test"
    if p == "cargo" and rest[:1] in (["test"], ["check"]):
        return f"cargo {rest[0]}"
    if p == "go" and rest[:1] in (["test"], ["vet"]):
        return f"go {rest[0]}"
    return None


def _piped_verification(cmd: str):
    if _HONEST.search(cmd):
        return None
    for pl in _commands(cmd):
        for argv in pl[:-1]:  # the last stage's status is the pipeline's own
            name = _verification(argv)
            if name:
                return name
    return None


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

    if _cfg("guard_git_add_all") and _git_add_all(cmd):
        _block(
            "git-add-all",
            "`git add -A` / `git add .` stages whatever else is dirty in the tree — it has "
            "swallowed unrelated in-progress work before.",
            "run `git status --short`, then `git add <explicit paths>`.",
        )

    if _cfg("guard_piped_verification"):
        name = _piped_verification(cmd)
        if name:
            _block(
                "piped-verification",
                f"`{name}` is piped into another command, so the exit code you see is that "
                "command's, not the check's. This produced two false 'clean' reports.",
                "run it bare and read the exit code, redirect to a file (`> out.txt 2>&1; echo $?`) "
                "and read the file separately, or prefix `set -o pipefail;` to keep the pipe honestly.",
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
