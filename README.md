# Crochetomancy

Claude Code hooks, woven into a loadout you can pick apart.

Every hook event Claude Code fires — session start/end, each prompt, each tool
call, subagents, compaction, tasks, permission prompts — gets caught here and
turned into something useful: MongoDB event logs, MQTT broadcasts your other
agents and dashboards can subscribe to, todo context injected back into your
prompts, autocommits when a session stops.

Nothing is mandatory. You install *integration groups*, and every group degrades
to a no-op when its dependency is absent. `python3` alone gets you a working
install; Mongo, MQTT, node and `titancall` each light up more of it.

## Quick start

```bash
git clone https://github.com/MadnessEngineering/crochetomancy.git ~/.claude/hooks
python3 ~/.claude/hooks/install.py
```

That opens a picker. Or name the groups yourself:

```bash
python3 ~/.claude/hooks/install.py install --groups mqtt,omnispindle --yes
python3 ~/.claude/hooks/install.py status
python3 ~/.claude/hooks/install.py uninstall
```

Start a new Claude Code session afterwards — hooks are read from
`settings.json` at session start.

The installer is stdlib-only python3 and does no network access: the clone is
already in place, so it only wires it up.

### Optional python packages

```bash
pip install pymongo      # Mongo logging + Omnispindle todo sync
pip install dvttestkit   # MQTT publishing (plus mosquitto_pub for retained topics)
```

Both imports are wrapped — without them the affected hooks return quietly
instead of failing, and Claude Code never sees an error.

## Groups

| Group | Needs | What you get |
|---|---|---|
| `core` *(always installed)* | python3 | Session bootstrap + todo context, prompt capture, agent auto-naming, stop-hook autocommit |
| `mongo` | `pymongo` + a mongod | Every event logged into per-project `claudehooks_*` databases |
| `mqtt` | `dvttestkit`, `mosquitto_pub` | The whole lifecycle published to `status/{DeNa}/claude/#` |
| `omnispindle` | `pymongo` + a mongod | Active todos injected into prompts; Claude tasks mirrored into the `swarmonomicon` todos collection |
| `tinker` | node | Tinker-mode persona: activation banner, per-prompt mode tracking |
| `titancall` | a `titancall` binary | Fire-and-forget push notifications on every lifecycle beat |

`mongo` and `mqtt` both pull in the same hidden telemetry fragment — the 14
event hooks that produce the data. Selecting both wires them once.

Groups are additive: `install --groups mqtt` on top of an existing install adds
MQTT without disturbing what's already there. To *remove* a group, run
`uninstall` and reinstall the set you want — a precise shrink isn't safe while
the sidecar's command list is shared with the madhub installer (below).

## What it touches

| File | How |
|---|---|
| `~/.claude/settings.json` | Appends hook entries, deduped by exact command string. Your own entries are never rewritten, reordered, or removed. |
| `~/.claude/hooks_config.json` | Writes the feature flags the selected groups own; unknown keys are preserved. |
| `~/.claude/.madness-loadout.json` | Sidecar recording exactly which commands we added, so `uninstall` removes precisely those. |

Writes are atomic (`os.replace`). `install.py --home /tmp/scratch …` points the
whole thing at a throwaway directory, which is how the install paths are tested.

## Hooks

| Hook | Event | Does |
|---|---|---|
| `session_start.py` | SessionStart | Refreshes the flag config, optional `git pull` of main, builds todo + git context, sets the session title, publishes retained session/git context |
| `user_prompt_submit.py` | UserPromptSubmit | Stores each prompt, names the agent via local Ollama (Anthropic fallback), injects active Omnispindle todos |
| `pre_tool_use.py` | PreToolUse | Activity heartbeat + tool-call logging |
| `post_tool_use.py` | PostToolUse | Result logging, heartbeat, agent/tool correlation ids |
| `post_tool_use_failure.py` | PostToolUseFailure | Failure logging + notification publish |
| `notification.py` | Notification | Notification logging + MQTT publish (`--notify`) |
| `subagent_start.py` | SubagentStart | Opens a subagent span (agent id/type) |
| `subagent_stop.py` | SubagentStop | Closes the span, archives the subagent transcript, optional TTS announce (`--notify`) |
| `pre_compact.py` | PreCompact | Snapshots context before compaction |
| `post_compact.py` | PostCompact | Signals compaction done, attaches usage stats |
| `session_end.py` | SessionEnd | Final usage summary, clears retained context, pushes commits |
| `task_events.py` | TaskCreated / TaskCompleted | Task lifecycle to Mongo + MQTT, mirrors tasks into Omnispindle todos (`--event created\|completed`) |
| `permission_events.py` | PermissionRequest / PermissionDenied | Records permission decisions (`--event request\|denied`) |
| `teammate_idle.py` | TeammateIdle | Publishes idle agents so siblings can pick up work |
| `stop.py` | Stop | Transcript archive, autocommit with a generated message (`--autocommit`), optional TTS |
| `tinker-activate.js` | SessionStart | Injects the tinker-mode persona |
| `tinker-mode-tracker.js` | UserPromptSubmit | Tracks mode changes per prompt |

