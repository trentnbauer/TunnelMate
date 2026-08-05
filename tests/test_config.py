import pytest

from app.config import ConfigError, parse_global, parse_hostnames


def base_env(**overrides):
    env = {
        "HOSTNAME_1": "app.example.com",
        "SERVICE_1": "http://app:3000",
    }
    env.update(overrides)
    return env


def test_parses_single_public_hostname():
    configs = parse_hostnames(base_env())
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg.hostname == "app.example.com"
    assert cfg.path is None
    assert cfg.is_route
    assert cfg.service == "http://app:3000"
    assert cfg.authusers == ()


def test_users_set_means_protected():
    configs = parse_hostnames(base_env(USERS_1="a@example.com, b@example.com"))
    assert configs[0].authusers == ("a@example.com", "b@example.com")


def test_stops_at_first_gap():
    env = base_env(**{"HOSTNAME_3": "gap.example.com", "SERVICE_3": "http://gap:80"})
    configs = parse_hostnames(env)
    assert len(configs) == 1  # index 2 is missing, so index 3 is never reached


def test_parses_multiple_sequential_hostnames():
    env = base_env(**{"HOSTNAME_2": "auth.example.com", "SERVICE_2": "http://auth:8080", "USERS_2": "a@example.com"})
    configs = parse_hostnames(env)
    assert len(configs) == 2
    assert configs[1].authusers == ("a@example.com",)


def test_missing_required_service_raises():
    env = base_env()
    del env["SERVICE_1"]
    with pytest.raises(ConfigError, match="SERVICE_1"):
        parse_hostnames(env)


def test_invalid_service_url_raises():
    with pytest.raises(ConfigError, match="SERVICE_1"):
        parse_hostnames(base_env(SERVICE_1="not-a-url"))


def test_duplicate_route_hostname_raises():
    env = base_env(**{"HOSTNAME_2": "app.example.com", "SERVICE_2": "http://other:80"})
    with pytest.raises(ConfigError, match="app.example.com"):
        parse_hostnames(env)


def test_no_hostnames_raises():
    with pytest.raises(ConfigError, match="no hostnames configured"):
        parse_hostnames({})


def test_parse_global_requires_all_fields():
    with pytest.raises(ConfigError, match="CLOUDFLARE_API_TOKEN"):
        parse_global({"CLOUDFLARE_ACCOUNT_ID": "x", "NAME": "y"})

    cfg = parse_global({"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "a", "NAME": "n"})
    assert cfg.api_token == "t"
    assert cfg.account_id == "a"
    assert cfg.tunnel_name == "n"


class TestPathScopes:
    def base_with_path(self, **overrides):
        env = base_env(**{"HOSTNAME_2": "app.example.com/admin", "USERS_2": "a@example.com"})
        env.update(overrides)
        return env

    def test_parses_path_scoped_entry(self):
        configs = parse_hostnames(self.base_with_path())
        path_cfg = configs[1]
        assert path_cfg.hostname == "app.example.com"
        assert path_cfg.path == "/admin"
        assert not path_cfg.is_route
        assert path_cfg.service is None
        assert path_cfg.scope_key == "app.example.com/admin"
        assert path_cfg.authusers == ("a@example.com",)

    def test_path_scope_without_users_raises(self):
        env = self.base_with_path()
        del env["USERS_2"]
        with pytest.raises(ConfigError, match="USERS_2"):
            parse_hostnames(env)

    def test_path_scope_with_service_raises(self):
        env = self.base_with_path(SERVICE_2="http://app:3000")
        with pytest.raises(ConfigError, match="SERVICE_2"):
            parse_hostnames(env)

    def test_path_scope_without_matching_route_raises(self):
        env = {
            "HOSTNAME_1": "other.example.com/admin",
            "USERS_1": "a@example.com",
        }
        with pytest.raises(ConfigError, match="other.example.com"):
            parse_hostnames(env)

    def test_duplicate_path_scope_raises(self):
        env = self.base_with_path(**{"HOSTNAME_3": "app.example.com/admin", "USERS_3": "b@example.com"})
        with pytest.raises(ConfigError, match="app.example.com.*admin"):
            parse_hostnames(env)

    def test_two_different_paths_on_same_hostname_are_allowed(self):
        env = self.base_with_path(**{"HOSTNAME_3": "app.example.com/reports", "USERS_3": "b@example.com"})
        configs = parse_hostnames(env)
        assert len(configs) == 3
        assert configs[2].path == "/reports"
