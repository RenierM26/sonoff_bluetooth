# Contributing

Use Python 3.13 with HA 2026.2.3 or Python 3.14 with HA 2026.10.4. Follow the README
for the uv environment and local check commands. Optional pre-commit hooks run
Ruff and JSON/YAML/whitespace checks; install pre-commit separately if desired.

Before a PR, run Ruff lint and format checks, strict mypy, and pytest with coverage.
CI repeats those checks on both HA versions and validates metadata using hassfest.
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

Dependabot tracks tool and action releases. The `homeassistant` requirement states
our supported floor; current HA is already tested explicitly in the CI matrix.
Optional USB/Supervisor packages in requirements-dev.txt mirror the matching HA
integration manifests. Do not independently bump those pins: update them when the
HA matrix moves, and verify both environments. Mypy and other development tools
can be updated after both matrix checks pass.

## Releases

Update `custom_components/sonoff_l2_ble/manifest.json` and CHANGELOG.md together.
After the checks pass, a maintainer can push a matching `v<version>` tag. The release
workflow reruns validation, checks the manifest/tag match, builds a component-only
ZIP and publishes it as a GitHub Release asset. This initial repository bootstrap
does not itself create or publish a release tag.
