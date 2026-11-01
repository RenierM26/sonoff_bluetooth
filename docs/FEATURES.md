# L2 feature support and remaining work

Assessed against extracted eWeLink 5.29.1 UIID137 binding (module 5272), screen
(8411), preset/action helpers (5273), protocol (4826), scene editor (11159) and
rhythm screen (11167). Basic controls have command/readback and owner-confirmed
visible evidence. The owner subsequently tested the installed HA integration:
effects, rhythm, speed, sound reaction and 60-second polling worked on the L2.
See HARDWARE-TEST.md for scope; simulated peers alone are not hardware evidence.

| Feature | Current status | Evidence / native HA behaviour |
| --- | --- | --- |
| On/off, RGB, brightness | Implemented; physically verified on one L2 | Native RGB light; matching successful ACK required. |
| Fresh state after restart / IR / app changes | Implemented; 60-second polling owner-tested | Best-effort startup read and native Refresh state button use a fresh authenticated BLE session. Polling defaults to 60 seconds; 0 disables it. Startup/restart and external-change scenarios are not individually certified by this report. No ACK-only query or old command-session report is accepted as readback. Bluetooth absence is unavailable; rediscovery/missing fields are unknown, not stale state. |
| Preset effects | Implemented; enabled by default | Exactly 21 UIID137 presets (modes 6-26), not every field in the shared protocol. Normal HA light effect picker / `light.turn_on` / scenes. Owner reports effects work; not an exhaustive test of all 21 presets. |
| Effect speed / brightness | Implemented; enabled by default | Owner reports speed works. Native speed number (1-100); per-mode `speedXX`/`brightXX` preserve the active effect. Commands are built inside the shared operation lock. No guessed brightness update in rhythm/unknown modes. |
| Controller rhythm mode / sensitivity | Implemented; enabled by default | Four discrete controller modes and sensitivity 0-100 through native HA effect/number entities. The traced screen sends device configuration, not phone audio. Owner reports rhythm reacts to sound. Sensitivity adjustment and every mode/range were not individually reported. No HA audio capture or `media_player` beat sync is implemented. |
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
integration analyses music. The owner has confirmed sound reaction on the tested
L2. An Internet-blocked trial remains a separate outstanding check.

## Next validation priorities

1. Long-run, power-cycle, startup/manual-refresh/recovery and external-change
   readback scenarios beyond the owner-confirmed 60-second polling trial.
2. Exhaustive preset coverage, sensitivity adjustment and per-mode slider ranges.
3. Internet-blocked operation and explicit phone/app-closed test conditions; the
   sound-reaction report does not specify these conditions.
4. Other firmware/shared-account coverage before broader compatibility claims.
   Native HA scenes/automations already cover most saved-colour/scheduling needs.

Cloud administration/sharing, factory Wi-Fi commissioning, firmware updates and
unverified strip hardware settings are intentionally outside the local BLE scope.
For every new device behaviour, trace the exact model caller/payload, add codec,
ACK/error tests, and perform an attended test restoring the original state before
marking the behaviour physically supported.
