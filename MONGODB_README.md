# MongoDB Integration for Claude Hooks

All hooks in this directory now store their data in MongoDB instead of JSON files.

## Configuration

### MongoDB URI
Set the MongoDB URI using an environment variable:

```bash
export CLAUDE_MONGO_URI="mongodb://localhost:27017/"
```

If not set, it defaults to `mongodb://localhost:27017/` (local MongoDB instance).

### Database Name
The database name is automatically generated from your project directory name. For example:
- Path: `~/lab/madness_interactive/Anathesmelt`
- Database: `claudehooks_Anathesmelt`

This keeps the database name short and compliant with MongoDB's 63-character limit.

## Collections

Each hook stores data in its own collection:

### `tool_use`
Combined pre and post tool execution data.
- Created by `pre_tool_use.py` with status='pre'
- Updated by `post_tool_use.py` with status='complete'
- Fields:
  - `_id`: UUID of the tool execution
  - `tool_name`: Name of the tool
  - `tool_input`: Input parameters
  - `timestamp_pre`: When the tool was called
  - `timestamp_post`: When the tool completed
  - `tool_output`: Output from the tool
  - `status`: 'pre' or 'complete'
  - `session_id`: Claude session ID

### `session_start`
Session initialization events.
- Fields:
  - `session_id`: Claude session ID
  - `source`: 'startup', 'resume', or 'clear'
  - `timestamp`: When the session started

### `session_stop`
Session termination events.
- Fields:
  - `session_id`: Claude session ID
  - `stop_hook_active`: Whether the stop hook was active
  - `timestamp`: When the session stopped

### `subagent_stop`
Subagent termination events.
- Similar structure to `session_stop`

### `user_prompt`
User prompts submitted to Claude.
- Fields:
  - `session_id`: Claude session ID
  - `prompt`: The user's prompt text
  - `timestamp`: When the prompt was submitted

### `notification`
Notification events (when Claude is waiting for input).
- Fields:
  - `message`: Notification message
  - `timestamp`: When the notification was sent

### `pre_compact`
Pre-compaction events (before context window compaction).
- Fields:
  - `session_id`: Claude session ID
  - `trigger`: 'manual' or 'auto'
  - `transcript_path`: Path to the transcript file
  - `custom_instructions`: Any custom compaction instructions
  - `timestamp`: When compaction was triggered

### `chat_transcript`
Full chat transcripts (stored when using `--chat` flag).
- Fields:
  - `session_id`: Claude session ID
  - `messages`: Array of all messages in the transcript
  - `transcript_path`: Original path to the .jsonl file
  - `timestamp`: When the transcript was stored

### `subagent_transcript`
Subagent chat transcripts.
- Similar structure to `chat_transcript`

## Tool Use Tracking

The `pre_tool_use.py` and `post_tool_use.py` hooks work together to create a complete record:

1. **pre_tool_use.py** creates a document with a UUID and stores:
   - Tool name and input
   - Pre-execution timestamp
   - Status set to 'pre'
   - The UUID is saved to `.claude_temp/last_tool_use_id.txt`

2. **post_tool_use.py** reads the UUID from the temp file and updates the document with:
   - Tool output
   - Post-execution timestamp
   - Status changed to 'complete'
   - The temp file is then deleted

This approach links pre and post data in a single document **as long as tool
calls are serial**.

### Known limitation: parallel tool calls orphan the `pre` document

`.claude_temp/last_tool_use_id.txt` holds exactly one id. Claude Code routinely
issues several tool calls in a single turn, and subagents add more concurrency —
so a second `pre_tool_use.py` overwrites the file before the first
`post_tool_use.py` reads it. The loser's document is never updated and stays at
`status: 'pre'` forever; the winner's document may be updated twice.

This is measurable, not theoretical. Across 12 project databases:

| Database | Total | Stuck at `pre` | % |
|---|---|---|---|
| `claudehooks_Inventorium` | 61,861 | 11,518 | 18.6% |
| `claudehooks_ElementalAlloy` | 17,356 | 4,841 | 27.9% |
| `claudehooks_AlloyMonorepo` | 8,145 | 2,328 | 28.6% |
| **all 12** | **142,504** | **29,114** | **20.4%** |

Consequences when querying:

- Filter on `status: 'complete'` for anything that needs output or duration. A
  bare `find()` over `tool_use` is ~20% orphans.
- `timestamp_post - timestamp_pre` is only meaningful on completed documents.
- Counts of tool *invocations* should use `status: 'pre'` plus `'complete'`
  together; counts of tool *results* should use `'complete'` alone.

`tool_use_id` is already carried on the MQTT heartbeats and would pair the two
sides correctly. Moving Mongo onto it (id file → per-`tool_use_id` key, or
writing `pre` and `post` as separate documents joined on `tool_use_id`) is the
obvious fix, and is deliberately not done here — it changes the collection
shape, so it wants a decision rather than a drive-by.

## Error Handling

All hooks fail silently if MongoDB is not available. This ensures that Claude continues to work even if:
- MongoDB is not running
- The connection URI is incorrect
- There are any database errors

The hooks will simply not store data but will not block Claude's operation.

Note that "not available" is not free: an unreachable mongod costs pymongo its
full `serverSelectionTimeoutMS` (500 ms) per attempt, which on the per-tool-call
hooks means 500 ms on every tool call. A missing `pymongo` import costs nothing.

`pre_tool_use.py` and `post_tool_use.py` exit via `os._exit(0)` rather than
`sys.exit(0)` to skip interpreter teardown — see *Cost per tool call* in
`README.md`. Writes are safe across that early exit because `insert_one` and
`update_one` are server-acknowledged before the hook returns.

## Querying Data

You can query the data using any MongoDB client or the `mongosh` shell:

```bash
# Connect to MongoDB
mongosh mongodb://localhost:27017/

# Switch to your project database
use claudehooks_Anathesmelt

# View all collections
show collections

# Query tool use data
db.tool_use.find({status: 'complete'}).sort({timestamp_post: -1}).limit(10)

# Query user prompts
db.user_prompt.find().sort({timestamp: -1}).limit(10)

# Find all tool uses for a specific session
db.tool_use.find({session_id: 'your-session-id'})

# Count tool executions by tool name
db.tool_use.aggregate([
  {$match: {status: 'complete'}},
  {$group: {_id: '$tool_name', count: {$sum: 1}}},
  {$sort: {count: -1}}
])
```

## Dependencies

All hooks now require `pymongo`:

```bash
uv pip install pymongo
```

This is automatically handled by the uv script headers in each hook.

## Temp Directory

The `.claude_temp` directory is used to pass the tool execution UUID between pre and post hooks. This directory is automatically created and cleaned up. You may want to add it to `.gitignore`:

```
.claude_temp/
```
