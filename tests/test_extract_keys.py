"""Exercise private export using synthetic cache files only."""

import json
import stat

import pytest

from scripts.extract_sonoff_keys import extract


def cache(tmp_path, devices):
    folder = tmp_path / ".storage" / "sonoff"
    folder.mkdir(parents=True)
    (folder / "account.json").write_text(json.dumps({"data": devices}))


def test_export_is_private_and_leaves_cache_unchanged(tmp_path):
    cache(
        tmp_path,
        [{"deviceid": "1000000000", "apikey": "synthetic-key", "devicekey": "not-the-ble-key"}],
    )
    source = tmp_path / ".storage" / "sonoff" / "account.json"
    before = source.read_bytes()
    output = tmp_path / "export.credentials.json"
    extract(tmp_path, "1000000000", output)
    data = json.loads(output.read_text())
    assert data["device_apikey"] == "synthetic-key" and "devicekey" not in data
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert source.read_bytes() == before
    with pytest.raises(FileExistsError):
        extract(tmp_path, "1000000000", output)


def test_missing_or_conflicting_cache_does_not_write_output(tmp_path):
    cache(
        tmp_path,
        [
            {"deviceid": "1000000000", "apikey": "first"},
            {"deviceid": "1000000000", "apikey": "other"},
        ],
    )
    output = tmp_path / "export.credentials.json"
    with pytest.raises(ValueError):
        extract(tmp_path, "1000000000", output)
    assert not output.exists()
