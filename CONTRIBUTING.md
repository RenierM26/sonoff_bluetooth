# Contributing

## Scope and evidence

Currently supported: SONOFF L2/UIID 137 with original firmware, using Home
Assistant's Bluetooth adapters or active ESPHome proxies. Other SONOFF models
need their own protocol and hardware evidence; see [hardware validation](docs/HARDWARE-TEST.md).
Do not upload vendor APKs/decompiled code, credentials, config/cache exports,
device identifiers or unredacted diagnostics. Tests use synthetic credentials.

## Development

Target the latest supported stable Home Assistant and Python 3.14; the exact
supported version and dependency pins live in `requirements-dev.txt` and `hacs.json`.

```sh
uv venv --python 3.14
uv pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov
```

Use a feature branch and pull request. Both Python CI and Home Assistant manifest
validation must pass against current main, and review threads must be resolved.
Squash merging keeps main linear; merged branches are deleted automatically.
Reviewer approvals are optional for this single-maintainer project, not a way to
skip CI. Do not push directly to main or rewrite published version tags.

Preserve entity unique IDs and explicit saved options. Keep HA in charge of
Bluetooth scanning, serialize device operations and never equate ACKs with fresh
state or visible output. Setup validation must not change the light. Changes to
device behaviour need meaningful synthetic tests and separately documented
attended hardware evidence. See [repository instructions](AGENTS.md).

## Reports

Use the issue forms for bugs or feature requests. For bugs, include exact versions,
controller/proxy details, reproduction steps and redacted native HA diagnostics.
Use [private vulnerability reporting](SECURITY.md) for security problems.

## Releases (maintainer)

1. Prepare a PR with the manifest version, `CHANGELOG.md` and relevant docs updated.
2. Merge only after both required checks pass. Use the green main commit for release.
3. Create an annotated `vX.Y.Z` tag matching the manifest, then push it:
   `git tag -a vX.Y.Z <commit> -m "SONOFF Bluetooth vX.Y.Z"` and
   `git push origin vX.Y.Z`.
4. The Package release workflow repeats CI, checks tag/version consistency and
   publishes `sonoff_l2_ble.zip` plus `SHA256SUMS`. Verify the workflow, release
   assets and notes before announcing the release.

Published version tags are protected against rewriting/deletion. Fix mistakes
with a new version rather than moving tags. HACS uses the release ZIP; its files
are at the archive root for extraction into `custom_components/sonoff_l2_ble/`.
