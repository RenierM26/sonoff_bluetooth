# Native Home Assistant diagnostics

Both `async_get_config_entry_diagnostics` and `async_get_device_diagnostics` use
HA's [native diagnostics interface](https://contributors.home-assistant.io/core/integrations/diagnostics/).
There is one strip per entry, so both return the same safe snapshot. No diagnostic
sensors, extra services, dependencies, debug-logging switch or YAML are required.

## Download

1. Update/redownload **SONOFF Bluetooth** and restart HA so Python loads the new
   diagnostics module. Current code version: **0.6.0**.
2. Open **Settings → Devices & services → SONOFF Bluetooth**.
3. Open the entry's **⋮ → Download diagnostics**, or open its device and use
   **Download diagnostics** there.
4. To investigate a failed control, attempt the desired normal action, then download
   the JSON. Downloading itself never retries or changes the strip.

HA supplies the standard system/version/manifest envelope and filename. This
integration's section is under `data`, with `diagnostics_schema: 3`.

## Exported information

| Section | Contents |
| --- | --- |
| `options` | Effect-controls flag, configured poll interval and opt-in MTU-aware writes flag. No extra options are copied. |
| `bluetooth` | Backend readiness, scanner counts, cached visibility/connectability, signal strength (dBm), count of connectable paths and their anonymous signal values. |
| Identity / service checks | Boolean exact `ck_<deviceid>` match and whether the control service UUID appears in the cached advertisement. Neither the name/ID nor service/manufacturer payload is exported. A missing advertised UUID is not proof that GATT lacks the service. |
| `runtime` | Reachability, availability, explicit-auth rejection flag, polling enabled and whether a local operation currently holds the lock. |
| State source | `unknown`, `reported` or `assumed`, plus booleans indicating whether power, mode, brightness and static RGB fields are known. No actual household power/colour/mode values are included. |
| Latest operation outcomes | Independent read and control outcomes. A successful control ACK may set coordinator update success true, but it does **not** change a failed read into successful readback. |
| Session performance | Latest per-operation relative durations, selected chunk size and write/byte counts. Coordinator queue waits are separate; see [performance tuning](PERFORMANCE.md). Only safe numeric fields/categories are exported. |
| Error category | An allowlisted exception type/category only, never its message/traceback. Unknown types become `OtherError`. |

Before the first read/control, outcomes are `not_attempted`; while a read is running,
`last_read_success` is `null`, not an invented success/failure. A missing runtime is
`null`; an absent/unready Bluetooth backend is reported without failing the export.

## Interpreting installation problems

- No connectable scanners, or visible but not connectable: check the existing HA
  Bluetooth adapter / **active** ESPHome proxy. Listen-only receivers cannot control
  the strip. No firmware or configuration is changed by diagnostics.
- Zero connectable paths / weak RSSI: check strip power, proxy proximity and whether
  another BLE client is holding the strip. RSSI is cached, not a fresh measurement.
- Identity match false: check that the BLE address and eWeLink device ID identify
  the same strip. `null` means no canonical cached advertisement was available.
- `auth_failed`: the device explicitly rejected the key; complete native reauth.
- `connection_failed`: connectivity/timeout, not automatic proof of an invalid key.
- `invalid_report`: connection returned no usable state; that is not readback.
- `acknowledged`: a matching control ACK, not physical-output or standalone-music
  proof. Use normal Refresh state separately if needed, then download again.

## Privacy and side effects

Exports use an explicit allowlist. No apikey/devicekey/account token/password,
config-entry dump, BLE/Wi-Fi MAC, eWeLink device ID, friendly/room/proxy name, host,
raw state/advertisement/manufacturer data or exception message is included in the
integration payload. Additional unknown options/source-object fields are ignored.
Device-registry details are never copied. HA's own wrapper/filename is managed by HA.

Only already-cached HA Bluetooth API lookups and in-memory coordinator flags are
read. Downloads do not start a scan, open Bluetooth/cloud connections, authenticate,
refresh state, send light commands, acquire the operation lock, or wait for an
in-flight BLE session. A download is a snapshot, not an active health probe.
