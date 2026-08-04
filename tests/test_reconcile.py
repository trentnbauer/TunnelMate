from app import reconcile
from app.config import HostnameConfig
from app.zones import Zone


class FakeClient:
    """Records calls instead of hitting the network."""

    def __init__(self):
        self.calls = []
        self._counters = {}

    def _id(self, prefix):
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]}"

    def create_dns_record(self, zone_id, hostname, target):
        self.calls.append(("create_dns_record", zone_id, hostname, target))
        return self._id("dns")

    def delete_dns_record(self, zone_id, record_id):
        self.calls.append(("delete_dns_record", zone_id, record_id))

    def create_access_app(self, hostname):
        self.calls.append(("create_access_app", hostname))
        return self._id("app")

    def delete_access_app(self, app_id):
        self.calls.append(("delete_access_app", app_id))

    def create_access_policy(self, app_id, hostname, accesstype, authusers):
        self.calls.append(("create_access_policy", app_id, hostname, accesstype, authusers))
        return self._id("policy")

    def update_access_policy(self, app_id, policy_id, hostname, accesstype, authusers):
        self.calls.append(("update_access_policy", app_id, policy_id, hostname, accesstype, authusers))


ZONES = [Zone(id="zone-1", name="example.com")]
# Placeholder fixture values only -- not real Cloudflare credentials.
TUNNEL = {
    "id": "tunnel-1",
    "name": "test",
    "account_tag": "fixture-account-tag",
    "tunnel_secret": "fixture-not-a-real-secret",
}


def new_hostname_config(**overrides):
    defaults = dict(index=1, hostname="app.example.com", service="http://app:80",
                     accesstype="public", authusers=())
    defaults.update(overrides)
    return HostnameConfig(**defaults)


def test_new_public_hostname_creates_dns_only():
    client = FakeClient()
    state = {"hostnames": {}}
    cfg = new_hostname_config()

    reconcile.reconcile_hostnames(client, TUNNEL, [cfg], state, ZONES)

    assert client.calls == [
        ("create_dns_record", "zone-1", "app.example.com", "tunnel-1.cfargotunnel.com")
    ]
    assert state["hostnames"]["app.example.com"]["access_app_id"] is None


def test_new_auth_hostname_creates_dns_and_access():
    client = FakeClient()
    state = {"hostnames": {}}
    cfg = new_hostname_config(accesstype="auth", authusers=("a@example.com",))

    reconcile.reconcile_hostnames(client, TUNNEL, [cfg], state, ZONES)

    kinds = [c[0] for c in client.calls]
    assert kinds == ["create_dns_record", "create_access_app", "create_access_policy"]
    entry = state["hostnames"]["app.example.com"]
    assert entry["access_app_id"] == "app-1"
    assert entry["access_policy_id"] == "policy-1"


def test_removed_hostname_is_pruned():
    client = FakeClient()
    state = {
        "hostnames": {
            "gone.example.com": {
                "zone_id": "zone-1",
                "dns_record_id": "dns-1",
                "access_app_id": "app-1",
                "access_policy_id": "policy-1",
                "accesstype": "auth",
                "authusers": ["a@example.com"],
            }
        }
    }

    reconcile.reconcile_hostnames(client, TUNNEL, [], state, ZONES)

    assert client.calls == [
        ("delete_dns_record", "zone-1", "dns-1"),
        ("delete_access_app", "app-1"),
    ]
    assert state["hostnames"] == {}


def test_public_to_auth_transition_adds_access():
    client = FakeClient()
    state = {
        "hostnames": {
            "app.example.com": {
                "zone_id": "zone-1",
                "dns_record_id": "dns-1",
                "access_app_id": None,
                "access_policy_id": None,
                "accesstype": "public",
                "authusers": [],
            }
        }
    }
    cfg = new_hostname_config(accesstype="auth", authusers=("a@example.com",))

    reconcile.reconcile_hostnames(client, TUNNEL, [cfg], state, ZONES)

    kinds = [c[0] for c in client.calls]
    assert kinds == ["create_access_app", "create_access_policy"]
    assert state["hostnames"]["app.example.com"]["access_app_id"] == "app-1"


def test_auth_to_public_transition_removes_access():
    client = FakeClient()
    state = {
        "hostnames": {
            "app.example.com": {
                "zone_id": "zone-1",
                "dns_record_id": "dns-1",
                "access_app_id": "app-1",
                "access_policy_id": "policy-1",
                "accesstype": "auth",
                "authusers": ["a@example.com"],
            }
        }
    }
    cfg = new_hostname_config(accesstype="public")

    reconcile.reconcile_hostnames(client, TUNNEL, [cfg], state, ZONES)

    assert client.calls == [("delete_access_app", "app-1")]
    assert state["hostnames"]["app.example.com"]["access_app_id"] is None


def test_authusers_change_updates_policy_in_place():
    client = FakeClient()
    state = {
        "hostnames": {
            "app.example.com": {
                "zone_id": "zone-1",
                "dns_record_id": "dns-1",
                "access_app_id": "app-1",
                "access_policy_id": "policy-1",
                "accesstype": "auth",
                "authusers": ["a@example.com"],
            }
        }
    }
    cfg = new_hostname_config(accesstype="auth", authusers=("a@example.com", "b@example.com"))

    reconcile.reconcile_hostnames(client, TUNNEL, [cfg], state, ZONES)

    assert client.calls == [
        (
            "update_access_policy",
            "app-1",
            "policy-1",
            "app.example.com",
            "auth",
            ("a@example.com", "b@example.com"),
        )
    ]


def test_service_only_change_makes_no_cloudflare_calls():
    client = FakeClient()
    state = {
        "hostnames": {
            "app.example.com": {
                "zone_id": "zone-1",
                "dns_record_id": "dns-1",
                "access_app_id": None,
                "access_policy_id": None,
                "accesstype": "public",
                "authusers": [],
            }
        }
    }
    cfg = new_hostname_config(service="http://app:9999")

    reconcile.reconcile_hostnames(client, TUNNEL, [cfg], state, ZONES)

    assert client.calls == []


def test_reconcile_tunnel_reuses_persisted_identity():
    client = FakeClient()
    client.create_tunnel = lambda name: (_ for _ in ()).throw(AssertionError("should not be called"))
    state = {"tunnel": TUNNEL}

    result = reconcile.reconcile_tunnel(client, None, state)

    assert result == TUNNEL
