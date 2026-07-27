#!/usr/bin/env python3
"""Crochetomancy installer — wire this hook clone into Claude Code.

Installs *integration groups* (core, mongo, mqtt, omnispindle, tinker,
titancall) rather than tiers: each group pairs the settings.json event wiring
it needs with the hooks_config.json feature flags it owns, so "MQTT but no
Mongo" is a thing you can ask for.

What it touches, and nothing else:

  ~/.claude/settings.json        append-only hook entries (deduped by exact
                                 command string; your own entries are never
                                 read, rewritten or reordered)
  ~/.claude/hooks_config.json    the feature flags the selected groups own
                                 (unknown keys preserved)
  ~/.claude/.madness-loadout.json  sidecar recording exactly what we added, so
                                 `uninstall` removes precisely that

Group command strings are byte-identical to their loadout-manifest.json tier
counterparts, which is what lets this coexist with the madnessDesktop (madhub)
installer: install both and dedupe collapses them, uninstall from either leaves
the other's wiring intact.

Stdlib only, python3.8+. No network, no cloning — this file ships inside the
clone, so the clone is already where it needs to be.

    python3 install.py                        # interactive picker
    python3 install.py install --groups mqtt,omnispindle --yes
    python3 install.py status
    python3 install.py uninstall

Testing against a scratch home (never your live config):

    python3 install.py --home /tmp/fakehome install --groups core --yes
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from shutil import which

MANIFEST_FILE = "loadout-manifest.json"
SIDECAR_FILE = ".madness-loadout.json"
HOOKS_CONFIG_FILE = "hooks_config.json"
SETTINGS_FILE = "settings.json"
DEFAULT_PATH_TOKEN = "{{HOOKS_DIR}}"
INSTALLER_ID = "install.py"

OK = "✓"      # ✓
NO = "✗"      # ✗
DOT = "·"     # ·


class InstallError(Exception):
    """Anything the user can fix: bad group name, unparseable settings.json."""


# ── paths ──────────────────────────────────────────────────────────────────

def resolve_paths(home, hooks_dir=None):
    """Layout of everything we read or write, given a home directory.

    hooks_dir defaults to this file's directory — we *are* the clone.
    """
    home = Path(home).expanduser()
    claude_dir = home / ".claude"
    return {
        "home": home,
        "claude_dir": claude_dir,
        "settings": claude_dir / SETTINGS_FILE,
        "sidecar": claude_dir / SIDECAR_FILE,
        "hooks_config": claude_dir / HOOKS_CONFIG_FILE,
        "hooks_dir": Path(hooks_dir).expanduser().resolve()
        if hooks_dir
        else Path(__file__).resolve().parent,
    }


def read_json(path, default=None, label=None):
    """Parse a JSON file. Missing file → default. Bad JSON → InstallError."""
    if not path.exists():
        return default
    try:
        text = path.read_text()
    except OSError as e:
        raise InstallError("cannot read %s: %s" % (path, e))
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except ValueError as e:
        raise InstallError("%s is not valid JSON: %s" % (label or path, e))


def write_json(path, obj):
    """Atomic write, 2-space indent + trailing newline (matches the TS installer)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    os.replace(str(tmp), str(path))


# ── manifest ───────────────────────────────────────────────────────────────

def load_manifest(hooks_dir):
    path = Path(hooks_dir) / MANIFEST_FILE
    if not path.exists():
        raise InstallError(
            "no %s in %s — run this from inside the hooks clone, or pass "
            "--hooks-dir" % (MANIFEST_FILE, hooks_dir)
        )
    manifest = read_json(path, label=MANIFEST_FILE)
    if not isinstance(manifest, dict) or "groups" not in manifest:
        raise InstallError(
            "%s has no `groups` section — this clone predates the group "
            "installer; git pull and retry" % MANIFEST_FILE
        )
    return manifest


def groups_of(manifest):
    return manifest.get("groups") or {}


def public_groups(manifest):
    """Selectable groups, in manifest declaration order (hidden fragments out)."""
    return [(n, g) for n, g in groups_of(manifest).items() if not g.get("hidden")]


