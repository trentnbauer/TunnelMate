import pytest

from app import main as main_mod
from app.cf_client import CloudflareAPIError
from app.config import HostnameConfig
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


class TestRoutingTableLines:
    def test_empty_routes_produces_no_box(self):
        assert main_mod._routing_table_lines([]) == []

    def test_box_is_bordered_and_contains_each_route(self):
        routes = [
            HostnameConfig(1, "app.example.com", None, "http://app:3000", ()),
            HostnameConfig(2, "admin.example.com", None, "http://admin:8080", ("a@example.com",)),
        ]
        lines = main_mod._routing_table_lines(routes)

        assert lines[0] == lines[2] == lines[-1]  # top border, title separator, bottom border match
        assert lines[0].startswith("+") and lines[0].endswith("+")
        assert "TUNNELMATE ROUTES" in lines[1]
        assert any("https://app.example.com" in row and "http://app:3000" in row for row in lines)
        assert any("https://admin.example.com" in row and "http://admin:8080" in row for row in lines)
        # Every line (including content rows) is the same width -- a
        # ragged box reads as broken, not just ugly.
        assert len({len(row) for row in lines}) == 1
