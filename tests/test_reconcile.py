import pytest

from app import reconcile
from app.config import HostnameConfig
from app.zones import Zone


class FakeClient:
    """Records calls instead of hitting the network.

    `fail_on` maps a call name to an exception to raise the first (and
    only) time that call is made -- used to simulate a Cloudflare API call
    failing partway through a reconcile run, without recording it in
    `calls` (a failed call has no lasting effect to assert on).
    """

    def __init__(self, fail_on=None):
        self.calls = []
        self._counters = {}
        self._fail_on = dict(fail_on or {})

    def _id(self, prefix):
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]}"

    def _maybe_fail(self, name):
        if name in self._fail_on:
            raise self._fail_on.pop(name)

    def create_dns_record(self, zone_id, hostname, target):
        self._maybe_fail("create_dns_record")
        self.calls.append(("create_dns_record", zone_id, hostname, target))
        return self._id("dns")

    def delete_dns_record(self, zone_id, record_id):
        self._maybe_fail("delete_dns_record")
        self.calls.append(("delete_dns_record", zone_id, record_id))

    def create_access_app(self, domain, name=None):
        self._maybe_fail("create_access_app")
        self.calls.append(("create_access_app", domain, name or domain))
        return self._id("app")

    def update_access_app(self, app_id, domain, name):
        self._maybe_fail("update_access_app")
        self.calls.append(("update_access_app", app_id, domain, name))

    def delete_access_app(self, app_id):
        self._maybe_fail("delete_access_app")
        self.calls.append(("delete_access_app", app_id))

    def create_access_policy(self, app_id, domain, authusers):
        self._maybe_fail("create_access_policy")
        self.calls.append(("create_access_policy", app_id, domain, authusers))
        return self._id("policy")

    def update_access_policy(self, app_id, policy_id, domain, authusers):
        self._maybe_fail("update_access_policy")
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
    def test_new_public_hostname_creates_dns_and_bypass_access(self):
        client = FakeClient()
        state = {"routes": {}}
        cfg = route_config()

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        kinds = [c[0] for c in client.calls]
        assert kinds == ["create_dns_record", "create_access_app", "create_access_policy"]
        assert client.calls[1] == ("create_access_app", "app.example.com", "app.example.com")
        assert client.calls[2] == ("create_access_policy", "app-1", "app.example.com", ())
        entry = state["routes"]["app.example.com"]
        assert entry["access_app_id"] == "app-1"
        assert entry["access_policy_id"] == "policy-1"
        assert entry["app_name"] == "app.example.com"

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

    def test_new_hostname_with_app_name_override_uses_it(self):
        client = FakeClient()
        state = {"routes": {}}
        cfg = route_config(app_name="My App")

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls[1] == ("create_access_app", "app.example.com", "My App")
        assert state["routes"]["app.example.com"]["app_name"] == "My App"

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

    def test_legacy_public_route_without_access_app_gets_backfilled(self):
        # Pre-existing state from before every hostname always got an
        # Access app: a public route with no app_id at all, and its own
        # config unchanged (still public). It should still get an app now.
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
        cfg = route_config()

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        kinds = [c[0] for c in client.calls]
        assert kinds == ["create_access_app", "create_access_policy"]
        assert client.calls[1] == ("create_access_policy", "app-1", "app.example.com", ())
        assert state["routes"]["app.example.com"]["access_app_id"] == "app-1"

    def test_public_to_protected_transition_updates_policy(self):
        client = FakeClient()
        state = {
            "routes": {
                "app.example.com": {
                    "zone_id": "zone-1",
                    "dns_record_id": "dns-1",
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": [],
                    "app_name": "app.example.com",
                }
            }
        }
        cfg = route_config(authusers=("a@example.com",))

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls == [
            ("update_access_policy", "app-1", "policy-1", "app.example.com", ("a@example.com",))
        ]
        assert state["routes"]["app.example.com"]["access_app_id"] == "app-1"

    def test_protected_to_public_transition_updates_policy_to_bypass(self):
        client = FakeClient()
        state = {
            "routes": {
                "app.example.com": {
                    "zone_id": "zone-1",
                    "dns_record_id": "dns-1",
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": ["a@example.com"],
                    "app_name": "app.example.com",
                }
            }
        }
        cfg = route_config(authusers=())

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls == [("update_access_policy", "app-1", "policy-1", "app.example.com", ())]
        assert state["routes"]["app.example.com"]["access_app_id"] == "app-1"

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
                    "app_name": "app.example.com",
                }
            }
        }
        cfg = route_config(authusers=("a@example.com", "b@example.com"))

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls == [
            ("update_access_policy", "app-1", "policy-1", "app.example.com", ("a@example.com", "b@example.com"))
        ]

    def test_app_name_change_updates_access_app_in_place(self):
        client = FakeClient()
        state = {
            "routes": {
                "app.example.com": {
                    "zone_id": "zone-1",
                    "dns_record_id": "dns-1",
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": [],
                    "app_name": "app.example.com",
                }
            }
        }
        cfg = route_config(app_name="New Display Name")

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls == [("update_access_app", "app-1", "app.example.com", "New Display Name")]
        assert state["routes"]["app.example.com"]["app_name"] == "New Display Name"

    def test_service_only_change_makes_no_cloudflare_calls(self):
        client = FakeClient()
        state = {
            "routes": {
                "app.example.com": {
                    "zone_id": "zone-1",
                    "dns_record_id": "dns-1",
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": [],
                    "app_name": "app.example.com",
                }
            }
        }
        cfg = route_config(service="http://app:9999")

        reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls == []

    def test_persist_called_after_each_mutation_for_a_new_route(self):
        snapshots = []
        client = FakeClient()
        state = {"routes": {}}
        cfg = route_config(authusers=("a@example.com",))

        reconcile.reconcile_routes(
            client, TUNNEL, [cfg], state, ZONES, persist=lambda: snapshots.append(dict(state["routes"]))
        )

        # Once right after the DNS record is created, once after the
        # Access app+policy completes the route.
        assert len(snapshots) == 2
        assert snapshots[0]["app.example.com"]["access_app_id"] is None
        assert snapshots[1]["app.example.com"]["access_app_id"] == "app-1"

    def test_retry_after_access_app_failure_does_not_recreate_dns(self):
        state = {"routes": {}}
        cfg = route_config(authusers=("a@example.com",))

        client = FakeClient(fail_on={"create_access_app": RuntimeError("boom")})
        with pytest.raises(RuntimeError):
            reconcile.reconcile_routes(client, TUNNEL, [cfg], state, ZONES)

        assert client.calls == [
            ("create_dns_record", "zone-1", "app.example.com", "tunnel-1.cfargotunnel.com")
        ]
        entry = state["routes"]["app.example.com"]
        assert entry["dns_record_id"] == "dns-1"
        assert entry["access_app_id"] is None

        # Retry with a fresh client/call log: the already-persisted DNS
        # record must not be recreated, only the missing Access app+policy.
        retry_client = FakeClient()
        reconcile.reconcile_routes(retry_client, TUNNEL, [cfg], state, ZONES)

        assert [c[0] for c in retry_client.calls] == ["create_access_app", "create_access_policy"]
        assert state["routes"]["app.example.com"]["dns_record_id"] == "dns-1"

    def test_retry_after_partial_prune_failure_does_not_redelete_dns(self):
        state = {
            "routes": {
                "gone.example.com": {
                    "zone_id": "zone-1",
                    "dns_record_id": "dns-1",
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": [],
                }
            }
        }

        client = FakeClient(fail_on={"delete_access_app": RuntimeError("boom")})
        with pytest.raises(RuntimeError):
            reconcile.reconcile_routes(client, TUNNEL, [], state, ZONES)

        assert client.calls == [("delete_dns_record", "zone-1", "dns-1")]
        entry = state["routes"]["gone.example.com"]
        assert entry["dns_record_id"] is None
        assert entry["access_app_id"] == "app-1"  # not yet deleted

        retry_client = FakeClient()
        reconcile.reconcile_routes(retry_client, TUNNEL, [], state, ZONES)

        assert retry_client.calls == [("delete_access_app", "app-1")]
        assert state["routes"] == {}