def required_groups(manifest):
    return [n for n, g in groups_of(manifest).items() if g.get("required")]


def all_managed_flags(manifest):
    """Every flag any group claims — the set we take responsibility for writing."""
    flags = []
    for g in groups_of(manifest).values():
        for f in g.get("flags") or []:
            if f not in flags:
                flags.append(f)
    return flags


def resolve_selection(manifest, names, include_required=True):
    """Validate names, force-add required groups, expand `includes`.

    Returns (selected, effective): what the user asked for (plus required), and
    that closed over includes (hidden fragments now visible). Pass
    include_required=False to inspect one group in isolation (status display).
    """
    catalog = groups_of(manifest)
    selected = []
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        if name not in catalog:
            raise InstallError(
                "unknown group %r — available: %s"
                % (name, ", ".join(n for n, _ in public_groups(manifest)))
            )
        if catalog[name].get("hidden"):
            raise InstallError(
                "%r is an internal fragment, not installable on its own "
                "(it comes in with the groups that use it)" % name
            )
        if name not in selected:
            selected.append(name)
    if include_required:
        for name in required_groups(manifest):
            if name not in selected:
                selected.insert(0, name)

    effective = []

    def walk(name, trail):
        if name in trail:
            raise InstallError(
                "circular `includes` in manifest: %s" % " -> ".join(trail + [name])
            )
        if name in effective:
            return
        effective.append(name)
        for inc in catalog[name].get("includes") or []:
            if inc not in catalog:
                raise InstallError(
                    "group %r includes unknown group %r" % (name, inc)
                )
            walk(inc, trail + [name])

    # Manifest order, not user order, so expansion is stable across invocations.
    for name in catalog:
        if name in selected:
            walk(name, [])
    return selected, effective


def expand_groups(manifest, names, hooks_dir, include_required=True):
    """Selected group names → {event: [command, ...]} with the path token filled.

    Deduped inside the expansion; ordered by manifest declaration so two runs
    of the same selection produce the same wiring.
    """
    _, effective = resolve_selection(manifest, names, include_required)
    token = manifest.get("pathToken") or DEFAULT_PATH_TOKEN
    catalog = groups_of(manifest)
    events = {}
    seen = set()
    for name in effective:
        for event, commands in (catalog[name].get("events") or {}).items():
            for command in commands:
                resolved = command.replace(token, str(hooks_dir))
                if resolved in seen:
                    continue
                seen.add(resolved)
                events.setdefault(event, []).append(resolved)
    return events


def compute_flags(manifest, names):
    """Deterministic flag state: managed key is True iff a selected group owns it.

    Every managed key is written explicitly — an absent key reads as True in the
    hooks (`_cfg` defaults on), so "not selected" has to mean an explicit False.
    """
    _, effective = resolve_selection(manifest, names)
    catalog = groups_of(manifest)
    wanted = set()
    for name in effective:
        wanted.update(catalog[name].get("flags") or [])
    return {flag: (flag in wanted) for flag in all_managed_flags(manifest)}


# ── settings.json ──────────────────────────────────────────────────────────

def existing_commands(hooks):
    """Every command string already wired, whoever wired it."""
    seen = set()
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks") or []:
                if isinstance(hook, dict) and "command" in hook:
                    seen.add(hook["command"])
    return seen


def script_name(command):
    """The hook script a command invokes, by basename, or None.

    Dedupe is by exact string, so `user_prompt_submit.py --log-only …` and the
    manifest's argument set are two different commands — and wiring both runs
    the hook twice. Basename matching is what lets us warn about that, and it
    sees through path differences too (~/.claude/hooks vs the real clone path).
    """
    for token in command.replace('"', " ").replace("'", " ").split():
        name = token.rsplit("/", 1)[-1]
        if name.endswith((".py", ".js", ".sh")):
            return name
    return None


def near_duplicates(expanded, wired):
    """(script, existing command) pairs where we'd add a second copy of a hook."""
    hits = []
    by_script = {}
    for command in wired:
        by_script.setdefault(script_name(command), []).append(command)
    for commands in expanded.values():
        for command in commands:
            if command in wired:
                continue
            name = script_name(command)
            if not name:
                continue
            for other in by_script.get(name, []):
                hits.append((name, other))
    return hits


