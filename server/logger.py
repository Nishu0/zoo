import json
import logging
import sys


class Logger(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        standard_attrs = {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "msecs",
            "message",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
        }

        for key, value in record.__dict__.items():
            if key not in standard_attrs:
                log_data[key] = value

        return json.dumps(log_data)

    def get_custom_logger(self, name: str = "app_logger"):
        log = logging.getLogger(name)
        log.setLevel(logging.INFO)

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(self)

        log.addHandler(handler)
        return log


logger = Logger().get_custom_logger("app_logger")