Helpers live in `utils/`: `usage_stats.py`, `llm/` (ollama, anthropic, openai
naming + commit messages), `tts/` (elevenlabs, openai, pyttsx3 — first one whose
API key is present wins).

See `MONGODB_README.md` for the collection layout and
`query_mongodb_example.py` for reading your own logs back.

## Environment

| Variable | Default | Used for |
|---|---|---|
| `CLAUDE_MONGO_URI` | `mongodb://localhost:27017/` | Hook event logging |
| `MONGODB_URI` | `mongodb://localhost:27017/` | Omnispindle todo sync |
| `MONGODB_DB` | `swarmonomicon` | Omnispindle todo database |
| `MADNESS_MQTT_HOST` | `localhost` | `mosquitto_pub` retained topics |
| `DeNa` | `macbook` | Device name — the `status/{DeNa}/claude/…` topic prefix |
| `ELEVENLABS_API_KEY` / `OPENAI_API_KEY` | — | TTS engine selection |
| `ANTHROPIC_API_KEY` | — | Agent naming / commit messages when Ollama is unavailable |

A `.env` in the working directory is loaded when `python-dotenv` is installed.

## Feature flags

`~/.claude/hooks_config.json` is the runtime switchboard. The installer writes
the flags the selected groups own; **a missing key reads as on**, so a hand-made
install with no config file behaves exactly as it always did.

| Flag | Group | Gates |
|---|---|---|
| `autocommit` | core | Stop-hook autocommit |
| `auto_pull_main` | core | `git pull` of main at session start |
| `context_injection` | omnispindle | Todo context injected into prompts |
| `task_sync` | omnispindle | Claude tasks mirrored into Omnispindle todos |
| `mongo_logging` | mongo | All MongoDB writes |
| `usage_stats` | mongo, mqtt | Token/usage summaries attached to payloads |
| `activity_heartbeat` | mqtt | Per-tool-call heartbeat |
| `session_mqtt` | mqtt | Session start/end + retained session & git context |
| `notification_mqtt` | mqtt | Notification + tool-failure publishes |
| `subagent_mqtt` | mqtt | Subagent start/stop spans |
| `compact_mqtt` | mqtt | Pre/post compaction publishes |
| `permission_mqtt` | mqtt | Permission request/denied publishes |
| `teammate_idle_mqtt` | mqtt | Idle-agent publishes |
| `stop_mqtt` | mqtt | Stop-hook publish |
| `task_mqtt` | mqtt | Task created/completed publishes |
| `tts_notify` | (telemetry) | Spoken subagent-completion announcements |

### Remote control

At session start, `session_start.py` reads the retained MQTT topic
`status/{DeNa}/claude/hooks_config` and merges it over the file. Precedence:

```
built-in defaults  <  hooks_config.json  <  retained MQTT payload
```

So you can flip a flag across every machine subscribed to your broker by
publishing a retained partial payload — and note the consequence: a retained
payload that names a flag **overrides your local installer choice** until you
clear or update it.

## Graceful degradation

Every integration is optional at runtime, not just install time:

- No `pymongo`, or mongod unreachable → Mongo writes skipped (500 ms timeout).
- No `dvttestkit` → `post()` is a no-op stub.
- No `mosquitto_pub` → retained context topics skipped; local context file still written for git hooks.
- No `titancall` / `node` → those commands fail in the background; hook exit codes are unaffected.
- No TTS engine or API key → announcements skipped silently.

Hooks exit 0 on any internal error by design. A broken integration must never
block your prompt.

## Alternative: install via madhub

[madnessDesktop](https://github.com/MadnessEngineering/madnessDesktop) installs
the same hooks along a different axis — *tiers* (`minimal`, `standard`, `full`)
picked from a preferences panel, which also clones and updates the repo for you.

Both installers read `loadout-manifest.json`, and every group command string is
byte-identical to its tier counterpart. That's deliberate:

- Install a tier in madhub, then `install.py install --groups mqtt` → zero
  duplicate wiring; only what's genuinely missing is added.
- `uninstall` from either one removes exactly what the sidecar records.
- The sidecar is a superset shape: `install.py` preserves madhub's `tier` field,
  madhub keeps the `installedCommands` list it needs.

Two things to know:

- Flags are written deterministically, so a managed flag no selected group owns
  is written `false`. Running `install.py` over a madhub `standard` install
  therefore quiets `mongo_logging` unless you include the `mongo` group.
- Dedupe is by *exact* command string. A hand-rolled entry that runs the same
  hook with different arguments (say `user_prompt_submit.py --log-only …`) or
  through a different path isn't recognised as the same wiring, so installing
  would add a second copy and the hook would fire twice.

The installer warns about both before writing anything.

## Layout

```
install.py               group installer (stdlib only)
loadout-manifest.json    groups + tiers catalog, single source of wiring truth
*.py                     the hooks themselves
utils/                   usage stats, LLM helpers, TTS engines
tinker-*.js              tinker-mode persona scripts
MONGODB_README.md        collection reference
```

Part of the [Madness Interactive](https://madnessinteractive.cc) workshop.

MIT licensed — see [LICENSE](LICENSE).
