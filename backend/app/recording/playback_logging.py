"""Opt in only archive preparation metrics, not verbose camera/SDK logging."""

import logging

LOGGER_NAME = "backend.app.recording.playback"


def configure() -> None:
    """Install one stderr sink at application startup, independent of root level."""
    logger = logging.getLogger(LOGGER_NAME)
    if not any(getattr(handler, "_ccg_playback_metrics", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler._ccg_playback_metrics = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
