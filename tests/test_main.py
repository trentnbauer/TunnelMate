import logging
import signal

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


class TestWaitUntilReady:
    def test_returns_true_once_check_ready_succeeds(self):
        calls = []

        def check_ready():
            calls.append(1)
            return len(calls) >= 2  # not ready on the first poll, ready on the second

        result = main_mod._wait_until_ready(
            is_alive=lambda: True, check_ready=check_ready, timeout=5, poll_interval=0
        )
        assert result is True
        assert len(calls) == 2

    def test_returns_false_if_process_exits_before_ready(self):
        result = main_mod._wait_until_ready(
            is_alive=lambda: False, check_ready=lambda: False, timeout=5, poll_interval=0
        )
        assert result is False

    def test_returns_false_on_timeout(self):
        result = main_mod._wait_until_ready(
            is_alive=lambda: True, check_ready=lambda: False, timeout=0.05, poll_interval=0.01
        )
        assert result is False


class _FakeProc:
    def __init__(self, returncode=0):
        self._returncode = returncode
        self.signals_received = []

    def poll(self):
        return None  # still running

    def send_signal(self, signum):
        self.signals_received.append(signum)

    def wait(self):
        return self._returncode


class _FakeCompletedProcess:
    def __init__(self, returncode):
        self.returncode = returncode


class TestRunCloudflared:
    """subprocess.Popen/run and signal.signal are all monkeypatched here --
    this never spawns a real process or touches this test process's actual
    OS signal handlers.
    """

    def test_spawns_cloudflared_with_no_autoupdate(self, monkeypatch):
        captured = {}

        def fake_popen(args, **kw):
            captured["args"] = args
            return _FakeProc(returncode=0)

        monkeypatch.setattr(main_mod.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(main_mod.subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(0))
        monkeypatch.setattr(main_mod.signal, "signal", lambda *a, **kw: None)

        main_mod.run_cloudflared("mytunnel", [], logging.getLogger("test"))

        assert captured["args"] == [
            "cloudflared",
            "tunnel",
            "--config",
            main_mod.CONFIG_PATH,
            "--no-autoupdate",
            "run",
            "mytunnel",
        ]

    def test_logs_routing_table_once_ready(self, monkeypatch, caplog):
        monkeypatch.setattr(main_mod.subprocess, "Popen", lambda *a, **kw: _FakeProc(returncode=0))
        monkeypatch.setattr(main_mod.subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(0))
        monkeypatch.setattr(main_mod.signal, "signal", lambda *a, **kw: None)

        routes = [HostnameConfig(1, "app.example.com", None, "http://app:3000", ())]
        with caplog.at_level("INFO"):
            code = main_mod.run_cloudflared("mytunnel", routes, logging.getLogger("test"))

        assert code == 0
        assert any("TUNNELMATE ROUTES" in r.getMessage() for r in caplog.records)

    def test_skips_routing_table_when_never_ready(self, monkeypatch, caplog):
        monkeypatch.setattr(main_mod.subprocess, "Popen", lambda *a, **kw: _FakeProc(returncode=1))
        monkeypatch.setattr(main_mod.subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(1))
        monkeypatch.setattr(main_mod.signal, "signal", lambda *a, **kw: None)
        monkeypatch.setattr(main_mod, "READY_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(main_mod, "READY_POLL_INTERVAL_SECONDS", 0.01)

        routes = [HostnameConfig(1, "app.example.com", None, "http://app:3000", ())]
        with caplog.at_level("INFO"):
            code = main_mod.run_cloudflared("mytunnel", routes, logging.getLogger("test"))

        assert code == 1
        assert not any("TUNNELMATE ROUTES" in r.getMessage() for r in caplog.records)
        assert any("did not report ready" in r.getMessage() for r in caplog.records)

    def test_forwards_signals_to_the_child_process(self, monkeypatch):
        fake_proc = _FakeProc(returncode=0)
        monkeypatch.setattr(main_mod.subprocess, "Popen", lambda *a, **kw: fake_proc)
        monkeypatch.setattr(main_mod.subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(0))

        registered = {}
        monkeypatch.setattr(main_mod.signal, "signal", lambda sig, handler: registered.__setitem__(sig, handler))

        main_mod.run_cloudflared("mytunnel", [], logging.getLogger("test"))

        assert signal.SIGTERM in registered
        assert signal.SIGINT in registered
        # Simulate the OS delivering SIGTERM without touching any real
        # signal handler -- just call what was registered, directly.
        registered[signal.SIGTERM](signal.SIGTERM, None)
        assert fake_proc.signals_received == [signal.SIGTERM]
