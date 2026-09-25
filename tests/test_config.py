"""Offline tests for credential loading and session selection."""

import pytest
from telethon.crypto import AuthKey
from telethon.sessions import StringSession

from telescraper import config
from telescraper.config import Credentials, load_credentials, session_for


def test_load_credentials_reads_session_string(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)  # keep a real .env out of the test
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "h")
    monkeypatch.setenv("TG_SESSION_STRING", "abc")
    assert load_credentials().session_string == "abc"


def test_session_for_prefers_string_over_file():
    assert session_for(Credentials(1, "h"), "file") == "file"
    saved = StringSession()
    saved.set_dc(2, "149.154.167.51", 443)
    saved.auth_key = AuthKey(bytes(256))  # save() is "" without a key
    s = session_for(Credentials(1, "h", session_string=saved.save()), "file")
    assert isinstance(s, StringSession) and s.dc_id == 2


@pytest.mark.parametrize("bad", ["abc", "1abc"])  # not a string / truncated paste
def test_session_for_rejects_malformed_string(bad):
    with pytest.raises(SystemExit, match="TG_SESSION_STRING"):
        session_for(Credentials(1, "h", session_string=bad), "file")
