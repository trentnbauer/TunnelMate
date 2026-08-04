import pytest

from app.config import ConfigError, parse_global, parse_hostnames


def base_env(**overrides):
    env = {
        "TUNNEL_HOSTNAME_1": "app.example.com",
        "TUNNEL_SERVICE_1": "http://app:3000",
        "TUNNEL_ACCESS_1": "public",
    }
    env.update(overrides)
    return env


def test_parses_single_public_hostname():
    configs = parse_hostnames(base_env())
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg.hostname == "app.example.com"
    assert cfg.service == "http://app:3000"
    assert cfg.accesstype == "public"
    assert cfg.authusers == ()


def test_stops_at_first_gap():
    env = base_env(
        **{
            "TUNNEL_HOSTNAME_3": "gap.example.com",
            "TUNNEL_SERVICE_3": "http://gap:80",
            "TUNNEL_ACCESS_3": "public",
        }
    )
    configs = parse_hostnames(env)
    assert len(configs) == 1  # index 2 is missing, so index 3 is never reached


def test_parses_multiple_sequential_hostnames():
    env = base_env(
        **{
            "TUNNEL_HOSTNAME_2": "auth.example.com",
            "TUNNEL_SERVICE_2": "http://auth:8080",
            "TUNNEL_ACCESS_2": "auth",
            "TUNNEL_AUTH_USERS_2": "a@example.com, b@example.com",
        }
    )
    configs = parse_hostnames(env)
    assert len(configs) == 2
    assert configs[1].authusers == ("a@example.com", "b@example.com")


def test_missing_required_field_raises():
    env = base_env()
    del env["TUNNEL_SERVICE_1"]
    with pytest.raises(ConfigError, match="TUNNEL_SERVICE_1"):
        parse_hostnames(env)


def test_invalid_accesstype_raises():
    with pytest.raises(ConfigError, match="TUNNEL_ACCESS_1"):
        parse_hostnames(base_env(TUNNEL_ACCESS_1="admin-only"))


def test_auth_without_authusers_raises():
    with pytest.raises(ConfigError, match="TUNNEL_AUTH_USERS_1"):
        parse_hostnames(base_env(TUNNEL_ACCESS_1="auth"))


def test_invalid_service_url_raises():
    with pytest.raises(ConfigError, match="TUNNEL_SERVICE_1"):
        parse_hostnames(base_env(TUNNEL_SERVICE_1="not-a-url"))


def test_duplicate_hostname_raises():
    env = base_env(
        **{
            "TUNNEL_HOSTNAME_2": "app.example.com",
            "TUNNEL_SERVICE_2": "http://other:80",
            "TUNNEL_ACCESS_2": "public",
        }
    )
    with pytest.raises(ConfigError, match="app.example.com"):
        parse_hostnames(env)


def test_no_hostnames_raises():
    with pytest.raises(ConfigError, match="no hostnames configured"):
        parse_hostnames({})


def test_parse_global_requires_all_fields():
    with pytest.raises(ConfigError, match="CLOUDFLARE_API_TOKEN"):
        parse_global({"CLOUDFLARE_ACCOUNT_ID": "x", "TUNNEL_NAME": "y"})

    cfg = parse_global(
        {"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "a", "TUNNEL_NAME": "n"}
    )
    assert cfg.api_token == "t"
    assert cfg.account_id == "a"
    assert cfg.tunnel_name == "n"
