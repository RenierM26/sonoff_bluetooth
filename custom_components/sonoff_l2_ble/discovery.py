"""Setup-only credential import and HA-owned Bluetooth discovery.

SonoffLAN's live registry is an optional, private API. Never read its config entry
password, mutate its registry, or open a second cloud/WebSocket session for import.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError, ClientTimeout
from homeassistant.components import bluetooth
from homeassistant.helpers.aiohttp_client import async_get_clientsession

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

REGIONS = {
    "cn": "https://cn-apia.coolkit.cn",
    "as": "https://as-apia.coolkit.cc",
    "us": "https://us-apia.coolkit.cc",
    "eu": "https://eu-apia.coolkit.cc",
}
# Public application identifier used by SonoffLAN; not an application secret.
SONOFFLAN_APP_ID = "R8Oq3y0eSZSYdKccHlrQzT1ACCOUT9Gv"


class ImportFailed(Exception):
    """No sensitive remote response or token belongs in the exception text."""


class InvalidToken(ImportFailed):
    """Token is malformed, rejected or expired."""


@dataclass(frozen=True)
class Candidate:
    """Only ID/name may be exposed in selection forms."""

    device_id: str
    name: str
    device_apikey: str = field(repr=False)
    account_apikey: str = field(repr=False)


def candidates_from_records(records: Iterable[Any], account_key: str) -> list[Candidate]:
    """Accept only well-formed L2/UIID137 records; reject conflicting credentials."""
    found: dict[str, Candidate] = {}
    conflicts: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        extra = record.get("extra")
        if not isinstance(extra, Mapping) or str(extra.get("uiid")) != "137":
            continue
        device_id = record.get("deviceid")
        key = record.get("apikey")
        if (
            not isinstance(device_id, str)
            or not re.fullmatch(r"[0-9a-fA-F]{10}", device_id)
            or not isinstance(key, str)
            or not key.strip()
        ):
            continue
        device_id = device_id.lower()
        candidate = Candidate(
            device_id, str(record.get("name") or "SONOFF L2"), key, account_key or key
        )
        if device_id in found and (
            found[device_id].device_apikey != candidate.device_apikey
            or found[device_id].account_apikey != candidate.account_apikey
        ):
            conflicts.add(device_id)
        found[device_id] = candidate
    return [found[key] for key in sorted(found) if key not in conflicts]


def sonofflan_candidates(hass: HomeAssistant) -> list[Candidate]:
    """Snapshot an already loaded SonoffLAN registry without cloud requests."""
    registries = hass.data.get("sonoff", {})
    if not isinstance(registries, Mapping):
        return []
    candidates: list[Candidate] = []
    for registry in registries.values():
        devices = getattr(registry, "devices", None)
        if not isinstance(devices, Mapping):
            continue
        auth = getattr(getattr(registry, "cloud", None), "auth", None)
        user = auth.get("user", {}) if isinstance(auth, Mapping) else {}
        account_key = user.get("apikey", "") if isinstance(user, Mapping) else ""
        candidates.extend(
            candidates_from_records(
                devices.values(), account_key if isinstance(account_key, str) else ""
            )
        )
    # Keep accounts separate: a shared device can have different selfApikey values.
    return list(dict.fromkeys(candidates))


def bluetooth_addresses(hass: HomeAssistant, device_id: str) -> list[str]:
    """Use only advertisements already collected by connectable HA adapters."""
    expected = f"ck_{device_id}".lower()
    return sorted(
        {
            info.address.upper()
            for info in bluetooth.async_discovered_service_info(hass, connectable=True)
            if info.name.lower() == expected
        }
    )


async def token_candidates(hass: HomeAssistant, supplied_token: str) -> list[Candidate]:
    """Read profile and all homes using a region-prefixed SonoffLAN bearer token.

    No login, refresh, WebSocket or device commands. Token lives only in this call;
    fixed HTTPS hosts and disabled redirects prevent forwarding it elsewhere.
    """
    region, separator, token = supplied_token.strip().partition(":")
    if not separator or region not in REGIONS or not token or any(c.isspace() for c in token):
        raise InvalidToken
    host = REGIONS[region]
    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {token}", "X-CK-Appid": SONOFFLAN_APP_ID}

    async def request(path: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
        try:
            async with session.get(
                host + path,
                headers=headers,
                params=params,
                timeout=ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise InvalidToken
                if response.status != 200:
                    raise ImportFailed
                body = await response.json()
        except (ClientError, TimeoutError, ValueError):
            raise ImportFailed from None
        if not isinstance(body, dict):
            raise ImportFailed
        if body.get("error") != 0:
            if path == "/v2/user/profile" or body.get("error") in (10001, 10002, 10003):
                raise InvalidToken
            raise ImportFailed
        data = body.get("data")
        if not isinstance(data, dict):
            raise ImportFailed
        return data

    profile = await request("/v2/user/profile")
    user = profile.get("user")
    account_key = user.get("apikey") if isinstance(user, dict) else None
    if not isinstance(account_key, str) or not account_key:
        raise ImportFailed
    families = (await request("/v2/family")).get("familyList")
    if not isinstance(families, list):
        raise ImportFailed
    home_ids = {
        str(family["id"]) for family in families if isinstance(family, dict) and family.get("id")
    }
    records: list[Any] = []
    for home_id in sorted(home_ids) or [""]:
        params: dict[str, str | int] = {"num": 0}
        if home_id:
            params["familyid"] = home_id
        things = (await request("/v2/device/thing", params)).get("thingList")
        if not isinstance(things, list):
            raise ImportFailed
        records.extend(item.get("itemData") for item in things if isinstance(item, dict))
    return candidates_from_records(records, account_key)
