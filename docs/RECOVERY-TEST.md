# Attended L2 responsiveness and recovery checks

These checks are for the owner after installing v0.7.0, not CI or an automatic
hardware probe. The default connection-reuse option is off. Power/proxy operations
below have **not** been executed by the coding agent.

## Prepare / restore

Confirm the installed integration version and existing options. Use Refresh state
to obtain reported state, then capture the target strip with HA's native scene
snapshot before changing it. Restore that scene after the test, then refresh to
check the result. Keep the option's original value so it can also be restored.
If the strip is unavailable, restore connectivity before starting output tests.

| Check | Expected result / evidence |
| --- | --- |
| Baseline with reuse off | Each action connects/authenticates, has a matching ACK and finishes cleanup. No retained connection. |
| Two normal controls <3 seconds apart with reuse on | First is fresh, second has `reused_connection: true`, null connection/auth stages, a distinct ACK sequence and visibly correct output. No inferred speedup from write counts alone. |
| Wait more than 3 seconds | `runtime.connection_retained` becomes false; `lease_cleanup` reports successful disconnect. Next command authenticates afresh. |
| Continuous controls past 15-second reuse age | A new connection/authentication occurs rather than indefinite renewal. Avoid claiming a hard 15-second close if a command is still in flight. |
| Refresh state while a lease is held | Lease closes; read uses a new connection/authentication and actual report. Command-session notifications must not appear as fresh readback. |
| IR/app change, then Refresh state / next poll | HA reflects fresh reported state, not only the previous acknowledged target. |
| Power cycle only the L2 controller manually | Temporary absence/failure is possible; after advertising returns, a separate normal action or refresh reconnects. No wrong-key repair prompt for a timeout. |
| Temporarily disconnect/reconnect the selected proxy network | Affected command may fail; after recovery the next separate action connects afresh. The integration must not blindly replay an uncertain command. |
| Reload integration with reuse held | Lease/task closes on unload and new instance starts fresh. No orphan proxy slot. |
| Disable reuse | Options reload releases the old lease; subsequent actions use default per-request cleanup. |

Native diagnostics are read-only and can be downloaded during operations. Record
version/options, action/read outcomes, per-stage timings, retained/reused flags and
cleanup outcome. Keep account keys, identifiers, network details and raw exports
out of public issues. A few successful actions do not prove long-run reliability;
repeat ordinary use over several days before promoting reuse to a default.

## Synthetic regression coverage

`tests/test_reuse.py` covers disabled mode, unique rapid ACK sequences, warm reuse,
idle/max-age expiry, fresh read/query/auth sessions, dropped links, poisoned queue,
negative/stale ACKs, partial write failure without replay, expiry/command races,
active-operation cancellation, native task cancellation, cancelled disconnect
ownership, failed cleanup, task-creation fallback, options serialization and native
unload/stop paths. These are software tests, separate from the attended table.
