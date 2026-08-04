from app.config import HostnameConfig
from app.ingress import render


def test_renders_ingress_with_catchall():
    hostnames = [
        HostnameConfig(1, "app.example.com", "http://app:3000", "public", ()),
        HostnameConfig(2, "admin.example.com", "http://admin:8080", "auth", ("a@example.com",)),
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
        HostnameConfig(1, "first.example.com", "http://a:1", "public", ()),
        HostnameConfig(2, "second.example.com", "http://b:2", "public", ()),
    ]
    text = render("id", "/data/credentials.json", hostnames)
    assert text.index("first.example.com") < text.index("second.example.com")
