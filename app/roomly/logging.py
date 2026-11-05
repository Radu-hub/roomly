"""JSON log lines on stdout, and business metrics in CloudWatch Embedded Metric Format."""

import json
import logging
import sys
import time
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # Our middleware logs each request, so uvicorn's access log would be a duplicate.
    logging.getLogger("uvicorn.access").disabled = True


def emit_metric(name: str, value: float = 1) -> None:
    """Print one EMF line. CloudWatch Logs turns it into a metric in the Roomly namespace.

    "Dimensions": [[]] means no dimensions, which keeps us within the free 10 custom metrics.
    """
    line = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": "Roomly",
                    "Dimensions": [[]],
                    "Metrics": [{"Name": name, "Unit": "Count"}],
                }
            ],
        },
        name: value,
    }
    print(json.dumps(line), flush=True)
