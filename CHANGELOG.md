# Changelog

## 0.4.2 — Native installation diagnostics

- Expand native config-entry diagnostics and add a native device diagnostics hook.
- Read-only HA Bluetooth-cache snapshot: scanner/connection-path counts, visibility,
  signal strength, advertised control-service flag and identity-match boolean.
  Never include scanner objects, identifiers, proxy names/hosts or raw advertising.
- Separate latest read outcome from control outcome/coordinator update success so
  an ACK cannot make a failed state read appear successful. Include in-progress,
  polling, unknown/reported/assumed state and state-field-presence flags.
- Downloads work without loaded runtime or Bluetooth readiness, never acquire the
  operation lock or trigger a scan/connection/refresh/command, and sanitize unknown
  outcome/error type names. No new dependencies, entities or config migrations.
- Privacy/read-only/unloaded/in-flight regressions and documented download steps.

## 0.4.1 — HA developer-guidance review

- Correct dynamic effect colour modes: preset effects expose brightness only,
  rhythm exposes on/off, and static RGB uses HA's standard `effect: off`.
  Allow static scene restoration with effect-off plus RGB; keep Solid as an alias.
- Track connectable Bluetooth presence and unavailability using HA-managed
  callbacks, automatically remove listeners on unload, and keep only the recovery
  button always available. Rediscovery/missing state never display stale values.
- Distinguish explicit authentication rejection from connection/protocol errors;
  add native same-entry reauthentication without false prompts on timeouts.
- Prevent reconfiguration from rebinding MAC/device ID and orphaning registry IDs.
- Bound the entire connected GATT session, including notify setup and writes.
- Add credential/identifier-safe whitelisted diagnostics, translated entity names
  and runtime action exceptions, and classify invalid actions as validation errors.
- Declare local_polling for optional local readback, explicit platform concurrency,
  and document reviewed official sources, fixes, deliberate trade-offs and limits.

## 0.4.0 — Native local state and experimental effects

- HA DataUpdateCoordinator and shared native light/button/number entities; no
  post-setup dependency on eWeLink, SonoffLAN, token refresh or a vendor app.
- Best-effort startup read, native Refresh state button, optional 30-3600s polling
  (disabled by default), fresh-session report validation and explicit
  reported/assumed state. Missing state/ACK-only replies never fabricate state.
- Serialize report reads and mode-dependent command construction with controls;
  drop stale report fields on fresh reads and preserve retry capability on failure.
- Opt-in experimental controls: exact 21 L2 presets, effect-preserving brightness,
  native speed number, four controller-rhythm effect presets and sensitivity number.
- New effects/rhythm and installed-HA refresh behaviour are software-tested only.
  Standalone sound response is not proven; no audio feed or media-player sync added.
- Native options flow with frontend serialization coverage; no lighting commands
  sent during setup/reconfiguration or any state refresh.

## 0.3.1 — Latest Home Assistant only

- Target HA 2026.10.4 (latest stable checked 2026-11-01) and Python 3.14 only.
- Remove old-HA schema detection, old optional dependency branches and the
  two-version CI matrix; use native probatio forms directly.
- Align HACS minimum, development pins and documentation with the current release.
- Document app feature gaps, evidence limits and recommended implementation order.

## 0.3.0 — Assisted setup

- Setup menu: import from an already-loaded SonoffLAN registry, import with a
  region-prefixed eWeLink token, or enter credentials manually.
- Filter supported L2/UIID 137 records, select a strip, and match its exact BLE
  advertisement using HA-owned connectable adapters/proxies.
- Confirmation/editing of name and BLE address; absent or ambiguous discoveries
  preserve manual address entry. Authentication-only validation remains unchanged.
- Token import reads profile/device data across homes over fixed HTTPS hosts,
  disables redirects, and never saves the token or sends light commands.
- Imported keys stay in backend flow state and only the selected keys are saved;
  no saved credentials are returned to frontend forms. Setup forms tested through
  the native HA schema serializer on minimum/current HA versions.
- Update mypy to 2.4.1. Preserve HA-compatible serialx/Supervisor pins and the
  minimum HA floor; prevent independent Dependabot bumps of matrix-managed pins.
- BLE protocol/physical validation is unchanged; the new import paths are covered
  by synthetic tests, not yet a live cloud-token or installed-HA acceptance test.

## 0.2.0 — Initial public repository

- Original-firmware L2 Bluetooth control: on/off, RGB and brightness.
- HA-managed Bluetooth adapters and active ESPHome proxies.
- UI configuration flow with format/identity checks and authentication-only setup.
- Optional account apikey with owned-device default; masked credential inputs.
- Reconfiguration flow preserving saved credentials when left blank.
- Matching encrypted ACKs, per-device serialization and bounded cleanup.
- Private, read-only SonoffLAN cache export helper.
- Protocol/provenance/hardware notes, manual and HACS-custom installation guidance.
- Ruff, strict mypy, coverage, two-version HA tests, hassfest and tag packaging CI.

Earlier local prototypes (0.1.x) were not published releases. A physical L2 test
verified commands/readback and the owner confirmed visible output. Full live-HA
installation and broader firmware/offline/power-cycle checks remain outstanding.
