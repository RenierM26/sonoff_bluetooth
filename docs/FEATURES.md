# L2 feature support and remaining work

Assessed against extracted eWeLink 5.29.1 UIID137 binding (module 5272), screen
(8411), preset/action helpers (5273), protocol (4826), scene editor (11159) and
rhythm screen (11167). Only on/off, RGB and brightness have physical command,
readback and owner-confirmed visible evidence; see HARDWARE-TEST.md. Code and
simulated-peer tests do not replace a hardware trial.

| Feature | v0.4.0 status | Evidence / native HA behaviour |
| --- | --- | --- |
| On/off, RGB, brightness | Implemented; physically verified on one L2 | Native RGB light; matching successful ACK required. |
| Fresh state after restart / IR / app changes | Implemented; new integration path software-tested | Best-effort startup read and native Refresh state button use a fresh authenticated BLE session. Optional polling defaults OFF. No ACK-only query or old command-session report is accepted as readback. |
| Preset effects | Implemented; opt-in experimental | Exactly 21 UIID137 presets (modes 6-26), not every field in the shared protocol. Normal HA light effect picker / `light.turn_on` / scenes. Physical output still needs validation. |
| Effect speed / brightness | Implemented; opt-in experimental | Native speed number (1-100); per-mode `speedXX`/`brightXX` preserve the active effect. Commands are built inside the shared operation lock. No guessed brightness update in rhythm/unknown modes. |
| Controller rhythm mode / sensitivity | Implemented; opt-in experimental | Four discrete controller modes and sensitivity 0-100 through native HA effect/number entities. The traced screen sends device configuration, not phone audio. Autonomous sound response without a phone is **unverified**. No HA audio capture or `media_player` beat sync is implemented. |
| Colour favourites / HA scenes | Available using native HA features | Save RGB/brightness/effect combinations in HA scenes/scripts. No vendor account storage needed. |
| Vendor DIY scenes | Not implemented | DIY fields exist, but device-resident versus app/account storage and exact firmware applicability need tracing. |
| White / colour-temperature UI | Not implemented | UI/protocol have CCT/W helpers, but an RGB L2 has no demonstrated calibrated Kelvin range or dedicated white channels. Do not advertise genuine CCT based on field names. |
| Schedules, countdowns, groups | Use native HA automations/timers/groups | These require HA to be running. Device-resident BLE schedules/countdowns that persist independently of HA are not established. A JavaScript UI timer is not a device timer command. |
| Smooth transitions | Not implemented | No validated native duration/fade command. Software fades would add repeated BLE commands and connection overhead. |

## Runtime independence

After credentials are imported, the integration uses only HA's local Bluetooth
adapters/proxies and the L2. No SonoffLAN runtime object, account token, cloud
refresh, vendor app/service or audio feed is needed by the control code. Rhythm
means native HA controls **configure the controller**; it does not mean the
integration analyses music. Leave experimental controls disabled until their
hardware behaviour is validated if you need verified functions only.

## Next validation priorities

1. Installed-HA acceptance for startup/button/polled state freshness and recovery.
2. Attended validation of effect mode IDs, output, speed and per-mode brightness.
3. Controller rhythm response with the vendor app closed and no phone audio
   processing, followed by an Internet-blocked trial. Until then do not claim
   autonomous music response or offline hardware proof.
4. Only then consider virtual white, vendor DIY scenes or transitions. Native HA
   scenes/automations already cover most saved-colour/scheduling needs.

Cloud administration/sharing, factory Wi-Fi commissioning, firmware updates and
unverified strip hardware settings are intentionally outside the local BLE scope.
For every new device behaviour, trace the exact model caller/payload, add codec,
ACK/error tests, and perform an attended test restoring the original state before
marking the behaviour physically supported.
