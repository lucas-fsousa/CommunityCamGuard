"""Keep HTTP access metadata without persisting query strings in the bundled server."""

import logging
from copy import deepcopy

from uvicorn.config import LOGGING_CONFIG


class WithoutQuery(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        record.msg = '%s - "%s %s HTTP/%s" %d'
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        else:
            # Do not retain an unexpected preformatted request target after a server
            # logging contract change. The compatibility test must be updated instead.
            record.args = ("unknown", "UNKNOWN", "[redacted]", "?", 0)
        return True


def access_log_config() -> dict:
    config = deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["without_query"] = {"()": WithoutQuery}
    config["loggers"]["uvicorn.access"]["filters"] = ["without_query"]
    return config