def merge_settings(settings, expanded):
    """Append our commands, skipping any already present. Returns what was added.

    Append-only by construction: existing groups are read for dedupe and
    otherwise left exactly as they are.
    """
    hooks = settings.setdefault("hooks", {})
    seen = existing_commands(hooks)
    added = []
    for event, commands in expanded.items():
        groups = hooks.setdefault(event, [])
        for command in commands:
            if command in seen:
                continue
            groups.append({"hooks": [{"type": "command", "command": command}]})
            seen.add(command)
            added.append(command)
    return added


def remove_commands(settings, to_remove):
    """Drop hook groups containing any of `to_remove`. Returns commands removed."""
    hooks = settings.get("hooks") or {}
    removed = []
    for event in list(hooks.keys()):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            commands = [
                h.get("command")
                for h in (group.get("hooks") or [])
                if isinstance(h, dict)
            ] if isinstance(group, dict) else []
            hit = [c for c in commands if c in to_remove]
            if hit:
                removed.extend(hit)
            else:
                kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return removed


# ── sidecar ────────────────────────────────────────────────────────────────

def read_sidecar(paths):
    """Sidecar or None. Tolerates absent file, literal `null`, and junk."""
    try:
        data = read_json(paths["sidecar"], default=None, label=SIDECAR_FILE)
    except InstallError:
        return None
    return data if isinstance(data, dict) else None


def write_sidecar(paths, tier, commands, groups, installed_at):
    """Superset of the TS shape: `tier` preserved, `groups`/`installer` added.

    A madhub re-install rewrites this with its four keys only; that is fine —
    uninstall integrity lives in installedCommands, and status just degrades to
    reporting the tier.
    """
    record = {}
    if tier is not None:
        record["tier"] = tier
    record["installedCommands"] = commands
    record["hooksDir"] = str(paths["hooks_dir"])
    record["installedAt"] = installed_at
    record["groups"] = groups
    record["installer"] = INSTALLER_ID
    write_json(paths["sidecar"], record)
    return record


# ── deps ───────────────────────────────────────────────────────────────────

SERVICE_DEPS = {"mongo", "mqtt"}
IMPORT_DEPS = {"pymongo", "dvttestkit", "dotenv"}


