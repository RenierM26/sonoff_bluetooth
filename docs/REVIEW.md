# Home Assistant developer-guidance review

Reviewed 2026-11-01, targeting **HA 2026.10.4 / Python 3.14**, the latest stable
release verified for this project. Official developer URLs now redirect to the
Home Assistant Contributors site. Guidance was fetched live; APIs used by the
fixes were also checked against the installed stable HA source, not just unreleased
examples. This is a project code review, **not** a Home Assistant quality-tier
certification or proof of a live installation / physical effect behaviour.

## Findings fixed in v0.4.1

| Priority | Finding | Resolution / regression evidence |
| --- | --- | --- |
| Medium | Effect modes always reported RGB, including rhythm where brightness is not validated | Dynamic native `ColorMode.BRIGHTNESS` for preset effects, `ONOFF` for rhythm, RGB for static and UNKNOWN when state is absent. Native HA `state_attributes` tests check valid effect modes. |
| Medium | Static effects used Solid instead of standard EFFECT_OFF; restoring a static scene could reject effect-off + RGB | Report/list standard `off`; accept effect-off together with RGB/brightness. Keep Solid as a backwards-compatible input alias. Scene-style restoration regression added. |
| Medium | Every entity was always available, even when the device disappeared | Native connectable advertisement/unavailable subscriptions; communication failures mark unavailable. Rediscovery restores reachability, not invented device state. Recovery button remains available and retries without polling. Subscription unsubscriptions are owned by the entry. |
| Medium | Reconfiguration allowed changing MAC/device ID while old entity/device registry identities remained | Refuse changing physical identity and require a new entry for replacement hardware. Same-entry `data_updates` preserve IDs/options during name/key changes and reauthentication. No existing registry IDs were changed or migrated. |
| Medium | Device authentication rejection had no native credential-repair path | Explicit rejection has its own exception, coordinator raises `ConfigEntryAuthFailed`, controls request native reauth, and a masked same-device form updates/reloads the existing entry. Timeouts and generic malformed responses do not request reauth. |
| Medium | Only response waits were bounded; notify setup / GATT writes could stall indefinitely | A 45-second connected-session deadline covers notify setup, writes and responses, with separate bounded disconnect in finally. A stalled-write regression verifies disconnect and lock release. Connection establishment retains HA's retry connector and 20-second attempt timeout. |
| Low | Missing native diagnostics and hardcoded secondary entity names / unclassified action errors | Operational diagnostic allowlist, no raw config/report dump, translated button/number names and action errors; invalid commands use ServiceValidationError. Native diagnostics tests reject keys, tokens, names, IDs, MACs and exception messages. |
| Low | Manifest still described purely assumed state after local refresh was introduced | IoT class is local_polling, with default polling disabled; state source still accurately distinguishes reports and ACK-based assumptions. Platforms explicitly delegate request concurrency to the shared coordinator/transport locks. |

## Practices verified / preserved

- HA owns all scanners/adapters/proxies. `bluetooth_adapters` dependency, connectable
  address selection, fresh Bleak clients and retry connector; no private scanner,
  second proxy subscriber, hardcoded bridge, persistent GATT connection or firmware
  changes. Presence callbacks do **not** start GATT operations.
- Typed `ConfigEntry.runtime_data`, native coordinator/light/button/number entities,
  shared device identity and stable unique IDs. Entity properties do no I/O.
- No blocking file/network I/O or synchronous sleeps in the runtime event loop.
  The separate optional CLI exporter is not called from HA's runtime/config flow.
- Async setup forwards platforms; unload removes entities/listeners/coordinator
  scheduling. Native options update reloads the same entry. State polling is optional
  and defaults to 60 seconds since v0.5.0 (0 disables it). Every connection releases its slot in bounded cleanup.
- Mode-dependent commands are constructed inside the operation lock. Matching
  encrypted ACK required; no blind command replay after a timeout. Unsolicited
  pre-command reports cannot overwrite the requested state. Cancellation propagates
  and marks cached state uncertain.
- Setup/reconfiguration/reauth validate only; no lighting update sent. Native form
  serialization and completed-flow HTTP output tests verify credentials do not
  return to the frontend. Duplicate setups abort before contacting the strip.
- SonoffLAN is an explicitly optional, private-API **setup importer**; manual/token
  fallback documented. Cloud requests are setup-only, fixed regional HTTPS hosts,
  redirects disabled, per-request timeout, HA-managed HTTP session, sanitized errors.
  The token is not persisted, and runtime never uses a vendor app/cloud session.
- English translations and metadata are checked; sensitive source/APKs/keys excluded.
  Ruff, strict mypy, coverage and Home Assistant hassfest run in CI on current stable.

## Deliberate choices and remaining limits

