import pytest

from app.zones import NoMatchingZoneError, Zone, match


ZONES = [Zone(id="z1", name="example.com"), Zone(id="z2", name="sub.example.org")]


def test_matches_apex_domain():
    assert match("example.com", ZONES).id == "z1"


def test_matches_subdomain_of_apex():
    assert match("app.example.com", ZONES).id == "z1"


def test_prefers_longest_matching_zone():
    assert match("app.sub.example.org", ZONES).id == "z2"


def test_no_match_raises():
    with pytest.raises(NoMatchingZoneError, match="unrelated.net"):
        match("unrelated.net", ZONES)


def test_does_not_match_unrelated_suffix():
    # "notexample.com" should not match zone "example.com"
    with pytest.raises(NoMatchingZoneError):
        match("notexample.com", ZONES)
