# Changelog

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
