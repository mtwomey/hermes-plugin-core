"""
hermes_plugin_core/logging.py — Shared logging setup for Hermes native plugins.

Part of the hermes-plugin-core package. Import via:
    from hermes_plugin_core import setup_logging

Usage in a native plugin (__init__.py):
    from hermes_plugin_core import setup_logging
    log = setup_logging("<plugin-name>", _get_log_level())

    # Then in tools.py (get the same logger by name — no re-initialisation needed):
    import logging
    log = logging.getLogger("<plugin-name>")
    log.debug("tool called: arg=%s", value)
    log.error("call failed: %s", e)

Log file location:  ~/.hermes/logs/<tool_name>.log
Log rotation:       5 MB per file, 3 backups kept  (so max ~20 MB on disk)
Default level:      WARNING  (silent in normal operation)

Toggle (native plugin):
                    python setup.py log debug   →  sets plugins.config.<key>.log_level=DEBUG in config.yaml
                    python setup.py log quiet   →  removes plugins.config.<key>.log_level from config.yaml
                    __init__.py reads the level at startup via _get_log_level()
                    (both require a Hermes restart to take effect)
"""

import logging
import logging.handlers
import os
from pathlib import Path


def setup_logging(tool_name: str, level: str = "WARNING") -> logging.Logger:
    """
    Configure and return a logger for a Hermes plugin or MCP server.

    Args:
        tool_name:  Short name used for the log file, e.g. "imap", "google".
                    Log file will be at ~/.hermes/logs/<tool_name>.log
        level:      Logging level string: DEBUG | INFO | WARNING | ERROR | CRITICAL.
                    Default WARNING (quiet in normal operation).

    Returns:
        logging.Logger — use log.debug(), log.info(), log.warning(), log.error()
    """
    numeric_level = getattr(logging, level.upper(), logging.WARNING)

    # --- log directory -------------------------------------------------------
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    log_dir = hermes_home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{tool_name}.log"

    # --- logger --------------------------------------------------------------
    logger = logging.getLogger(tool_name)
    logger.setLevel(numeric_level)

    # Avoid duplicate handlers if setup_logging is called more than once
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler — 5 MB × 3 backups ≈ 20 MB max on disk
    fh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(numeric_level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # stderr handler for WARNING and above — visible in Hermes terminal output.
    # NOTE: For MCP servers, never write to stdout — it is the JSON-RPC wire.
    #       This module writes only to the rotating file and stderr, so it is
    #       safe in both native plugin and MCP server contexts.
    sh = logging.StreamHandler(stream=__import__("sys").stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    logger.debug("Logging initialised: tool=%s level=%s file=%s", tool_name, level, log_file)
    return logger
