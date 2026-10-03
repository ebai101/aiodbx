from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import pytest

from aiodbx import oauth_authorization_url

PKCE_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
PKCE_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def _params(url: str) -> dict[str, list[str]]:
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "www.dropbox.com"
    assert parts.path == "/oauth2/authorize"
    return parse_qs(parts.query, keep_blank_values=True)


def test_required_offline_parameters() -> None:
    params = _params(oauth_authorization_url("app-key", state="csrf"))

    assert params == {
        "client_id": ["app-key"],
        "response_type": ["code"],
        "token_access_type": ["offline"],
        "state": ["csrf"],
    }


def test_redirect_scope_and_state_round_trip() -> None:
    params = _params(
        oauth_authorization_url(
            "app-key",
            state="a+b/c=",
            redirect_uri="https://example.com/cb?x=1",
            scopes=("files.content.read", "files.content.write"),
        )
    )

    assert params["state"] == ["a+b/c="]
    assert params["redirect_uri"] == ["https://example.com/cb?x=1"]
    assert params["scope"] == ["files.content.read files.content.write"]


def test_pkce_s256_challenge_matches_rfc7636() -> None:
    url = oauth_authorization_url("app-key", state="csrf", code_verifier=PKCE_VERIFIER)
    params = _params(url)

    assert params["code_challenge"] == [PKCE_CHALLENGE]
    assert params["code_challenge_method"] == ["S256"]
    assert PKCE_VERIFIER not in url


def test_empty_scope_sequence_omits_parameter() -> None:
    params = _params(oauth_authorization_url("app-key", state="csrf", scopes=()))
    assert "scope" not in params


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: oauth_authorization_url("", state="s"), "app_key"),
        (lambda: oauth_authorization_url("k", state=""), "state"),
        (
            lambda: oauth_authorization_url("k", state="s", redirect_uri=""),
            "redirect_uri",
        ),
        (
            lambda: oauth_authorization_url(
                "k", state="s", scopes="files.content.read"
            ),
            "scopes",
        ),
        (lambda: oauth_authorization_url("k", state="s", scopes=("",)), "scopes"),
        (lambda: oauth_authorization_url("k", state="s", scopes=("a b",)), "scopes"),
        (
            lambda: oauth_authorization_url("k", state="s", code_verifier="too-short"),
            "code_verifier",
        ),
        (
            lambda: oauth_authorization_url("k", state="s", code_verifier="a" * 129),
            "code_verifier",
        ),
        (
            lambda: oauth_authorization_url("k", state="s", code_verifier="é" * 43),
            "code_verifier",
        ),
    ],
)
def test_rejects_invalid_inputs(call: Callable[[], str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        call()
