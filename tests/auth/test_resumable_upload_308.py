"""Resumable uploads must not treat 308 as a redirect.

Drive answers every chunk of a resumable upload with ``308 Resume Incomplete`` and
no ``Location:`` header. httplib2 keeps 308 in ``redirect_codes``, so an Http built
by hand raises ``RedirectMissingLocation`` and the upload dies -- but only once the
payload needs more than one chunk, which is why uploads at or below
``UPLOAD_CHUNK_SIZE_BYTES`` (5 MB) looked fine while everything above it failed.

A real 6 MB/13 MB upload is the honest test, but it needs live Google credentials.
This asserts the one thing that made those uploads fail.
"""

import httplib2

from auth.google_auth import _build_authorized_http
from gdrive.drive_helpers import UPLOAD_CHUNK_SIZE_BYTES


class _Credentials:
    """Minimal stand-in -- AuthorizedHttp only stores it until a request is made."""

    token = "token"
    expired = False
    valid = True

    def before_request(self, request, method, url, headers):  # noqa: ARG002
        headers["authorization"] = "Bearer token"


def test_authorized_http_does_not_redirect_308():
    authorized = _build_authorized_http(_Credentials())
    assert 308 not in authorized.http.redirect_codes


def test_other_redirects_are_left_alone():
    authorized = _build_authorized_http(_Credentials())
    # Only 308 is special; touching the rest would change unrelated behaviour.
    assert httplib2.REDIRECT_CODES - authorized.http.redirect_codes == {308}


def test_socket_timeout_survives_the_fix():
    authorized = _build_authorized_http(_Credentials(), timeout=17)
    assert authorized.http.timeout == 17


def test_chunk_size_is_the_boundary_that_used_to_fail():
    # Uploads of this size or smaller are a single chunk and never produce a 308,
    # which is what made the bug look like "a 5 MB upload limit".
    assert UPLOAD_CHUNK_SIZE_BYTES == 5 * 1024 * 1024
