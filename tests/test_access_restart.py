"""Fresh-interpreter authorization against isolated persistent SQLite, no services."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app import access_keys, auth
from backend.app.config import get_settings

VERIFY = """
import json, sys
from datetime import datetime
from backend.app import access_keys, auth
payload = json.load(sys.stdin)
if payload.get('now'):
    access_keys._now = lambda: datetime.fromisoformat(payload['now'])
guest = auth.token_principal(payload['guest'])
owner = auth.token_principal(payload['owner'])
print(json.dumps({'guest': guest is not None,
                  'permissions': list(guest.permissions) if guest else [],
                  'owner': owner is not None and owner.can_manage}))
"""


def test_expiry_grants_and_revocation_survive_fresh_interpreters(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SIGNING_KEY", "synthetic-restart-test-signing-key")
    get_settings.cache_clear()
    settings = get_settings()
    # Do not inherit the operator's environment or load the repository's .env.
    environment = {
        "PYTHONPATH": str(Path(__file__).parents[1]),
        "DB_PATH": str(settings.db_path),
        "DASHBOARD_SECRET_KEY": "test-secret-key",
        "SESSION_SIGNING_KEY": "synthetic-restart-test-signing-key",
        "AUTOSTART_SERVICES": "false",
    }
    owner = auth.issue_token()

    def fresh_verify(guest, now=None):
        result = subprocess.run([sys.executable, "-c", VERIFY], cwd=tmp_path, env=environment,
            input=json.dumps({"guest": guest, "owner": owner, "now": now}),
            text=True, capture_output=True, timeout=10, check=True)
        return json.loads(result.stdout)

    indefinite = access_keys.create(access_keys.CreateKey(label="Restart", expires_at=None, permissions=("ptz",)))
    token = auth.issue_temporary_token(indefinite.secret)
    assert fresh_verify(token) == {"guest": True, "permissions": ["ptz"], "owner": True}
    access_keys.revoke(indefinite.metadata.id)
    assert fresh_verify(token) == {"guest": False, "permissions": [], "owner": True}

    finite = access_keys.create(access_keys.CreateKey(label="Finite", expires_at=datetime.now(UTC) + timedelta(hours=1),
                                                     permissions=("recordings",)))
    token = auth.issue_temporary_token(finite.secret)
    assert fresh_verify(token) == {"guest": True, "permissions": ["recordings"], "owner": True}
    assert fresh_verify(token, finite.metadata.expires_at.isoformat()) == {
        "guest": False, "permissions": [], "owner": True}
