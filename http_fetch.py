"""Restricted HTTPS client for the three fixed cantonal endpoints.

Only https on port 443 against geodienste.ch (cadastre WFS), api.geo.ag.ch
(cantonal ÖREB API) and oereblex.ag.ch (legal-document platform) is ever
requested — every URL is assembled at a call site from those fixed bases.
Ambient proxies are disabled so an environment variable cannot reroute a
request; the URL is validated before the request and again at every
redirect; and a redirect may only land on the same host the request started
on. No cross-host redirect is configured as trusted; upstream redirects
were not live-tested in this offline remediation pass.

Audit provenance: docs/2026-09-29-bandit-review.md (Bandit B310 remediation).
"""
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_HOSTS = frozenset({"geodienste.ch", "api.geo.ag.ch", "oereblex.ag.ch"})


def _check_url(url):
    """Validate `url` against the endpoint policy and return its host."""
    if not isinstance(url, str) or not url:
        raise ValueError("URL must be a non-empty string")
    if "\\" in url:
        raise ValueError("URL must not contain backslashes")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        raise ValueError("URL must not contain control characters")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise ValueError(f"URL scheme must be https, got {parts.scheme!r}")
    if parts.hostname not in ALLOWED_HOSTS:
        raise ValueError(
            f"URL host {parts.hostname!r} is not an allowed endpoint"
        )
    if parts.port not in (None, 443):
        raise ValueError(f"URL port must be 443, got {parts.port}")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL must not carry credentials")
    return parts.hostname


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect target before it is followed."""

    def __init__(self, origin_host):
        self._origin_host = origin_host
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        host = _check_url(target)
        if host != self._origin_host:
            raise urllib.error.URLError(
                f"refusing cross-host redirect to {host!r}"
            )
        return super().redirect_request(req, fp, code, msg, headers, target)


def get(url, timeout):
    """GET one allowed URL and return the response body as bytes.

    `timeout` is the per-attempt socket timeout, unchanged from the
    `urllib.request.urlopen(..., timeout=...)` contract it replaces.
    """
    host = _check_url(url)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _SameOriginRedirect(host),
    )
    with opener.open(url, timeout=timeout) as response:
        return response.read()
