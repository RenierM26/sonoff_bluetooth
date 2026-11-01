# Repository instructions

- This is a Home Assistant custom integration; only SONOFF L2/UIID137 is currently verified.
- Use `uv venv --python 3.13` and install requirements-dev.txt with HA 2026.2.3;
  CI additionally covers Python 3.14/HA 2026.10.4.
- Required checks: `ruff check .`, `ruff format --check .`, `mypy`, and `pytest --cov`.
- Keep keys, device identifiers, local exports, proprietary APKs and decompiled app
  source out of commits. Tests use synthetic values; no automatic hardware probes.
- HA owns scanners/adapters/proxies. Never create a competing BLE scanner or
  subscriber inside the integration. Preserve command ACK correlation and cleanup.
- Setup/reconfigure validation may authenticate but must not change light state.
  Never return stored keys as form defaults or log them on errors.
- Do not infer state from GATT write success or an ACK-only query. Keep assumed
  state until a matching ACK, and separate device reports from visual evidence.
- Preserve Apache-2.0. Keep manifest version, HACS minimum HA version, docs and
  release notes consistent; supported models must not be overstated.

- Assisted setup reads optional SonoffLAN live records or the fixed regional
  eWeLink APIs only at explicit user request. Never copy a token into entry data,
  follow cloud redirects, log remote responses, expose imported keys in forms,
  or add runtime cloud requests. Keep native minimum/current HA form serialization
  tests for every setup path and reconfiguration.
- HA-floor and optional HA-manifest dependency pins are matrix-managed; update
  them together, not via isolated Dependabot major/minor bumps.
