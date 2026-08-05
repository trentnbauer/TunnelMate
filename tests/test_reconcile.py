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

    def create_access_app(self, domain):
        self.calls.append(("create_access_app", domain))
        return self._id("app")

    def delete_access_app(self, app_id):
        self.calls.append(("delete_access_app", app_id))

    def create_access_policy(self, app_id, domain, authusers):
        self.calls.append(("create_access_policy", app_id, domain, authusers))
        return self._id("policy")

    def update_access_policy(self, app_id, policy_id, domain, authusers):
        self.calls.append(("update_access_policy", app_id, policy_id, domain, authusers))


ZONES = [Zone(id="zone-1", name="example.com")]
# Placeholder fixture values only -- not real Cloudflare credentials.
TUNNEL = {
    "id": "tunnel-1",
    "name": "test",
    "account_tag": "fixture-account-tag",
    "tunnel_secret": "fixture-not-a-real-secret",
}


def route_config(**overrides):
    defaults = dict(index=1, hostname="app.example.com", path=None, service="http://app:80", authusers=())
    defaults.update(overrides)
    return HostnameConfig(**defaults)


def path_config(**overrides):
    defaults = dict(index=2, hostname="app.example.com", path="/admin", service=None, authusers=("a@example.com",))
    defaults.update(overrides)
    return HostnameConfig(**defaults)


class TestReconcileRoutes:
    def test_new_public_hostname_creates_dns_only(self):
        client = FakeClient()
        state = {"routes": {}}
        cfg = route_config()

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls == [
            ("create_dns_record", "zone-1", "app.example.com", "tunnel-1.cfargotunnel.com")
        ]
        assert state["routes"]["app.example.com"]["access_app_id"] is None

    def test_new_protected_hostname_creates_dns_and_access(self):
        client = FakeClient()
        state = {"routes": {}}
        cfg = route_config(authusers=("a@example.com",))

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        kinds = [c[0] for c in client.calls]
        assert kinds == ["create_dns_record", "create_access_app", "create_access_policy"]
        entry = state["routes"]["app.example.com"]
        assert entry["access_app_id"] == "app-1"
        assert entry["access_policy_id"] == "policy-1"

    def test_removed_hostname_is_pruned(self):
        client = FakeClient()
        state = {
            "routes": {
                "gone.example.com": {
                    "zone_id": "zone-1",
                    "dns_record_id": "dns-1",
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": ["a@example.com"],
                }
            }
        }

        reconcile.reconcile_routes(client, TUNNEL, [], state, ZONES)

        assert client.calls == [
            ("delete_dns_record", "zone-1", "dns-1"),
            ("delete_access_app", "app-1"),
        ]
        assert state["routes"] == {}

    def test_public_to_protected_transition_adds_access(self):
        client = FakeClient()
        state = {
            "routes": {
                "app.example.com": {
                    "zone_id": "zone-1",
                    "dns_record_id": "dns-1",
                    "access_app_id": None,
                    "access_policy_id": None,
                    "authusers": [],
                }
            }
        }
        cfg = route_config(authusers=("a@example.com",))

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        kinds = [c[0] for c in client.calls]
        assert kinds == ["create_access_app", "create_access_policy"]
        assert state["routes"]["app.example.com"]["access_app_id"] == "app-1"

    def test_protected_to_public_transition_removes_access(self):
        client = FakeClient()
        state = {
            "routes": {
                "app.example.com": {
                    "zone_id": "zone-1",
                    "dns_record_id": "dns-1",
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": ["a@example.com"],
                }
            }
        }
        cfg = route_config(authusers=())

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls == [("delete_access_app", "app-1")]
        assert state["routes"]["app.example.com"]["access_app_id"] is None

    def test_authusers_change_updates_policy_in_place(self):
        client = FakeClient()
        state = {
            "routes": {
                "app.example.com": {
                    "zone_id": "zone-1",
                    "dns_record_id": "dns-1",
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": ["a@example.com"],
                }
            }
        }
        cfg = route_config(authusers=("a@example.com", "b@example.com"))

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls == [
            ("update_access_policy", "app-1", "policy-1", "app.example.com", ("a@example.com", "b@example.com"))
        ]

    def test_service_only_change_makes_no_cloudflare_calls(self):
        client = FakeClient()
        state = {
            "routes": {
                "app.example.com": {
                    "zone_id": "zone-1",
                    "dns_record_id": "dns-1",
                    "access_app_id": None,
                    "access_policy_id": None,
                    "authusers": [],
                }
            }
        }
        cfg = route_config(service="http://app:9999")

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls == []


class TestReconcilePathScopes:
    def test_new_path_scope_creates_access_only(self):
        client = FakeClient()
        state = {"path_scopes": {}}
        cfg = path_config()

        reconcile.reconcile_path_scopes(client, [cfg], state)

        assert client.calls == [
            ("create_access_app", "app.example.com/admin"),
            ("create_access_policy", "app-1", "app.example.com/admin", ("a@example.com",)),
        ]
        entry = state["path_scopes"]["app.example.com/admin"]
        assert entry["access_app_id"] == "app-1"

    def test_removed_path_scope_is_pruned(self):
        client = FakeClient()
        state = {
            "path_scopes": {
                "app.example.com/admin": {
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": ["a@example.com"],
                }
            }
        }

        reconcile.reconcile_path_scopes(client, [], state)

        assert client.calls == [("delete_access_app", "app-1")]
        assert state["path_scopes"] == {}

    def test_authusers_change_updates_policy_in_place(self):
        client = FakeClient()
        state = {
            "path_scopes": {
                "app.example.com/admin": {
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": ["a@example.com"],
                }
            }
        }
        cfg = path_config(authusers=("a@example.com", "b@example.com"))

        reconcile.reconcile_path_scopes(client, [cfg], state)

        assert client.calls == [
            ("update_access_policy", "app-1", "policy-1", "app.example.com/admin", ("a@example.com", "b@example.com"))
        ]

    def test_unchanged_path_scope_makes_no_calls(self):
        client = FakeClient()
        state = {
            "path_scopes": {
                "app.example.com/admin": {
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": ["a@example.com"],
                }
            }
        }
        cfg = path_config()

        reconcile.reconcile_path_scopes(client, [cfg], state)

        assert client.calls == []


def test_reconcile_tunnel_reuses_persisted_identity():
    client = FakeClient()
    client.create_tunnel = lambda name: (_ for _ in ()).throw(AssertionError("should not be called"))
    state = {"tunnel": TUNNEL}

    result = reconcile.reconcile_tunnel(client, None, state)

    assert result == TUNNEL
