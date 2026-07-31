"""Shared logging configuration.

Console output stays terse and readable; the log file keeps everything,
including DEBUG lines and full tracebacks, for after-the-fact diagnosis.
"""

from datetime import datetime
from pathlib import Path
import logging
import sys

from app.config import LOG_DIR

_CONSOLE_FORMAT = "%(asctime)s  %(levelname)-7s  %(message)s"

_FILE_FORMAT = (
    "%(asctime)s  %(levelname)-7s  %(name)s:%(lineno)d  %(message)s"
)

_TIME_FORMAT = "%H:%M:%S"

_configured = False


def setup_logging(
    run_name: str = "run",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> Path | None:
    """Configure root logging. Returns the path of the log file.

    Safe to call more than once; only the first call takes effect.
    """

    global _configured

    if _configured:
        return None

    root = logging.getLogger()
    root.setLevel(min(console_level, file_level))

    # tqdm writes its progress bars to stderr, so logging goes to stdout to
    # avoid the two interleaving into unreadable output.
    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT, _TIME_FORMAT))
    root.addHandler(console)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"{run_name}-{stamp}.log"

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    root.addHandler(file_handler)

    # These libraries are chatty at INFO and drown out the pipeline's own output.
    for noisy in (
        "httpx",
        "httpcore",
        "urllib3",
        "sentence_transformers",
        "transformers",
        "filelock",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True

    return log_path
