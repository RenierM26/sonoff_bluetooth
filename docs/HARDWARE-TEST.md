# Hardware validation

One SONOFF L2/UIID 137 was tested on its original firmware through an existing
ESPHome Sonoff M5 active Bluetooth proxy running ESPHome 2026.10.0. Live Home
Assistant was 2026.10.4. The owner confirmed that the visible test sequence worked.
Household identifiers, addresses and credentials are intentionally omitted here.

| Check | Result |
| --- | --- |
| GATT service AAAA, write BBB0, notify BBB1 | Present |
| ATT MTU | 247; conservative 20-byte writes worked |
| Fresh UUID4 challenge/token authentication | Passed repeatedly |
| On/off | ACK plus subsequent device-state report matched |
| Red, brightness 30 | ACK plus subsequent state matched; visual sequence confirmed |
| Blue, brightness 60 | ACK plus subsequent state matched; visual sequence confirmed |
| Restore original yellow/mode 1/brightness 88 | Readback matched |
| Finish off | Final Bluetooth state confirmed off |
| Existing HA proxy connection restored | Verified enabled and loaded |

No L2 Wi-Fi commands, eWeLink cloud calls, firmware changes or HA restarts were
used. This is evidence of the BLE path, not an Internet-blocked test; no network
block was applied. The initial device was off, so its original colour settings
were captured after a plain on command before changing colour.

The test client had to temporarily release HA's ownership of one idle proxy;
that entry was restored in a `finally` handler. Routine integration operation
uses HA's existing Bluetooth manager and needs no such ownership handoff.

Important observation: `action:query` received a success ACK without `params`.
State reports on a subsequent authenticated connection provided readback. A
notification received during a command session could still describe the state
before that command. An ACK or an old notification alone is not fresh state proof.

The device apikey passed authentication. The same value in `selfApikey` was
accepted for this owned device. Account profile retrieval was not used; shared
devices may require a different account apikey.

Remaining checks beyond the owner trial below: long-duration reliability,
other L2 firmware, shared accounts, power cycling,
and Internet-blocked operation. Simulated peer/unit tests and HA import/config
flow tests do not replace those hardware checks.

## Initial v0.4.0 evidence boundary (superseded in part by owner trial below)

Startup/manual/polled fresh-state reads initially used the previously observed session
report path. Their new integration/coordinator implementation has simulated-peer
and native HA entity tests, not a new installed-HA hardware acceptance trial.

The opt-in 21 preset effects, per-mode speed/brightness, rhythm presets and
sensitivity are source-derived and software-tested only. Their visible output,
accepted slider ranges, and sound response with the phone/app closed still need
an attended trial. No new hardware commands were sent while implementing them.
The runtime code uses only local HA/Bluetooth control, but an Internet-blocked
trial remains separate evidence that has not yet been obtained.


## Owner live-HA validation — 2026-11-01

After installing the integration, the owner reported that effect selection,
rhythm, speed and 60-second state polling worked perfectly, then separately
confirmed reaction to sound. This is owner-reported physical/live-HA evidence,
not an inference from code, ACKs or simulated tests. The exact installed commit,
all-preset coverage and individual sensitivity/range trials were not specified.
Phone/app-closed and Internet-blocked conditions were not explicitly reported.

Based on that validation and the owner's request, v0.5.0 removes experimental
labels, defaults effects/rhythm on and defaults state polling to 60 seconds.
Explicit saved enable/disable and interval choices (including 0) are preserved.
The implementation still does not capture/stream audio, use a vendor app at
runtime, or claim HA media-player beat synchronisation. Other models/firmware,
long-run/power-cycle/recovery scenarios and blocked-Internet operation remain
outside this trial's evidence. No hardware was actuated to make the v0.5.0 change.


## v0.6.0 performance evidence boundary

The original 20-byte wire policy is unchanged by default. Matching-ACK publication
before disconnect and session timings are regression-tested with synthetic peers.
Larger MTU-aware writes remain off by default and are not hardware-validated; no
physical latency improvement is claimed. See PERFORMANCE.md for comparison and
rollback. The owner reports above do not automatically cover this new packet mode.
