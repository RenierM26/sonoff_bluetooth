# Security

Do not post eWeLink apikeys, LAN devicekeys, account credentials, access tokens,
HA config-entry exports or unredacted diagnostics in public issues or pull requests.
The export helper writes a new owner-only file; never commit that file. HA stores
integration credentials in its config entry; masked UI does not encrypt storage.

For a vulnerability, use GitHub's private vulnerability reporting when available
on this repository, rather than a public issue containing sensitive details.
For an accidentally exposed credential, remove the public copy and invalidate or
rotate the affected credential through the relevant vendor/account service.

The device protocol requires legacy MD5 key derivation and a fixed-IV AES-CBC
scheme. This integration reproduces that protocol for compatibility; it does not
claim to improve the vendor's cryptographic design. Runtime control is local, but
Bluetooth proximity alone should not be treated as an authorization boundary.
