import logging

from hermes import config

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    log_dir = config.logs_dir()
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(log_dir / "hermes.log"))
        except (PermissionError, OSError):
            pass  # Fall back to console-only logging

    logging.basicConfig(level=level, format=LOG_FORMAT, handlers=handlers, force=True)
