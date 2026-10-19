# Phase 1 offline guard

First-party, small integration plugin for synthetic offline acceptance. Copy this
directory unchanged into `data/plugins/phase1_guard` before AstrBot startup. No
third-party source is copied into it. It uses AstrBot's public plugin registration,
`StarTools.get_data_dir`, event hooks, `event.stop_event`, `event.send`, and
`event.clear_result`; it never patches upstream modules.

## Why this supplement exists

Source review against AstrBot **v4.29.1** found:

- `core/pipeline/process_stage/method/star_request.py` runs message handlers in
  registered priority order; `stop_event()` prevents subsequent handlers and LLM
  processing. Priority **1000000** claims an inbound ID before Recorder and model
  handlers. AstrBot's native whitelist runs before these message handlers.
- `core/pipeline/respond/stage.py` catches send failures and still invokes
  `OnAfterMessageSentEvent`. That callback alone cannot establish delivery.
- `core/platform/sources/aiocqhttp/aiocqhttp_message_event.py` awaits the real
  OneBot send API in `event.send`; a normal return establishes adapter-level
  acknowledgement. It does not expose the returned remote message ID.
- Message Recorder **0.0.4** uses an inbound `event_message_type(ALL)` listener
  and does not implement an outbound recorder. Keep its native inbound archive;
  this small companion table covers the missing delivery observation.

## Tested profile and limits

The supported Phase 1 profile is private OneBot v11 chat, synthetic identities
and text, with streaming and segmented replies disabled. This plugin does not
interpret user requests, call any model, create reminders, or retry messages.

The final `on_decorating_result(priority=-1000000)` hook awaits the actual public
`event.send(result)` with a configurable timeout, records its outcome, and clears
that result to stop a second native dispatch. Native adapter sends and the real
OneBot socket remain in the path. The core's post-send hook does not run for this
owned result; plugins that require that hook need separate compatibility testing.
No other plugin should dispatch the same result before this hook.
The priorities also bracket the selected PrivateCompanion's early inbound hooks
(up to 230000) and decoration hooks (down to -30000). Recheck this ordering when
upgrading plugins; arbitrary new plugins are not covered by the acceptance claim.

`sent` means the OneBot API call returned successfully, **not** proof that a real
QQ user read it. Offline acceptance additionally asserts the simulator received
the action. An exception is `failed`; a timeout/cancellation is `unknown` because
the peer may have accepted it before acknowledgement was lost. Incomplete
`pending` rows become `unknown/Interrupted` at startup. There is no automatic
retry and no production delivery ledger.

Complex/nontext or segmented results are recorded `unknown` and left to upstream
dispatch. Streaming is outside this plugin's claimed archive coverage. Direct
`event.send` calls by other plugins, proactive sends, future tasks, media files,
and separate platform sessions are not claimed as comprehensively archived.

Deduplication is at-most-once processing of the tuple `(platform_id,
unified_msg_origin, message_id)` persisted before downstream work. The tuple
stays claimed after errors or process crashes. This prevents duplicates but may
require manual investigation of an interrupted turn. It intentionally does not
provide exactly-once delivery or automatic crash recovery of unfinished replies.

## Configuration and data

`data/config/phase1_guard_config.json` accepts:

```json
{"send_timeout_seconds": 2.0}
```

Allowed timeout range is greater than zero through 30 seconds. The default is
2 seconds. Malformed or missing OneBot message identity is rejected before model
processing. A storage error likewise stops the inbound event. Error log entries
contain error class names, not raw response bodies or exception messages.

SQLite path: `data/plugin_data/phase1_guard/guard.sqlite3`. The persisted tables
are defined in [`store.py`](store.py):

- `inbound`: platform, session, message_id (composite primary key), sender_id,
  original text, original raw segments as chain_json, received_at, duplicate_count.
- `outbound`: id, inbound foreign key tuple, reply text and chain_json, state
  (`pending|sent|failed|unknown`), error_type, created_at, finished_at.

Timestamp strings explicitly include the `Asia/Shanghai` `+08:00` offset. These
tables contain synthetic content and belong in the ignored persistent data tree,
not in Git. Existing project SQLite backup/export tools can include them.

Read-only inspection after a test:

```sql
SELECT message_id, text, duplicate_count FROM inbound;
SELECT message_id, text, state, error_type FROM outbound ORDER BY id;
```

Local storage contract tests (not substituted for the real framework tests):

```bash
python -m unittest discover -s tests -p test_guard.py -v
```