class TestReconcilePathScopes:
    def test_new_path_scope_creates_access_only(self):
        client = FakeClient()
        state = {"path_scopes": {}}
        cfg = path_config()

        reconcile.reconcile_path_scopes(client, [cfg], state)

        assert client.calls == [
            ("create_access_app", "app.example.com/admin", "app.example.com/admin"),
            ("create_access_policy", "app-1", "app.example.com/admin", ("a@example.com",)),
        ]
        entry = state["path_scopes"]["app.example.com/admin"]
        assert entry["access_app_id"] == "app-1"
        assert entry["app_name"] == "app.example.com/admin"

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
                    "app_name": "app.example.com/admin",
                }
            }
        }
        cfg = path_config(authusers=("a@example.com", "b@example.com"))

        reconcile.reconcile_path_scopes(client, [cfg], state)

        assert client.calls == [
            ("update_access_policy", "app-1", "policy-1", "app.example.com/admin", ("a@example.com", "b@example.com"))
        ]

    def test_app_name_change_updates_access_app_in_place(self):
        client = FakeClient()
        state = {
            "path_scopes": {
                "app.example.com/admin": {
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": ["a@example.com"],
                    "app_name": "app.example.com/admin",
                }
            }
        }
        cfg = path_config(app_name="Admin Panel")

        reconcile.reconcile_path_scopes(client, [cfg], state)

        assert client.calls == [("update_access_app", "app-1", "app.example.com/admin", "Admin Panel")]
        assert state["path_scopes"]["app.example.com/admin"]["app_name"] == "Admin Panel"

    def test_unchanged_path_scope_makes_no_calls(self):
        client = FakeClient()
        state = {
            "path_scopes": {
                "app.example.com/admin": {
                    "access_app_id": "app-1",
                    "access_policy_id": "policy-1",
                    "authusers": ["a@example.com"],
                    "app_name": "app.example.com/admin",
                }
            }
        }
        cfg = path_config()

        reconcile.reconcile_path_scopes(client, [cfg], state)

        assert client.calls == []

    def test_retry_after_policy_failure_does_not_recreate_app(self):
        state = {"path_scopes": {}}
        cfg = path_config()

        client = FakeClient(fail_on={"create_access_policy": RuntimeError("boom")})
        with pytest.raises(RuntimeError):
            reconcile.reconcile_path_scopes(client, [cfg], state)

        assert client.calls == [("create_access_app", "app.example.com/admin", "app.example.com/admin")]
        entry = state["path_scopes"]["app.example.com/admin"]
        assert entry["access_app_id"] == "app-1"
        assert entry["access_policy_id"] is None

        retry_client = FakeClient()
        reconcile.reconcile_path_scopes(retry_client, [cfg], state)

        assert retry_client.calls == [
            ("create_access_policy", "app-1", "app.example.com/admin", ("a@example.com",))
        ]


def test_reconcile_tunnel_reuses_persisted_identity():
    client = FakeClient()
    client.create_tunnel = lambda name: (_ for _ in ()).throw(AssertionError("should not be called"))
    state = {"tunnel": TUNNEL}

    result = reconcile.reconcile_tunnel(client, None, state)

    assert result == TUNNEL
