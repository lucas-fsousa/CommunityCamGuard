"""Check Uvicorn formatter compatibility without launching a server."""

import logging
import subprocess
import sys

from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter

from backend.app.access_logging import WithoutQuery, access_log_config


def test_query_removed_before_uvicorn_formatter():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:9000", "POST", "/api/discovery/scan?password=SYNTHETIC_SECRET&x=1", "1.1", 400), None)
    assert WithoutQuery().filter(record)
    rendered = AccessFormatter('%(client_addr)s "%(request_line)s" %(status_code)s', use_colors=False).format(record)
    assert "SYNTHETIC_SECRET" not in rendered and "?" not in rendered
    assert "/api/discovery/scan" in rendered and "400" in rendered
    assert "SYNTHETIC_SECRET" not in str(record.args)


def test_unknown_log_format_does_not_retain_preformatted_secret():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "password=SYNTHETIC_SECRET", (), None)
    WithoutQuery().filter(record)
    assert "SYNTHETIC_SECRET" not in record.getMessage()
    rendered = AccessFormatter('%(request_line)s %(status_code)s', use_colors=False).format(record)
    assert "[redacted]" in rendered and "SYNTHETIC_SECRET" not in rendered


def test_logging_config_is_isolated_and_filter_is_wired():
    result = access_log_config()
    assert result is not LOGGING_CONFIG
    assert result["loggers"]["uvicorn.access"]["filters"] == ["without_query"]
    assert result["filters"]["without_query"]["()"] is WithoutQuery
    assert "filters" not in LOGGING_CONFIG["loggers"]["uvicorn.access"]


def test_audio_summary_enabled_without_enabling_driver_debug():
    config = access_log_config()
    name = "backend.app.drivers.yoosee.audio_timing"
    assert config["loggers"][name] == {
        "handlers": ["default"], "level": "INFO", "propagate": False,
    }
    assert name not in LOGGING_CONFIG["loggers"]
    result = subprocess.run([sys.executable, "-c", """
import logging
from logging.config import dictConfig
from backend.app.access_logging import access_log_config
from backend.app.drivers.yoosee.audio_timing import DeliveryTiming
dictConfig(access_log_config())
DeliveryTiming().report('cam_0123456789abcdef01234567', elapsed=0,
                        opened=False, released=True)
logging.getLogger('backend.app.drivers.yoosee.p2p').info('HIDDEN_DRIVER_DETAIL')
"""], capture_output=True, text=True, timeout=10, check=True)
    assert result.stderr.count("rtsp_audio_timing") == 1
    assert "HIDDEN_DRIVER_DETAIL" not in result.stderr + result.stdout
