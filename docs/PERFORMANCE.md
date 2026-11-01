# Command responsiveness

## Responsiveness features (since v0.6.0)

- HA publishes the requested (assumed) state as soon as the matching successful
  device ACK is validated, rather than waiting for disconnect to finish.
- Default actions still wait for bounded cleanup with both locks held. The optional
  reuse mode below retains an owned live connection instead; its idle cleanup is
  entry-managed and serialized with operations, never an untracked task.
- Native diagnostics record relative timings, byte counts and GATT write-call
  counts for the latest authentication/read/update/query session. No payload,
  address, name, credential, wall-clock timestamp or exception text is added.
- MTU-aware writes reduce the number of GATT operations. Since v0.6.1 they are
  enabled by default after successful live L2 read/control diagnostics and owner
  feedback. Explicitly saved false remains false on upgrade.

The default write size follows the adapter limits below. Disabling larger packets
restores conservative 20-byte writes. Setup/reauthentication credential validation
always uses 20-byte writes, independently of the runtime option.
No command is blindly retried after a partial write or lost ACK. State remains
assumed after a command: faster HA feedback is not fresh readback or proof of
physical light output. The 60-second polling default is unchanged.

## Measure before tuning

1. Update/redownload the integration and restart HA.
2. To establish a conservative baseline, turn **Use larger Bluetooth packets** off.
   Perform a normal on/off, colour or
   effect change, then download native diagnostics from the integration/device.
3. Inspect `data.runtime.transport_timings.update`. Durations are milliseconds:

| Field | Meaning |
| --- | --- |
| `queue_ms` | Wait for the transport lock. Coordinator lock waiting is separately shown in `last_control_queue_ms` / `last_read_queue_ms`. |
| `connect_ms` | HA connection helper, connection/service discovery and any connector retries. |
| `notify_ms` | Notification subscription setup. |
| `authentication_ms` | Challenge/token exchanges, including their writes and response waits. |
| `command_ms` | Control/query writes and wait for the matching ACK. |
| `ack_ms` | Update-session elapsed time through the successful control ACK, before disconnect; excludes coordinator lock wait. |
| `disconnect_ms` | Bounded disconnect cleanup. |
| `total_ms` | Entire session, including its lock wait and cleanup. |
| `write_calls` / `bytes_written` | Attempted GATT write calls / bytes whose write call completed, not device acceptance. |
| `chunk_size` | Selected write limit: up to 244, or 20 with conservative/unknown limits. |
| `reused_connection` / `retained_connection` | Whether this command skipped connection/authentication and whether it handed ownership to the idle lease. |
| `outcome` / `disconnect_outcome` | Separate safe categories, so successful ACKs and cleanup failures are distinguishable. |

Unreached stages are `null`; partial/in-flight snapshots are valid. Each operation
keeps its own latest record, not a history/average or a physical reaction-time
measurement. A failed connect still records its duration; cancellation is distinct.
If the action is cancelled after its ACK during cleanup, the ACKed assumed state
is retained, while session/cleanup cancellation is recorded separately.

Large connection time suggests proxy range, contention or connection negotiation;
large command/authentication time suggests packet/response overhead. Large
coordinator queue wait suggests an operation already running, including a poll.
Moving the proxy closer can improve reliability; it is not a guaranteed speedup.
Do not shorten failure timeouts merely to improve successful-command latency.

## MTU-aware writes and conservative fallback

In **Settings → Devices & services → SONOFF Bluetooth → Options/Configure**,
**Use larger Bluetooth packets** defaults to on when no choice is saved. Existing
explicit true/false choices are preserved. Options reload the integration; its
startup read also uses the selected write policy.

When enabled, the chunk limit is at most `MTU - 3`, capped at 244 bytes.
Write-without-response characteristics are additionally capped by the backend's
advertised maximum and keep the existing pacing. Unknown/unsupported MTU or
backend limits use 20 bytes. The normal write-with-response policy is unchanged.
The tested L2 accepted a 244-byte limit, but an adapter-reported MTU alone is not
proof of compatibility with every controller firmware.

