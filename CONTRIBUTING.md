# Contributing

Use Python 3.14 with HA 2026.10.4 (latest stable checked 2026-11-01). Only the
latest stable HA release is supported; do not add older-version shims. Follow the README
for the uv environment and local check commands. Optional pre-commit hooks run
Ruff and JSON/YAML/whitespace checks; install pre-commit separately if desired.

Before a PR, run Ruff lint and format checks, strict mypy, and pytest with coverage.
CI repeats those checks on the current supported HA and validates metadata using hassfest.
Tests must use synthetic credentials, and must not scan/connect to actual hardware.

Preserve authenticated framing, bounded waits, serialized per-device commands,
matching ACK correlation, cleanup on cancellation, and unknown/assumed entity state
unless real readback is implemented and tested. A query ACK without params is not
current state. Do not add a private scanner or compete with HA's proxy connection.

Add tests for new behaviour and failure paths, especially configuration flow and
credential handling. Include reproduction steps and distinguish source-derived,
simulated, device-reported and visually confirmed findings. New model support needs
an exact model/UIID binding and physical evidence, not generic UUID guesses.

The repository retains Apache-2.0 licensing. Do not submit APKs, decompiled app
source, authentication tokens, keys, private device addresses or household backups.

## Dependency updates

Dependabot tracks tool and action releases. HA is pinned to the latest stable
release verified by this project. When a new stable HA is available, update its
pin, HACS minimum, README, and optional USB/Supervisor dependency pins together.
Those optional dependencies mirror the corresponding HA integration manifests,
not necessarily each package's newest release. Keep native frontend serialization
tests for every setup path and reconfiguration. No older-version matrix is needed.

## Releases

Update `custom_components/sonoff_l2_ble/manifest.json` and CHANGELOG.md together.
After the checks pass, a maintainer can push a matching `v<version>` tag. The release
workflow reruns validation, checks the manifest/tag match, builds a component-only
ZIP and publishes it as a GitHub Release asset. This initial repository bootstrap
does not itself create or publish a release tag.
