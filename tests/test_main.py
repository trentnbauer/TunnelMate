import pytest

from app import main as main_mod
from app.cf_client import CloudflareAPIError
from app.zones import Zone


class _FakeResponse:
    status_code = 400
    text = '{"success":false,"errors":[{"code":81053,"message":"An A, AAAA, or CNAME record with that host already exists."}]}'


class FakeClientDNSConflict:
    """Reproduces the real-world crash: a DNS record for the hostname
    already exists on Cloudflare (not created by this tool)."""

    def __init__(self, *a, **kw):
        pass

    def create_tunnel(self, name):
        return {"id": "tunnel-id", "name": name, "account_tag": "acct", "tunnel_secret": "secret"}

    def list_zones(self):
        return [Zone(id="zone-1", name="example.com")]

    def create_dns_record(self, zone_id, hostname, target):
        raise CloudflareAPIError(_FakeResponse())


def test_reconcile_failure_logs_one_clean_line_and_exits(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a")
    monkeypatch.setenv("NAME", "test")
    monkeypatch.setenv("HOSTNAME_1", "app.example.com")
    monkeypatch.setenv("SERVICE_1", "http://app:80")

    monkeypatch.setattr(main_mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main_mod, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(main_mod, "CREDENTIALS_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setattr(main_mod, "CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(main_mod, "CloudflareClient", FakeClientDNSConflict)

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as exc_info:
        main_mod.main()

    assert exc_info.value.code == 1
    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(error_records) == 1
    message = error_records[0].getMessage()
    assert "already exists" in message
    assert "CloudflareAPIError" in message
    # No traceback text (e.g. "Traceback (most recent call last)") leaked
    # into the log record itself.
    assert "Traceback" not in message
