from app.config import HostnameConfig
from app.ingress import render


def route(index, hostname, service, authusers=()):
    return HostnameConfig(index, hostname, None, service, authusers)


def path_scope(index, hostname, path, authusers):
    return HostnameConfig(index, hostname, path, None, authusers)


def test_renders_ingress_with_catchall():
    hostnames = [
        route(1, "app.example.com", "http://app:3000"),
        route(2, "admin.example.com", "http://admin:8080", ("a@example.com",)),
    ]
    text = render("tunnel-id-123", "/data/credentials.json", hostnames)

    assert text.splitlines() == [
        "tunnel: tunnel-id-123",
        "credentials-file: /data/credentials.json",
        "ingress:",
        "  - hostname: app.example.com",
        "    service: http://app:3000",
        "  - hostname: admin.example.com",
        "    service: http://admin:8080",
        "  - service: http_status:404",
    ]


def test_preserves_index_order():
    hostnames = [
        route(1, "first.example.com", "http://a:1"),
        route(2, "second.example.com", "http://b:2"),
    ]
    text = render("id", "/data/credentials.json", hostnames)
    assert text.index("first.example.com") < text.index("second.example.com")


def test_https_service_gets_no_tls_verify():
    hostnames = [
        route(1, "app.example.com", "https://app:8443"),
        route(2, "other.example.com", "http://other:80"),
    ]
    text = render("id", "/data/credentials.json", hostnames)

    assert text.splitlines() == [
        "tunnel: id",
        "credentials-file: /data/credentials.json",
        "ingress:",
        "  - hostname: app.example.com",
        "    service: https://app:8443",
        "    originRequest:",
        "      noTLSVerify: true",
        "  - hostname: other.example.com",
        "    service: http://other:80",
        "  - service: http_status:404",
    ]


def test_path_scoped_entries_are_not_rendered_as_ingress_rules():
    hostnames = [
        route(1, "app.example.com", "http://app:3000"),
        path_scope(2, "app.example.com", "/admin", ("a@example.com",)),
    ]
    text = render("id", "/data/credentials.json", hostnames)

    assert text.splitlines() == [
        "tunnel: id",
        "credentials-file: /data/credentials.json",
        "ingress:",
        "  - hostname: app.example.com",
        "    service: http://app:3000",
        "  - service: http_status:404",
    ]
