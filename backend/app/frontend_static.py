"""Only reviewed public dashboard assets may be served without authentication."""

from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

PUBLIC_ASSETS = frozenset({
    "index.html", "app.js", "boot.js", "i18n.js", "style.css", "player.js", "video-rtc.js",
    "modules/core.js", "modules/recordings.js", "modules/settings.js",
    "modules/session-watch.js", "modules/push-to-talk.js", "modules/step-ptz.js",
    "modules/camera-control-actions.js", "modules/notifications.js", "modules/audio-message.js",
    "modules/recording-playback.js", "modules/camera-provisioning-ble.js",
    "modules/camera-controls.js", "modules/live-cameras.js", "modules/camera-management.js",
})


class DashboardFiles(StaticFiles):
    """No directory browsing, HTML fallback, unlisted files or asset symlinks."""

    def __init__(self, directory):
        super().__init__(directory=directory, html=False, follow_symlink=False)
        self._dashboard_root = Path(directory)

    async def get_response(self, path, scope):
        path = "index.html" if path in {"", "."} else path
        if path not in PUBLIC_ASSETS:
            raise HTTPException(404)
        candidate = self._dashboard_root
        for part in Path(path).parts:
            candidate /= part
            if candidate.is_symlink():
                raise HTTPException(404)
        response = await super().get_response(path, scope)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
