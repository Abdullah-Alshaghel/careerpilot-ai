from __future__ import annotations

import pytest

from company_service.urls import InvalidUrl, detect_connector, normalize_public_https_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://acme.com/careers", "https://acme.com/careers"),
        ("  https://Careers.ACME.com/jobs?team=eng#top ", "https://careers.acme.com/jobs?team=eng"),
        ("acme.com/careers", "https://acme.com/careers"),
        ("https://acme.com", "https://acme.com/"),
        ("https://acme.com:443/jobs", "https://acme.com/jobs"),
        ("https://شركة.السعودية/وظائف", "https://xn--ogbpi5d.xn--mgberp4a5d4ar/وظائف"),
    ],
)
def test_accepts_and_normalizes_public_https_urls(raw, expected):
    assert normalize_public_https_url(raw) == expected


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("http://acme.com/careers", "https_required"),
        ("ftp://acme.com", "https_required"),
        ("javascript:alert(1)", "https_required"),
        ("file:///etc/passwd", "https_required"),
        ("https://user:pass@acme.com/", "credentials_in_url"),
        ("https://acme.com@evil.com/", "credentials_in_url"),
        ("https://acme.com:8443/jobs", "non_default_port"),
        ("https://127.0.0.1/", "ip_address_not_allowed"),
        ("https://10.0.0.5/admin", "ip_address_not_allowed"),
        ("https://169.254.169.254/latest/meta-data/", "ip_address_not_allowed"),
        ("https://[::1]/", "ip_address_not_allowed"),
        ("https://localhost/", "private_host"),
        ("https://api.localhost/", "private_host"),
        ("https://intranet.corp/", "private_host"),
        ("https://db.internal/", "private_host"),
        ("https://printer.local/", "private_host"),
        ("https://acme/", "private_host"),
        ("https://2130706433/", "private_host"),  # decimal IPv4 trick
        ("https://-bad-.com/", "private_host"),
        ("", "invalid_url"),
        ("https://acme.com/ space", "invalid_url"),
        ("https://acme.com/" + "a" * 2100, "invalid_url"),
    ],
)
def test_rejects_unsafe_or_invalid_urls(raw, code):
    with pytest.raises(InvalidUrl) as err:
        normalize_public_https_url(raw)
    assert err.value.code == code


@pytest.mark.parametrize(
    ("url", "connector_type", "config"),
    [
        ("https://boards.greenhouse.io/acme", "greenhouse", {"board_token": "acme"}),
        ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse", {"board_token": "acme"}),
        ("https://jobs.lever.co/acme-inc", "lever", {"company": "acme-inc"}),
        ("https://jobs.eu.lever.co/acme", "lever", {"company": "acme"}),
        ("https://jobs.ashbyhq.com/Acme", "ashby", {"organization": "Acme"}),
    ],
)
def test_detects_public_ats_boards(url, connector_type, config):
    detected = detect_connector(url)
    assert detected is not None
    assert (detected.connector_type, detected.config) == (connector_type, config)


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.com/careers",
        "https://boards.greenhouse.io/",
        "https://evilgreenhouse.io/acme",
        "https://boards.greenhouse.io.evil.com/acme",
    ],
)
def test_no_detection_for_other_urls(url):
    assert detect_connector(url) is None
