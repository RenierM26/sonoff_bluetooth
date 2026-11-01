# SONOFF Bluetooth for Home Assistant

[![Python CI](https://github.com/RenierM26/sonoff_bluetooth/actions/workflows/ci.yml/badge.svg)](https://github.com/RenierM26/sonoff_bluetooth/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Local Bluetooth control of a **SONOFF L2 LED strip (UIID 137)** with its original
firmware. Provides a Home Assistant RGB light with **on/off, colour and brightness**.
Currently supports L2 only; the repository name does **not** imply compatibility
with every SONOFF Bluetooth product. L2 Lite, L1 and L3 have not been verified.

The protocol has been tested on a physical L2 through an ESPHome Sonoff M5 proxy:
authentication, on/off, red/30%, blue/60%, and restoration all passed. Subsequent
Bluetooth state reports matched, and the owner confirmed the visible output.
The integration is still **experimental**; full live-HA installation, long-run,
power-cycle and Internet-blocked testing are separate remaining checks.

## How it works

```mermaid
flowchart LR
    HA[Home Assistant + SONOFF Bluetooth] -->|Local Wi-Fi / Ethernet| ESP[ESPHome active Bluetooth proxy]
    ESP -->|Authenticated Bluetooth GATT| L2[Original SONOFF L2 controller]
```

A nearby USB Bluetooth adapter on the HA host can replace the proxy. HA manages
adapter selection and existing ESPHome proxies; **do not disable your ESPHome
integration for routine use**. No custom firmware is required on the L2. There are
no eWeLink cloud calls or Wi-Fi light commands in this integration. The bridge still
uses the local network, and obtaining credentials initially may involve eWeLink.

## Requirements

- Home Assistant **2026.2.3 or newer**; CI covers 2026.2.3 and 2026.10.4.
- A SONOFF **L2 / UIID 137**, powered and advertising its Bluetooth control service.
- A connectable Bluetooth adapter or **active ESPHome Bluetooth proxy** in range.
- The strip's **Bluetooth MAC**, eWeLink **device ID**, and **device `apikey`**.
- For a shared device, possibly a separate **account `apikey`** (`selfApikey`).

An ESP32-based switch can act as a proxy if its chip and firmware support Bluetooth
and have sufficient resources. ESP8266/ESP8285 and ESP32-S2 cannot. See the
[ESPHome example](esphome/proxy.example.yaml); preserve an existing switch's board,
pins, relay logic and API credentials when merging only its Bluetooth sections.

## Installation

### HACS custom repository

This is a **custom repository**, not a claim of inclusion in the default HACS catalogue.

1. In HACS, open **⋮ → Custom repositories**.
2. Add `https://github.com/RenierM26/sonoff_bluetooth` with category **Integration**.
3. Find **SONOFF Bluetooth**, download it, and restart Home Assistant.
4. Continue with configuration below.

### Manual installation

1. Download this repository's source archive.
2. Copy `custom_components/sonoff_l2_ble` into your HA config directory's
   `custom_components/sonoff_l2_ble` directory.
3. Restart Home Assistant, then add the integration through the UI.

Do not copy the entire repository into `custom_components`. If downloading a
`sonoff_l2_ble.zip` release asset instead, extract its contents **inside** that
component directory; the asset contains the component files at its root.

## Configuration flow

**Settings → Devices & services → Add integration → SONOFF Bluetooth**

| Field | What to enter |
| --- | --- |
| Name | The device name to show in HA |
| Bluetooth MAC | The BLE address, not the Wi-Fi MAC |
| eWeLink device ID | The 10-character hexadecimal ID; often in advertised name `ck_<deviceid>` |
| Device apikey | The eWeLink device record's `apikey`, **not** its LAN `devicekey` |
| Account apikey | Optional for owned devices: blank uses the device apikey. Shared devices may need their user's separate account apikey. |

Setup verifies the address/ID format and authenticates over Bluetooth. If a known
`ck_` advertisement disagrees with the supplied device ID, setup refuses it. The
validation does **not** turn the strip on/off. It verifies the device key, not every
possible account-sharing permission. Close the eWeLink app if it is holding the
strip's sole BLE connection.

To change connection settings later: open this integration's **⋮ → Reconfigure**.
You can update its name, address, ID or credentials. Saved keys are never sent back
into the form; **blank key fields during reconfiguration keep existing keys**.
Keys are masked in the UI but are stored in HA's config entry, not encrypted at rest.

### Obtaining credentials from an existing SonoffLAN installation

SonoffLAN saves device records locally even though it does not support this BLE
control path. On a machine with read access to the HA configuration directory:

```sh
python3 scripts/extract_sonoff_keys.py \
  --config-dir /config \
  --device-id YOUR_DEVICE_ID \
  --output /config/l2.credentials.json
```

Some Apps mount that directory at `/homeassistant` instead of `/config`. Adjust the
paths accordingly. The helper reads only `.storage/sonoff/*.json`; it **does not
edit HA storage, read account passwords or log in to eWeLink**. It creates a new
owner-only file (mode 0600), refuses to overwrite an existing file, and prints no
key values. The owner-key account default needs checking for shared devices.
Read the file locally to fill in the UI, then remove your temporary export when
no longer needed. Do not commit or attach the file to an issue.

If the strip was never paired with your account or cached by SonoffLAN, this
helper cannot manufacture its key. No cloud login/commissioning flow is included.
A LAN `devicekey`, access token or OAuth secret cannot substitute for `apikey`.

## Usage and state

Use the entity like another HA RGB light:

```yaml
action: light.turn_on
target:
  entity_id: light.your_strip
data:
  rgb_color: [255, 0, 0]
  brightness_pct: 30
```

State is initially unknown and then **assumed**, reflecting the last acknowledged
HA command. IR/app changes and power cycles are not continuously synchronised.
A successful GATT write alone is not accepted as success: the integration waits
for a matching decrypted device ACK before updating HA state. A timeout can mean
the command was applied but its ACK was lost; light commands are not blindly replayed.

The L2 can report state when a fresh authenticated session opens, but query ACKs
can omit state and notifications can describe the pre-command state. The hardware
test used a subsequent connection for readback. Continuous push synchronisation
and polling are **not** implemented in the shipped light entity.

## Limitations and troubleshooting

- **Only L2/UIID 137 is verified.** Effects/music, timers, transitions, power-on
  behaviour and factory commissioning are not exposed.
- **Cannot connect:** confirm the proxy is active/connectable, the strip remains
  powered, and another phone/client is not connected. Improve proximity first.
- **Authentication fails:** check `apikey` versus `devicekey`, BLE versus Wi-Fi
  address, advertised device ID and firmware compatibility.
- **HA says unavailable in SonoffLAN:** that cloud/LAN entity is separate from this
  BLE integration; adding this integration does not replace it automatically.
- **Proxy ownership:** a standalone test process must not compete with HA for its
  proxy's single Bluetooth subscriber. Routine operation stays inside HA's manager.
- **Unreliable state:** assumed state is intentional; a query ACK is not readback.
- Do not replace switch firmware or disconnect its mains wiring to install this
  integration. This integration neither flashes devices nor changes relay settings.

## Development

```sh
uv venv --python 3.13
uv pip install -r requirements-dev.txt 'homeassistant==2026.2.3'
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov --cov-report=term-missing
```

Node.js is needed for the independent AES comparison test. CI runners include it;
it is **not** a runtime requirement for Home Assistant. All automated tests use
synthetic credentials and simulated BLE peers; no physical hardware is contacted.
CI enforces Ruff lint/format, strict mypy, pytest with branch coverage (minimum 80%),
and Home Assistant hassfest validation. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation and support

- [Protocol reconstruction](docs/PROTOCOL.md)
- [Artifact provenance](docs/PROVENANCE.json)
- [Hardware validation and remaining checks](docs/HARDWARE-TEST.md)
- [Changelog](CHANGELOG.md)
- [Report an issue](https://github.com/RenierM26/sonoff_bluetooth/issues)
- [Security policy](SECURITY.md)

No APKs, decompiled vendor code, real device identifiers or credentials are
redistributed. This is an independent community project, not an official SONOFF
or eWeLink product. Licensed under [Apache-2.0](LICENSE).
