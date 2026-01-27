import importlib.util
import json
import logging
import os
import sys
import tempfile
from datetime import datetime


def setup_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stdout,
    )
    return logging.getLogger("lafayette911")


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    payload = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "event": event,
        **fields,
    }
    logger.info(json.dumps(payload, sort_keys=True))


def atomic_write_text(path: str, text: str) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def atomic_write_bytes(path: str, data: bytes) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=directory, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def get_rss_bytes() -> int:
    if importlib.util.find_spec("psutil"):
        import psutil

        process = psutil.Process(os.getpid())
        return int(process.memory_info().rss)

    if importlib.util.find_spec("resource"):
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return int(rss)
        return int(rss * 1024)

    return 0