- Best-effort startup uses `async_refresh`, not mandatory first-refresh setup
  failure. This is explicitly supported by the fetching-data documentation and
  leaves the local recovery button available for an off/out-of-range strip.
- Availability is not blindly identical to coordinator `last_update_success`:
  a reachable command-capable strip can omit state. A missing/invalid report then
  clears old fields to UNKNOWN while controls remain possible; a lost connection
  or HA unavailable event makes the light/numbers unavailable. The refresh button
  is intentionally exempt from that gate so users can recover with polling off.
- Reported state is only the latest successful session read, not continuous push
  sync. After a command/uncertain result state is assumed; native advertisements
  only describe reachability. They never invent switch, RGB or effect values.
- A corrupt/decrypt-failing frame cannot be conclusively identified as a wrong key,
  so native reauth starts only on an explicit authentication rejection. Manual
  reconfiguration remains available for other credential failures.
- Existing uppercase MAC-based unique IDs are preserved instead of applying a
  cosmetic normalization that would require a registry migration.
- Following the owner's installed-HA trial, L2 effects/rhythm/speed/sound reaction
  and 60-second polling have owner-reported hardware validation. Since v0.5.0
  controls default on and polling defaults to 60 seconds. Explicit saved options
  and existing entity unique IDs are preserved; no registry migration is needed.
- Unit tests use native HA APIs with simulated boundaries; the separate owner
  acceptance report is documented in HARDWARE-TEST.md. It does not establish
  exhaustive preset/sensitivity ranges, all recovery/startup conditions, other
  SONOFF models/firmware, long-run or Internet-blocked operation.
- No formal Bronze/Silver/Gold/Platinum tier is asserted; this custom component has
  not undergone Home Assistant core review. The legacy MD5/fixed-IV encryption is
  required by the device wire protocol, not a recommended new cryptosystem.

## Official references consulted

- [Bluetooth integrations](https://contributors.home-assistant.io/core/integrations/bluetooth/)
- [Fetching data and coordinator lifecycle](https://contributors.home-assistant.io/core/integrations/fetching-data/)
- [Config flows and unique IDs](https://contributors.home-assistant.io/core/integrations/config-flow/)
- [Reauthentication and reconfiguration](https://contributors.home-assistant.io/core/integrations/reauth-reconfigure/)
- [Light entities and effect colour modes](https://contributors.home-assistant.io/core/entities/light/)
- [Entity availability](https://contributors.home-assistant.io/core/quality-scale/entity-unavailable/)
- [Action exceptions](https://contributors.home-assistant.io/core/quality-scale/action-exceptions/)
- [Naming entities](https://contributors.home-assistant.io/core/entities/naming/)
- [Diagnostics](https://contributors.home-assistant.io/core/integrations/diagnostics/)
- [Manifest / IoT class](https://contributors.home-assistant.io/core/integrations/manifest/)


## v0.6.0 performance follow-up

Rechecked the current canonical guidance links above against supported HA 2026.10.4.
The early matching-ACK callback uses native `async_set_updated_data`; it does not
claim device readback. Both coordinator/transport locks remain held through
bounded cleanup. No new scanner, reused Bleak client, background cleanup task or
blind retry was introduced. The optional MTU-aware write policy defaults off and
retains characteristic write-response semantics and no-response pacing. Safe
relative timing fields extend the existing native diagnostics allowlist. Native
options serialization, cancellation, negative/stale ACK, partial write, cleanup
and privacy regressions are covered in tests/test_performance.py. Hardware speed
and larger-packet compatibility remain unverified; see PERFORMANCE.md.


## v0.6.1 default follow-up

Live L2 diagnostics subsequently established successful 244-byte-policy control
and fresh polling reads, with owner-reported improved responsiveness. Since
v0.6.1 MTU-aware writes default on for unset choices; explicit false remains the
conservative fallback. Setup/credential validation stays at 20 bytes. Adapter
caps, ACK gates and cleanup are unchanged. See HARDWARE-TEST.md for scope.


## v0.7.0 owned-session and CI follow-up

Dedicated HACS integration validation now runs with Python/hassfest and all three
contexts are required by main's ruleset. Only HA brands-index registration is
exempt for this custom repository. No default HACS catalog acceptance is claimed.

Optional reuse owns the same still-connected client, not a disconnected client
reused for a new connection. It defaults off. An entry-managed idle task takes
the transport lock for cleanup; explicit unload/stop closes the lease, cancels
active work and joins owned tasks. Fresh read/query/auth always close the lease
before a new connection. Every command still needs a unique matching ACK, and
uncertain commands are never replayed. Diagnostic schema4 exposes only safe
flags/categories/numbers. Physical recovery/latency evidence remains pending.
