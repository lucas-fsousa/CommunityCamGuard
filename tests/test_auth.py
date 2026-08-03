from backend.app import auth


def test_check_key_constant_time_match():
    assert auth.check_key("test-secret-key")      # matches fixture DASHBOARD_SECRET_KEY
    assert not auth.check_key("wrong")
    assert not auth.check_key("")


def test_token_roundtrip():
    token = auth.issue_token()
    assert auth.verify_token(token)


def test_tampered_token_rejected():
    token = auth.issue_token()
    assert not auth.verify_token(token + "x")
    assert not auth.verify_token("not-a-token")