Repeat the same normal actions and compare `chunk_size`, `write_calls`, timings
and visible output. If control becomes unreliable, disable the option and retry
normally. There is no automatic resend/fallback after a failed larger write:
the original command may already have taken effect. The **Refresh state** button
uses the same selected packet policy, so turn the option off before recovering
from incompatible larger packets.

## Live L2 evidence — 2026-11-01

The owner reported that v0.6.0 felt much better. Read-only diagnostics subsequently
confirmed MTU-aware writes enabled at a 244-byte limit, successful authentication,
a matching control ACK, fresh reported polling state and successful cleanup.
The latest control used four writes / 528 completed bytes: connection 579 ms,
notification setup 100 ms, authentication 204 ms, command/ACK 205 ms, ACK elapsed
1.09 s and full session 1.10 s. The latest polling read used two writes and took
0.50 s. Queueing was negligible. Recent logs contained no SONOFF/Bleak/Bluetooth
runtime errors, only HA's normal custom-integration loader warnings.

These are individual snapshots, not averages or a measured before/after speedup.
They support the owner's requested v0.6.1 default for L2, not universal firmware,
long-run or exhaustive preset compatibility. No HA settings or hardware were
changed during the log/diagnostics review or this default-change implementation.

## Optional bounded connection reuse — v0.7.0

Enable **Reuse connection briefly (hardware test; default off)** in Options to
try warm successive commands. The first update connects/authenticates normally.
After its matching ACK, HA owns that same live connection for up to three idle
seconds. A following control can skip connection, notification setup and auth.
The client is never reused after disconnect: the next connection uses a fresh
client through HA's connection helper. Reuse is not yet hardware-validated.

- Only successfully acknowledged controls may retain a session. No warm connection
  is opened speculatively, and no request is automatically resent on failure.
- Idle timeout is three seconds. The reuse deadline is 15 seconds after first
  retention and is not extended by later commands. After it passes, no new reuse
  starts. An already-running command keeps its normal bounded deadline/cleanup
  rather than being interrupted midway just to release a proxy slot.
- The proxy's BLE slot remains occupied during idle time. Other BLE clients may
  have to wait; disable reuse if this affects your proxies or phone control.
- All fresh state reads, queries and credential validation close any retained
  session before making a new connection. Old command-session notifications are
  never promoted to fresh reported state.
- A live disconnected/expired session is discarded before writing. A write, auth,
  protocol or ACK failure closes it and fails that action without automatic replay;
  the next separate action connects afresh. Rapid commands use strictly increasing
  numeric sequences even within a single clock millisecond.
- Idle cleanup uses a native config-entry background task and the transport lock.
  The integration explicitly closes/join tasks on unload and HA stop; native task
  cancellation is also handled. No permanent connection or unowned cleanup task.

With reuse enabled, action completion/`total_ms` may precede disconnect because
the slot is intentionally retained. `reused_connection` and `retained_connection`
identify warm/held operations; connect/notify/auth timings are null when skipped.
`disconnect_outcome: deferred` is not a cleanup success; latest idle/forced cleanup
is separately recorded in `transport_timings.lease_cleanup`. Runtime
`connection_retained` says whether the transport currently owns an idle connection.

For an attended comparison, start with reuse off and normal larger packets on.
Record an action's diagnostics and visible output. Enable reuse, perform two
normal actions less than three seconds apart, and inspect the second action:
it should show warm reuse and no new authentication. Check idle release and
Refresh state, then restore the original light state. Disable reuse if commands
or recovery are unreliable. See [recovery test plan](RECOVERY-TEST.md).

Synthetic peers cover lifecycle races, cancelled cleanup, expiry, fresh readback,
dropped links, stale/negative ACKs and no replay. They do not establish real-device
warm-session compatibility or a physical latency reduction. No live device changes
were made while implementing this option. HACS CI now runs with Python/hassfest;
only its external brands-index registration check is exempt for this custom repo.