def probe(name):
    """(present, note) for one dep name. Report-only; nothing here blocks."""
    if name in SERVICE_DEPS:
        return None, "runtime service — hooks no-op when unreachable"
    if name in IMPORT_DEPS:
        code = subprocess.run(
            [sys.executable, "-c", "import %s" % name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        return code == 0, "python package"
    return which(name) is not None, "binary on PATH"


def check_deps(manifest, names):
    """Probe hard + optional deps across the effective selection."""
    _, effective = resolve_selection(manifest, names)
    catalog = groups_of(manifest)
    hard, soft = [], []
    for name in effective:
        for dep in catalog[name].get("deps") or []:
            if dep not in hard:
                hard.append(dep)
        for dep in catalog[name].get("optional") or []:
            if dep not in soft:
                soft.append(dep)
    results = []
    for dep in hard:
        present, note = probe(dep)
        results.append((dep, present, False, note))
    for dep in soft:
        if dep in hard:
            continue
        present, note = probe(dep)
        results.append((dep, present, True, note))
    return results


# ── interactive picker ─────────────────────────────────────────────────────

def interactive_picker(manifest, current):
    """Numbered menu. Required groups are locked on; blank keeps the default."""
    entries = public_groups(manifest)
    locked = set(required_groups(manifest))
    preselected = [n for n, _ in entries if n in (current or []) and n not in locked]

    print("\n  Crochetomancy loadout — pick your integrations\n")
    for i, (name, group) in enumerate(entries, 1):
        if name in locked:
            mark, tag = OK, " (always installed)"
        elif name in preselected:
            mark, tag = OK, " (installed)"
        else:
            mark, tag = DOT, ""
        print("   %2d. %s %-12s %s%s" % (i, mark, name, group.get("label", name), tag))
        print("        %s" % group.get("summary", ""))
    print()

    default = preselected or []
    hint = ",".join(default) if default else "core only"
    try:
        raw = input(
            "  Numbers or names, comma-separated [%s]: " % hint
        ).strip()
    except EOFError:
        raw = ""
    if not raw:
        return default

    chosen = []
    for token in raw.replace(" ", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            idx = int(token)
            if not 1 <= idx <= len(entries):
                raise InstallError("no group numbered %s" % idx)
            token = entries[idx - 1][0]
        chosen.append(token)
    return chosen


def confirm(question):
    try:
        return input("  %s [y/N]: " % question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


# ── commands ───────────────────────────────────────────────────────────────

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def cmd_install(args):
    paths = resolve_paths(args.home, args.hooks_dir)
    manifest = load_manifest(paths["hooks_dir"])
    sidecar = read_sidecar(paths)
    # Tolerate a sidecar naming groups this clone no longer has (older install,
    # newer manifest) — the wiring is still recorded in installedCommands.
    prior_groups = [
        g for g in (sidecar or {}).get("groups") or []
        if isinstance(g, str) and g in dict(public_groups(manifest))
    ]

    if args.groups:
        requested = [g for chunk in args.groups for g in chunk.split(",")]
    else:
        requested = interactive_picker(manifest, prior_groups)

    # Install is additive (same as madhub's): a selection is added to whatever
    # is already wired, never differenced against it. To shrink, uninstall and
    # reinstall — a precise shrink can't be done safely while the sidecar's
    # command list is a union shared with the other installer.
    selected, effective = resolve_selection(manifest, list(prior_groups) + requested)
    expanded = expand_groups(manifest, selected, paths["hooks_dir"])
    flags = compute_flags(manifest, selected)

    # Parse settings.json before showing a plan or asking anything: a syntax
    # error there is the one failure we can't work around, and finding it after
    # the user has already confirmed is just rude.
    settings = read_json(paths["settings"], default={}, label=str(paths["settings"]))
    if not isinstance(settings, dict):
        raise InstallError("%s must contain a JSON object" % paths["settings"])

    print("\n  hooks dir : %s" % paths["hooks_dir"])
    print("  home      : %s" % paths["home"])
    print("  groups    : %s" % ", ".join(selected))
    hidden = [n for n in effective if n not in selected]
    if hidden:
        print("  includes  : %s" % ", ".join(hidden))
    print("  commands  : %d across %d events" % (
        sum(len(v) for v in expanded.values()), len(expanded)))

    deps = check_deps(manifest, selected)
    missing_hard = [d for d, present, optional, _ in deps if present is False and not optional]
    missing_soft = [d for d, present, optional, _ in deps if present is False and optional]
    if missing_hard:
        print("  %s missing : %s (hooks using them will not run)" % (NO, ", ".join(missing_hard)))
    if missing_soft:
        print("  %s optional: %s (those features stay dormant)" % (DOT, ", ".join(missing_soft)))

    dupes = near_duplicates(expanded, existing_commands(settings.get("hooks") or {}))
    if dupes:
        print("  %s already wired with different arguments — installing adds a "
              "second copy, and the hook will run twice:" % NO)
        for name, other in dupes:
            print("       %s  vs existing: %s" % (name, other))
        print("       (remove the existing entry from settings.json first if "
              "that isn't what you want)")

    # Flags are written deterministically: managed key off unless a selected
    # group owns it. Wiring that arrived some other way (a madhub tier, a
    # hand-rolled install) keeps running but goes quiet — say so before writing.
    prior_config = read_json(paths["hooks_config"], default={}, label=HOOKS_CONFIG_FILE)
    if isinstance(prior_config, dict):
        silenced = sorted(
            f for f, v in flags.items()
            if not v and prior_config.get(f) is True
        )
        if silenced:
            print("  %s turning off: %s" % (NO, ", ".join(silenced)))
            print("       (not owned by any selected group — add the group that "
                  "owns it, or edit hooks_config.json afterwards)")

    if not args.yes and not confirm("Write settings.json, hooks_config.json and the sidecar?"):
        print("  Aborted, nothing written.")
        return 1

    paths["claude_dir"].mkdir(parents=True, exist_ok=True)

    added = merge_settings(settings, expanded)
    write_json(paths["settings"], settings)

    config = read_json(paths["hooks_config"], default={}, label=HOOKS_CONFIG_FILE)
    if not isinstance(config, dict):
        config = {}
    config.update(flags)  # managed keys only — strangers ride along untouched
    write_json(paths["hooks_config"], config)

    commands = list((sidecar or {}).get("installedCommands") or [])
    for command in added:
        if command not in commands:
            commands.append(command)
    write_sidecar(paths, (sidecar or {}).get("tier"), commands, selected, now_iso())

    on = [f for f, v in flags.items() if v]
    print("\n  %s installed: %s" % (OK, ", ".join(selected)))
    print("  %s %d command(s) added, %d already wired" % (
        OK, len(added), sum(len(v) for v in expanded.values()) - len(added)))
    print("  %s flags on: %s" % (OK, ", ".join(on) if on else "(none)"))
    print("\n  Restart Claude Code (or start a new session) to pick up the hooks.")
    return 0


def cmd_uninstall(args):
    paths = resolve_paths(args.home, args.hooks_dir)
    sidecar = read_sidecar(paths)
    print("\n  home      : %s" % paths["home"])
    if not sidecar or not sidecar.get("installedCommands"):
        print("  Nothing recorded as installed — leaving settings.json alone.")
        return 0
    owned = set(sidecar["installedCommands"])
    print("  Removing %d command(s) recorded in %s" % (len(owned), SIDECAR_FILE))
    if not args.yes and not confirm("Proceed?"):
        print("  Aborted, nothing written.")
        return 1

    settings = read_json(paths["settings"], default={}, label=SETTINGS_FILE)
    removed = []
    if isinstance(settings, dict):
        removed = remove_commands(settings, owned)
        write_json(paths["settings"], settings)
    # `null`, not deletion — same convention the madhub installer uses.
    write_json(paths["sidecar"], None)
    print("  %s removed %d command(s)." % (OK, len(removed)))
    print("  %s %s left as-is (inert without the wiring; delete it to reset)."
          % (DOT, HOOKS_CONFIG_FILE))
    return 0


def cmd_status(args):
    paths = resolve_paths(args.home, args.hooks_dir)
    sidecar = read_sidecar(paths)
    settings = read_json(paths["settings"], default={}, label=SETTINGS_FILE)
    wired = existing_commands(settings.get("hooks") or {}) if isinstance(settings, dict) else set()
    config = read_json(paths["hooks_config"], default={}, label=HOOKS_CONFIG_FILE)
    if not isinstance(config, dict):
        config = {}

    print("\n  Crochetomancy status")
    print("  hooks dir : %s" % paths["hooks_dir"])
    print("  settings  : %s (%d hook command(s) total)" % (paths["settings"], len(wired)))

    if not sidecar:
        print("  installed : nothing recorded (no sidecar)")
    else:
        installed_groups = [g for g in sidecar.get("groups") or [] if isinstance(g, str)]
        via = sidecar.get("installer") or "madnessDesktop"
        if installed_groups:
            print("  installed : %s  (via %s, %s)" % (
                ", ".join(installed_groups), via, sidecar.get("installedAt") or "unknown"))
        elif sidecar.get("tier"):
            print("  installed : tier %r via madhub, %s — no group record, so this "
                  "CLI can only report totals" % (sidecar["tier"], sidecar.get("installedAt") or "unknown"))
        else:
            print("  installed : %d command(s) recorded, no group or tier label" % len(
                sidecar.get("installedCommands") or []))
        if sidecar.get("tier") and installed_groups:
            print("  tier      : %s (madhub install preserved alongside groups)" % sidecar["tier"])

    try:
        manifest = load_manifest(paths["hooks_dir"])
    except InstallError as e:
        print("  %s %s" % (NO, e))
        return 0

    # Two independent facts per group, because they can disagree: whether the
    # sidecar says we selected it, and whether its commands are wired. mongo and
    # mqtt share the telemetry wiring, so "14/14 wired" alone would report mongo
    # as installed on an mqtt-only box.
    recorded = [g for g in (sidecar or {}).get("groups") or [] if isinstance(g, str)]
    print("\n  Groups")
    for name, group in public_groups(manifest):
        # In isolation — core's commands shouldn't be counted against every group.
        expanded = expand_groups(manifest, [name], paths["hooks_dir"],
                                 include_required=False)
        commands = [c for cmds in expanded.values() for c in cmds]
        present = [c for c in commands if c in wired]
        complete = commands and len(present) == len(commands)
        if recorded:
            chosen = name in recorded
            mark = OK if chosen else DOT
            state = "selected" if chosen else "--      "
        else:
            # No group record (madhub tier, or hand-rolled) — wiring is all we know.
            mark = OK if complete else (DOT if present else NO)
            state = "        "
        note = ""
        if complete and recorded and name not in recorded:
            note = "  (wiring present via another group)"
        print("   %s %-12s %s  %2d/%-2d wired  %s%s" % (
            mark, name, state, len(present), len(commands),
            group.get("label", ""), note))
        if args.verbose:
            for command in commands:
                print("       %s %s" % (OK if command in wired else NO, command))

    print("\n  Flags (%s)" % paths["hooks_config"])
    if not config:
        print("   %s no config file — every flag reads as ON (hook defaults)" % DOT)
    for flag in all_managed_flags(manifest):
        if flag in config:
            print("   %s %s" % (OK if config[flag] else NO, flag))
        else:
            print("   %s %s (absent → ON by default)" % (DOT, flag))
    strangers = [k for k in config if k not in all_managed_flags(manifest)]
    if strangers:
        print("   %s unmanaged keys preserved: %s" % (DOT, ", ".join(sorted(strangers))))

    known = dict(public_groups(manifest))
    label_groups = [
        g for g in (sidecar or {}).get("groups") or []
        if isinstance(g, str) and g in known
    ]
    print("\n  Deps (%s)" % (", ".join(label_groups) if label_groups else "all groups"))
    probe_names = label_groups or [n for n, _ in public_groups(manifest)]
    for dep, present, optional, note in check_deps(manifest, probe_names):
        mark = DOT if present is None else (OK if present else NO)
        print("   %s %-12s %s%s" % (
            mark, dep, note, "" if not optional else " (optional)"))
    print()
    return 0


# ── entrypoint ─────────────────────────────────────────────────────────────

def build_parser():
    # SUPPRESS, not a real default: these live on both the top parser and every
    # subparser, and argparse lets a subparser's defaults overwrite values the
    # top parser already set. With nothing to write, `--home X install` survives.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--home", default=argparse.SUPPRESS,
        help="home directory whose .claude/ we touch (default: %s). "
             "Point it at a scratch dir to test without risking your config."
             % Path.home())
    common.add_argument(
        "--hooks-dir", default=argparse.SUPPRESS,
        help="hook clone to wire up (default: this file's directory)")

    parser = argparse.ArgumentParser(
        parents=[common],
        description="Install crochetomancy hooks into Claude Code by integration group.",
        epilog="Run bare for an interactive picker.")
    subs = parser.add_subparsers(dest="command")

    p_install = subs.add_parser(
        "install", parents=[common], help="wire groups into settings.json (additive)")
    p_install.add_argument(
        "--groups", action="append", default=[], metavar="a,b,c",
        help="groups to install; repeatable or comma-separated. Omit for the picker.")
    p_install.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    p_install.set_defaults(func=cmd_install)

    p_uninstall = subs.add_parser(
        "uninstall", parents=[common], help="remove exactly what we recorded installing")
    p_uninstall.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_status = subs.add_parser(
        "status", parents=[common], help="what is wired, which flags, which deps")
    p_status.add_argument("--verbose", "-v", action="store_true",
                          help="list every command, wired or not")
    p_status.set_defaults(func=cmd_status)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # Bare invocation: interactive install.
        args = parser.parse_args(argv + ["install"])
    args.home = getattr(args, "home", None) or str(Path.home())
    args.hooks_dir = getattr(args, "hooks_dir", None)
    try:
        return args.func(args)
    except InstallError as e:
        print("\n  %s %s\n" % (NO, e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n  Aborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
