# Command responsiveness

## What changes in v0.6.0

- HA publishes the requested (assumed) state as soon as the matching successful
  device ACK is validated, rather than waiting for disconnect to finish.
- The action itself and subsequent device operations still wait for bounded
  cleanup. Both locks remain held during cleanup, so no overlapping sessions,
  permanent connections or untracked background tasks are introduced.
- Native diagnostics record relative timings, byte counts and GATT write-call
  counts for the latest authentication/read/update/query session. No payload,
  address, name, credential, wall-clock timestamp or exception text is added.
- Optional larger writes can reduce the number of GATT operations. They are
  disabled by default because simulated peers do not prove firmware compatibility.

The default wire behaviour remains the tested 20-byte, acknowledged write path.
No command is blindly retried after a partial write or lost ACK. State remains
assumed after a command: faster HA feedback is not fresh readback or proof of
physical light output. The 60-second polling default is unchanged.

## Measure before tuning

1. Update/redownload the integration and restart HA.
2. Leave **Use larger Bluetooth packets** off. Perform a normal on/off, colour or
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
| `chunk_size` | Selected write limit: 20 by default. |
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

## Opt-in MTU-aware writes

In **Settings → Devices & services → SONOFF Bluetooth → Options/Configure**, enable
**Use larger Bluetooth packets (hardware test; default off)**. Options reload the
integration; its startup read also uses the selected write policy.

When explicitly enabled, the chunk limit is at most `MTU - 3`, capped at 244 bytes.
Write-without-response characteristics are additionally capped by the backend's
advertised maximum and keep the existing pacing. Unknown/unsupported MTU or
backend limits use 20 bytes. The normal write-with-response policy is unchanged.
A reported MTU is an adapter limit, not proof that L2 firmware accepts that size.

Repeat the same normal actions and compare `chunk_size`, `write_calls`, timings
and visible output. If control becomes unreliable, disable the option and retry
normally. There is no automatic resend/fallback after a failed larger write:
the original command may already have taken effect. The **Refresh state** button
uses the same selected packet policy, so turn the option off before recovering
from incompatible larger packets.

The synthetic tests show fewer write calls while preserving authentication and
ACK correlation. They do **not** establish a real-device speedup. No live HA,
proxy firmware or strip changes were made to implement v0.6.0. Short-lived session
reuse and control-priority scheduling are not added in this release; timings can
guide those later changes if they are actually needed.
