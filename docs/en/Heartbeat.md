# Heartbeat Jobs and Health Checks

[简体中文](../zh/心跳.md)

JiuwenSwarm separates session continuation, independent scheduled execution,
and service probing into three distinct capabilities.

| Capability | Heartbeat job | Cron job | HealthCheck |
|------------|---------------|----------|-------------|
| Primary use | Continue work in the original session | Start an independent task on schedule | Check Gateway-to-AgentServer connectivity |
| Execution context | Original `channel_id + session_id` | Independent execution context | Temporary system probe |
| Invokes an Agent | Yes | Yes | No; returns only `HEALTH_CHECK_OK` |
| History | Stored in the original session | Stored with the scheduled job | Excluded from normal session history |
| Task source | Prompt saved with the job | Cron job description | None; never reads `HEARTBEAT.md` |

## Heartbeat jobs

A Heartbeat job is an automated continuation bound to the current session. On
trigger, Gateway sends the saved prompt back to that session as a regular
`chat.send` request and marks it with
`metadata.automation.kind=heartbeat`. It does not create a separate session or
introduce a special low-level `RunKind`.

### Busy-session behavior

When the bound session is processing a user turn, Heartbeat neither preempts nor
cancels it. It waits up to 60 seconds for the session to become idle. If the
timeout expires, the current occurrence is recorded as `skipped`; recurring
jobs remain eligible for their next schedule. This wait rule is independent of
`concurrency_policy`, which controls overlap between Heartbeat runs using
`skip`, `queue`, or `replace`.

Heartbeat user messages, assistant responses, tool calls, and automation
metadata are written to the original session history. Previous Heartbeat runs
remain visible after the session is reloaded.

### Schedule types

- `interval`: fixed interval, with a default minimum of 60 seconds.
- `cron`: a five-field Cron expression with an optional IANA timezone.
- `once`: one execution at a Unix timestamp.

The default maximum run count is 12. `max_runs=null` explicitly enables an
unbounded job and should be used with care. Job status is one of `scheduled`,
`running`, `completed`, `expired`, or `disabled`.

### Resource configuration

```yaml
heartbeat:
  jobs:
    min_interval_seconds: 60
    max_active_jobs_per_session: 5
    max_active_jobs_global: 100
    default_max_runs: 12
    default_concurrency_policy: skip
    default_session_deleted_policy: disable
```

Environment overrides are:

- `HEARTBEAT_JOBS_MIN_INTERVAL`
- `HEARTBEAT_JOBS_MAX_ACTIVE_PER_SESSION`
- `HEARTBEAT_JOBS_MAX_ACTIVE_GLOBAL`
- `HEARTBEAT_JOBS_DEFAULT_MAX_RUNS`
- `HEARTBEAT_JOBS_DEFAULT_CONCURRENCY_POLICY`
- `HEARTBEAT_JOBS_DEFAULT_SESSION_DELETED_POLICY`

Jobs are persisted in `heartbeat_jobs.json` under the user data directory. If
that file is corrupt or unreadable, Heartbeat scheduling is disabled and the
error is logged without preventing Gateway, Cron, or ordinary channels from
starting.

### Web/RPC methods

- `heartbeat.job.list`
- `heartbeat.job.meta`
- `heartbeat.job.get`
- `heartbeat.job.create`
- `heartbeat.job.update`
- `heartbeat.job.delete`
- `heartbeat.job.toggle`
- `heartbeat.job.preview`
- `heartbeat.job.run_now`
- `heartbeat.job.cancel`

An Agent can also manage jobs in the current session through the corresponding
`heartbeat_*` tools. Ordinary callers can access only jobs bound to the current
session.

## HealthCheck

HealthCheck is the renamed liveness mechanism. It checks connectivity only and
does not execute user tasks.

```yaml
health_check:
  every: 3600
  target: web
  active_hours:
    start: 08:00
    end: 22:00
```

Preferred environment variables are `HEALTH_CHECK_INTERVAL`,
`HEALTH_CHECK_RELAY_CHANNEL_ID`, and `HEALTH_CHECK_TIMEOUT`. Legacy
`HEARTBEAT_*` variables are read only as migration fallbacks.

Public methods and events are:

- `health_check.get_conf`
- `health_check.set_conf`
- `health_check.relay`

At startup, legacy `heartbeat.every/target/active_hours` values are migrated to
`health_check`, while `heartbeat.jobs` is preserved. New writes use only the new
name. Upgrade Gateway and AgentServer atomically when possible. For a staged
rollout, upgrade AgentServer before Gateway.

Session-bound Heartbeat jobs currently require a local WebSocket AgentServer
topology with shared session storage. Other deployment types reject Heartbeat
job operations instead of accepting jobs that cannot safely resume a session.

## Cron jobs

Cron retains its independent scheduler, execution context, and existing
`cron.job.*` methods. Heartbeat does not bind Cron jobs to chat sessions and
does not change Cron's five/seven-field compatibility behavior.

## Code index

| Path | Purpose |
|------|---------|
| `jiuwenswarm/gateway/heartbeat/` | Models, persistence, API, and scheduling for session-bound Heartbeat jobs |
| `jiuwenswarm/gateway/health_check/` | Connectivity probe that never runs user tasks |
| `jiuwenswarm/gateway/cron/` | Independent Cron jobs |
| `jiuwenswarm/gateway/channel_manager/web/app_web_handlers.py` | Web/RPC methods |
| `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py` | HealthCheck short-circuit and Heartbeat Agent tools |
