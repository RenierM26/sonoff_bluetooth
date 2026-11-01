# L2 app feature comparison and priorities

Assessed 2026-11-01 against the previously extracted eWeLink 5.29.1 UIID137
binding (module 5272), screen (8411) and its protocol (4826). This is an assessment,
not a claim that extra BLE commands have been hardware-tested. No new controls
were sent during this review. Only on/off, RGB and brightness have physical
command/readback plus owner-confirmed visible evidence; see HARDWARE-TEST.md.

## What is missing?

| Feature | Current integration | Evidence and suggested HA implementation |
| --- | --- | --- |
| On/off, RGB, brightness | Implemented | Verified on one physical L2. |
| Built-in scene/effect selection | Missing | UIID137 template exposes `recommendScene` and `ACTION_MORESCENE137_KEY`; protocol has `getSetModeParams` and `getCurrentSceneModeParams`. Map the model-specific presets to `LightEntity.effect_list`; validate every offered mode before declaring it supported. Do not reuse another strip's mode table. |
| Effect speed and effect-specific brightness | Missing | Protocol exposes speed/brightness state per scene and getters such as `getSetSpeedParams` / `getCurrentSceneModeBrightParams`. Use a number entity for speed; preserve the active mode when changing brightness instead of forcing mode 0. Ranges and firmware behaviour still need validation. |
| Music/rhythm mode and sensitivity | Missing; candidate for investigation | Bound protocol contains `getRhythmicParams`, `rhythmMode` and `rhythmSensitive` (mode 4). Trace the UIID137 music caller and audio source before promising autonomous operation: a mode/sensitivity command does not prove the controller can react without phone audio/microphone processing. Possible select/number entities once validated. |
| State refresh after IR/app changes or restart | Missing | Device state was reported on a fresh authenticated connection in the earlier physical test. A query ACK can contain no state, and pre-command notifications can be stale. Consume fresh reports deliberately; a refresh action and optional conservative polling are safer first steps than claiming continuous push sync. Keep unknown/assumed semantics until reports are validated. |
| Saved colours / favourites / custom scenes | Missing as vendor-specific management | UI/protocol include custom-colour tags and DIY-scene fields, but device-resident versus account/app storage and model/firmware applicability are not established. Use normal HA scenes/scripts for saved RGB/brightness combinations now; investigate vendor DIY effects separately. |
| White / colour-temperature-style UI | Not exposed | UIID137 screen includes CCT/W panels; protocol has mode-2 colour-temperature and mode-3 brightness helpers. This is not evidence that the RGB L2 has dedicated white channels or a physically calibrated Kelvin range. Validate the actual rendering before exposing any white emulation; do not advertise genuine CCT capability based on field names alone. |
| Schedules, countdowns, groups and automations | Use HA's native features | HA already supplies local automation, timers, scenes and light groups. Prefer these to duplicating vendor scheduling/cloud APIs. Device-resident BLE scheduling, including persistence when HA is offline, has not been established. A JavaScript UI `timer` is not proof of a device timer command. |
| Smooth transitions | Not implemented | No validated native duration/fade command in the recovered L2 control path. Software fades would add repeated BLE commands and connection overhead; lower priority than reliable effects and state. |

## Recommended order

1. **State refresh/recovery:** useful after HA startup and IR/app changes; validate
   freshness and avoid stealing a long-lived BLE connection from other clients.
2. **Preset effects**, followed by **speed and effect-preserving brightness**:
   the largest clearly identified app-control gap.
3. **Music/rhythm controls**, after resolving the microphone/audio-processing path
   and proving the desired behaviour without a phone/cloud dependency.
4. Optional virtual-white UI and vendor DIY scenes only after model-specific
   validation. Ordinary colour favourites are already covered by HA scenes.

Cloud account administration, sharing, factory Wi-Fi commissioning and firmware
updates are intentionally outside the local-Bluetooth integration's scope. This
comparison does not establish BLE support for power-on behaviour, strip hardware
parameters or every field present in the shared protocol class.

For each addition: trace the exact UIID137 caller and payload, add synthetic
codec/ACK/error tests, then perform an attended hardware test with restoration of
the original state before marking the feature supported.
