# L2 BLE protocol recovered from eWeLink 5.29.1

## Evidence and scope

Analysis date: 2026-11-01. Package `com.coolkit`, version `5.29.1`, code `2026091016`.
This is the newest publicly downloadable build located during this investigation,
not a guarantee about staged Google Play updates in every region.

Official download `https://app.coolkit.cn/coolkit.apk` delivered **5.28.1**.
APKPure's latest-download endpoint delivered a **5.29.1 XAPK** containing the base
APK and ABI/language/density splits. Android signature verification succeeded for
both base APKs; their signing-certificate SHA-256 matches:

`3a07f60d4cb6ff54f2a2377a014db7b14b966057ce6e98cee007d29ad6684b62`

The relevant app logic is in **Hermes bytecode v84** (`assets/index.android.bundle`),
not just Java/Kotlin DEX. Decompiled with P1sec/hermes-dec. Decompiled pseudocode alone is not runtime proof. A later physical-device trial
verified authentication, commands and subsequent BLE state reports; see HARDWARE-TEST.md.

## Model binding

SonoffLAN's device table identifies L2 as **UIID 137**. In eWeLink 5.29.1:

- module **5272** registers `template_uiid_137` and the UIID137 light-belt screen;
  its protocol creator imports dependency index 15, module **4826**.
- module **4826** implements `RHYTHMIC_LIGHT_BELT_NEW_PROTOCOL`, including BLE
  framing, encryption, responses, RGB, brightness and switching.
- module **8411** is the UIID137 screen and uses BLEManager (module **2189**).

Other similarly named protocol classes exist (e.g. modules 4824/5589); do not select
one purely by its JavaScript class name. The UIID137 template binding is important.

## GATT control endpoint

| Item | UUID |
| --- | --- |
| Control service | `0000aaaa-0000-1000-8000-00805f9b34fb` |
| Write command characteristic | `0000bbb0-0000-1000-8000-00805f9b34fb` |
| Reply/notification characteristic | `0000bbb1-0000-1000-8000-00805f9b34fb` |

Source: module 2189, connection setup and `sendData` (Android uses write with
response). The app also tries other notification characteristics for other paths;
the minimal implementation subscribes only to the control reply characteristic.
Only BBB0/BBB1 exist on the tested L2 control service; that minimal endpoint was verified.

**Do not confuse the control service AAAA with service BBB0.** The app's Java
`BLEPairModule` uses BBB0/BBB1 for provisioning, which is a separate protocol.
A guessed FFF0/FFF1 or a single raw on/off byte is not supported by this evidence.

## Frames and encryption

```
command: uint16 little-endian
payload length: uint16 little-endian (ASCII Base64 bytes, not decoded ciphertext)
payload: bytes
```

Split writes into ATT-sized chunks (the prototype uses 20 bytes). Notifications can
be fragmented, so buffer and reassemble by length. No per-chunk checksum/header was
found in this control path.

All encrypted payloads use:

- AES-128-CBC, PKCS#7 padding.
- key = **raw 16-byte MD5(device.apikey)**, not ASCII MD5 hex and not `devicekey`.
- IV = UTF-8 `0000000000000000` (16 ASCII zero characters, **not zero bytes**).
- UTF-8 plaintext → AES ciphertext → Base64 text → ASCII payload bytes.

Source: module **2192** `encodeCryptData`/`decodeCryptData` and module **4826**
`parseBleCommod`/`disposeBLEResponse`; module **1278** supplies little-endian helpers.

## Authentication per connection

| Step | Command | Payload |
| --- | ---: | --- |
| App challenge | `0x0000` | Encrypt a fresh app-generated UUID-like string |
| Device challenge | `0x0001` | Decrypt to get `device_code` |
| App token | `0x0002` | Encrypt `app_code + "_" + device_code` |
| Device auth result | `0x0003` | First unencrypted payload byte `0x00` means accepted |

Sources: module **2189** `_authBlePeripheral`; **2190** AuthRequestCommod;
**2191** AuthResponse; **2193** AuthTokenRequest; **2194** AuthTokenResponse.
The prototype uses a standard UUID4 app code; the app uses its own UUID-like
random-string generator. A standard UUID4 was accepted in the physical-device authentication trial.

## Light control

Command **0x000f** carries encrypted JSON. Command **0x0010** carries encrypted
JSON responses and unsolicited state messages. Example plaintext request:

```json
{
  "sequence": "1793547000000",
  "userAgent": "app",
  "action": "update",
  "deviceid": "YOUR_DEVICE_ID",
  "apikey": "YOUR_DEVICE_APIKEY",
  "selfApikey": "YOUR_ACCOUNT_APIKEY",
  "params": {"switch": "on"}
}
```

Sources: module **1182** WsRequest adds sequence/userAgent; **2080** UpdateWsRequest
adds action/deviceid/apikey/selfApikey/params; **2071** handleBleCtlOperator passes
this envelope to the device protocol; **4826** encrypts and frames the entire object.

Recovered params (module 4826):

| Action | `params` |
| --- | --- |
| On | `{"switch":"on"}` |
| Off | `{"switch":"off"}` |
| RGB colour wheel, red | `{"mode":0,"colorR":255,"colorG":0,"colorB":0}` |
| Colour-wheel brightness 50% | `{"mode":0,"bright":50}` |
| RGB disk selection | `{"mode":1,"colorR":255,"colorG":0,"colorB":0,"bright":50}` |

The prototype combines switch/on with colour-wheel mode 0 for HA RGB/brightness.
The combined mode-0 command was acknowledged and matched subsequent device state
for red/30% and blue/60%. The owner also confirmed the visible test sequence. Do not substitute the L1's mode semantics.

A successful control ACK must have the matching `sequence` and `error: 0` after
decryption. Do not treat a successful GATT write, unrelated notification or old ACK
as proof that the requested light operation succeeded. Even an ACK does not prove
visible output without a physical check.

## External references

- eWeLink app page (official Android download link): https://ewelink.cc/app/
- Public newer build listing: https://apkpure.com/ewelink-smart-home/com.coolkit
- SONOFF L2 Bluetooth control documentation: https://sonoff.tech/blogs/news/three-control-ways-of-sonoff-l2-wi-fi-smart-led-light-strip
- L2/UIID table: https://github.com/AlexxIT/SonoffLAN/blob/master/DEVICES.md
- SonoffLAN transport scope: https://github.com/AlexxIT/SonoffLAN
- HA Bluetooth development guidance: https://developers.home-assistant.io/docs/bluetooth/
- ESPHome active proxy: https://esphome.io/components/bluetooth_proxy/
- Hermes bytecode tooling: https://github.com/P1sec/hermes-dec
