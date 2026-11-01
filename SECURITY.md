# Security policy

## Supported versions

Security fixes target the latest published release and the Home Assistant version
currently supported by this project. Update older releases before reporting.

## Report privately

Use [GitHub private vulnerability reporting](https://github.com/RenierM26/sonoff_bluetooth/security/advisories/new).
Do not open public issues containing keys, tokens, device/account identifiers,
private network details, full config entries, APKs or decompiled vendor code.
Include the affected version, impact and minimal redacted reproduction steps.
Response timing is best-effort; this is a community-maintained project.

## Credentials and device protocol

Device/account keys are stored in HA's config-entry storage and masked in forms;
that storage is not an encrypted secrets vault. Keep HA backups private. Cloud
setup tokens are used only during explicitly selected import, not retained for
runtime operation. Native diagnostics use an explicit safe-field allowlist, but
check any attached file and HA-managed envelope before sharing it publicly.

The original controller requires legacy MD5-derived AES-CBC with a fixed IV.
This implementation preserves wire compatibility; it does not upgrade the
controller's cryptography. Only use credentials for devices you own or manage.
Runtime control is local Bluetooth; keep Home Assistant and proxies updated.
