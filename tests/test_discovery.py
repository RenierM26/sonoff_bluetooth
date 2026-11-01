"""Credential import uses synthetic records and never a real cloud account."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import ClientError
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView

from custom_components.sonoff_l2_ble.config_flow import L2ConfigFlow
from custom_components.sonoff_l2_ble.discovery import (
    Candidate,
    ImportFailed,
    InvalidToken,
    bluetooth_addresses,
    candidates_from_records,
    sonofflan_candidates,
    token_candidates,
)

RECORD = {
    "deviceid": "1000000000",
    "name": "Synthetic strip",
    "apikey": "synthetic-device-secret",
    "extra": {"uiid": 137},
}
ACCOUNT = "synthetic-account-secret"
CANDIDATE = Candidate(RECORD["deviceid"], RECORD["name"], RECORD["apikey"], ACCOUNT)
ADDRESS = "AA:BB:CC:DD:EE:FF"
MODULE = "custom_components.sonoff_l2_ble"


def flow():
    instance = L2ConfigFlow()
    instance.hass = Mock(data={})
    instance.async_set_unique_id = AsyncMock()
    instance._abort_if_unique_id_configured = Mock()
    return instance


def frontend(form):
    serialized = FlowManagerIndexView(Mock())._prepare_result_json(form)
    assert RECORD["apikey"] not in json.dumps(serialized)
    assert ACCOUNT not in json.dumps(serialized)
    return serialized


def test_records_filter_models_and_invalid_records():
    invalid = [
        None,
        {},
        {**RECORD, "extra": {}},
        {**RECORD, "deviceid": "bad"},
        {**RECORD, "apikey": ""},
        {**RECORD, "extra": {"uiid": 1}},
    ]
    assert candidates_from_records([*invalid, RECORD, RECORD], ACCOUNT) == [CANDIDATE]
    assert RECORD["apikey"] not in repr(CANDIDATE)
    assert ACCOUNT not in repr(CANDIDATE)
    assert candidates_from_records([RECORD], "")[0].account_apikey == RECORD["apikey"]
    assert candidates_from_records([RECORD, {**RECORD, "apikey": "conflict"}], ACCOUNT) == []


def test_import_snapshots_live_registry_without_mutation_or_login():
    registry = SimpleNamespace(
        devices={RECORD["deviceid"]: dict(RECORD)},
        cloud=SimpleNamespace(auth={"user": {"apikey": ACCOUNT}}),
    )
    hass = Mock(data={"sonoff": {"entry": registry, "other": object()}})
    assert sonofflan_candidates(hass) == [CANDIDATE]
    assert registry.devices[RECORD["deviceid"]] == RECORD
    registry.cloud.auth = None
    assert sonofflan_candidates(hass)[0].account_apikey == RECORD["apikey"]
    assert sonofflan_candidates(Mock(data={"sonoff": []})) == []
    assert sonofflan_candidates(Mock(data={})) == []


def test_bluetooth_match_requires_exact_device_name_and_connectable_adapters():
    advertisements = [
        SimpleNamespace(name="CK_1000000000", address=ADDRESS.lower()),
        SimpleNamespace(name="ck_1000000001", address="wrong"),
        SimpleNamespace(name="ck_1000000000_extra", address="wrong"),
    ]
    with patch(
        f"{MODULE}.discovery.bluetooth.async_discovered_service_info", return_value=advertisements
    ) as scan:
        hass = Mock()
        assert bluetooth_addresses(hass, RECORD["deviceid"]) == [ADDRESS]
    scan.assert_called_once_with(hass, connectable=True)


def cloud_session(responses):
    session = Mock()
    contexts = []
    for status, body in responses:
        response = SimpleNamespace(status=status, json=AsyncMock(return_value=body))
        context = Mock(
            __aenter__=AsyncMock(return_value=response), __aexit__=AsyncMock(return_value=False)
        )
        contexts.append(context)
    session.get.side_effect = contexts
    return session


def ok(data):
    return 200, {"error": 0, "data": data}


async def test_token_fetches_profile_and_all_homes_with_safe_request_policy():
    session = cloud_session(
        [
            ok({"user": {"apikey": ACCOUNT}}),
            ok({"familyList": [{"id": "home-b"}, {"id": "home-a"}]}),
            ok({"thingList": [{"itemData": RECORD}]}),
            ok({"thingList": [{"itemData": RECORD}, {"itemData": {"group": True}}]}),
        ]
    )
    with patch(f"{MODULE}.discovery.async_get_clientsession", return_value=session):
        assert await token_candidates(Mock(), "eu:synthetic-token") == [CANDIDATE]
    assert len(session.get.call_args_list) == 4
    assert session.get.call_args_list[2].kwargs["params"] == {"num": 0, "familyid": "home-a"}
    assert session.get.call_args_list[3].kwargs["params"]["familyid"] == "home-b"
    for call in session.get.call_args_list:
        assert call.args[0].startswith("https://eu-apia.coolkit.cc/v2/")
        assert call.kwargs["allow_redirects"] is False
        assert call.kwargs["timeout"].total == 10
        assert call.kwargs["headers"]["Authorization"] == "Bearer synthetic-token"


async def test_empty_home_list_fetches_default_device_list():
    session = cloud_session(
        [ok({"user": {"apikey": ACCOUNT}}), ok({"familyList": []}), ok({"thingList": []})]
    )
    with patch(f"{MODULE}.discovery.async_get_clientsession", return_value=session):
        assert await token_candidates(Mock(), "us:synthetic-token") == []
    assert session.get.call_args.kwargs["params"] == {"num": 0}


@pytest.mark.parametrize(
    "token", ["", "secret", "https://attacker:token", "eu:", "eu:bad token", "xx:token"]
)
async def test_invalid_token_never_contacts_network(token):
    with (
        patch(f"{MODULE}.discovery.async_get_clientsession") as session,
        pytest.raises(InvalidToken),
    ):
        await token_candidates(Mock(), token)
    session.assert_not_called()


@pytest.mark.parametrize(
    "response,error",
    [
        ((401, {}), InvalidToken),
        ((403, {}), InvalidToken),
        ((302, {}), ImportFailed),
        ((500, {}), ImportFailed),
        ((200, []), ImportFailed),
        ((200, {"error": 10001, "msg": "secret"}), InvalidToken),
        ((200, {"error": 0, "data": []}), ImportFailed),
        (ok({"user": {}}), ImportFailed),
    ],
)
async def test_cloud_errors_do_not_echo_response_or_credentials(response, error):
    session = cloud_session([response])
    with (
        patch(f"{MODULE}.discovery.async_get_clientsession", return_value=session),
        pytest.raises(error) as exc,
    ):
        await token_candidates(Mock(), "eu:synthetic-token")
    assert "secret" not in str(exc.value)
    assert "synthetic-token" not in str(exc.value)


async def test_network_failure_is_sanitized():
    session = Mock()
    session.get.side_effect = ClientError("synthetic-token")
    with (
        patch(f"{MODULE}.discovery.async_get_clientsession", return_value=session),
        pytest.raises(ImportFailed) as exc,
    ):
        await token_candidates(Mock(), "eu:synthetic-token")
    assert not str(exc.value)


async def test_menu_and_import_form_serialize_without_credentials():
    instance = flow()
    menu = await instance.async_step_user()
    assert menu["menu_options"] == ["sonofflan", "token", "manual"]
    with patch(f"{MODULE}.config_flow.sonofflan_candidates", return_value=[CANDIDATE]):
        form = await instance.async_step_sonofflan()
    assert frontend(form)["data_schema"][0]["selector"]["select"]["options"][0]["label"].startswith(
        RECORD["name"]
    )
    token = frontend(await instance.async_step_token())
    assert token["data_schema"][0]["selector"]["text"]["type"] == "password"


async def test_import_select_discover_confirm_and_only_authenticate():
    instance = flow()
    with patch(f"{MODULE}.config_flow.sonofflan_candidates", return_value=[CANDIDATE]):
        await instance.async_step_sonofflan()
    device = SimpleNamespace(authenticate=AsyncMock(), send_update=AsyncMock())
    with patch(f"{MODULE}.config_flow.bluetooth_addresses", return_value=[ADDRESS]):
        form = await instance.async_step_sonofflan({"device": "0"})
        encoded = frontend(form)
        assert encoded["data_schema"][1]["default"] == ADDRESS
        with patch(f"{MODULE}.config_flow.make_transport", return_value=device):
            result = await instance.async_step_confirm({"name": "Imported", "address": ADDRESS})
    assert result["type"] == "create_entry"
    assert result["data"]["device_apikey"] == RECORD["apikey"]
    assert result["data"]["account_apikey"] == ACCOUNT
    assert "token" not in result["data"]
    assert instance._candidates == [] and instance._selected is None
    device.authenticate.assert_awaited_once()
    device.send_update.assert_not_called()


@pytest.mark.parametrize("addresses", [[], [ADDRESS, "11:22:33:44:55:66"]])
async def test_no_or_ambiguous_bluetooth_match_needs_manual_address(addresses):
    instance = flow()
    instance._selected = CANDIDATE
    with patch(f"{MODULE}.config_flow.bluetooth_addresses", return_value=addresses):
        form = frontend(await instance.async_step_confirm())
    assert form["data_schema"][1]["default"] == ""


async def test_confirm_retries_without_exposing_imported_secrets():
    instance = flow()
    instance._selected = CANDIDATE
    with patch(f"{MODULE}.config_flow.bluetooth_addresses", return_value=[]):
        form = await instance.async_step_confirm({"address": "bad"})
    assert form["step_id"] == "confirm"
    assert form["errors"] == {"base": "invalid_address"}
    frontend(form)


async def test_missing_and_stale_selection_fail_cleanly():
    instance = flow()
    assert (await instance.async_step_sonofflan())["reason"] == "no_devices"
    assert (await instance.async_step_confirm())["reason"] == "no_devices"
    instance._candidates = [CANDIDATE]
    form = await instance.async_step_device({"device": "-1"})
    assert form["errors"] == {"base": "invalid_device"}


@pytest.mark.parametrize(
    "exception,error", [(InvalidToken(), "invalid_token"), (ImportFailed(), "cloud_failed")]
)
async def test_token_form_error_does_not_echo_token(exception, error):
    instance = flow()
    with patch(f"{MODULE}.config_flow.token_candidates", side_effect=exception):
        result = await instance.async_step_token({"token": "eu:synthetic-token"})
    assert result["errors"] == {"base": error}
    assert "synthetic-token" not in json.dumps(frontend(result))


async def test_token_selection_does_not_retain_token():
    instance = flow()
    with patch(f"{MODULE}.config_flow.token_candidates", return_value=[CANDIDATE]):
        result = await instance.async_step_token({"token": "eu:synthetic-token"})
    assert result["step_id"] == "device"
    assert "synthetic-token" not in repr(vars(instance))
    frontend(result)


async def test_reconfigure_form_native_serialization_has_no_saved_keys():
    instance = flow()
    instance._get_reconfigure_entry = Mock(
        return_value=SimpleNamespace(
            data={
                "name": CANDIDATE.name,
                "address": ADDRESS,
                "device_id": CANDIDATE.device_id,
                "device_apikey": CANDIDATE.device_apikey,
                "account_apikey": CANDIDATE.account_apikey,
            }
        )
    )
    frontend(await instance.async_step_reconfigure())


@pytest.mark.parametrize("bad_data", [{}, {"familyList": "invalid"}])
async def test_invalid_family_response_aborts_import(bad_data):
    session = cloud_session([ok({"user": {"apikey": ACCOUNT}}), ok(bad_data)])
    with (
        patch(f"{MODULE}.discovery.async_get_clientsession", return_value=session),
        pytest.raises(ImportFailed),
    ):
        await token_candidates(Mock(), "eu:synthetic-token")


@pytest.mark.parametrize(
    "response,error",
    [
        (ok({"thingList": None}), ImportFailed),
        ((200, {"error": 12345}), ImportFailed),
        ((200, {"error": 10002}), InvalidToken),
    ],
)
async def test_invalid_device_response_aborts_import(response, error):
    session = cloud_session([ok({"user": {"apikey": ACCOUNT}}), ok({"familyList": []}), response])
    with (
        patch(f"{MODULE}.discovery.async_get_clientsession", return_value=session),
        pytest.raises(error),
    ):
        await token_candidates(Mock(), "eu:synthetic-token")
