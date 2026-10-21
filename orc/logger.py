import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
LOGS_DIR = os.path.join(ORYN_ROOT, 'logs')

_loggers = {}


def _ensure_dir():
    os.makedirs(LOGS_DIR, exist_ok=True)


def get_logger(name='oryn'):
    """Get or create a named logger with rotating file handler."""
    if name in _loggers:
        return _loggers[name]

    _ensure_dir()

    logger = logging.getLogger(f'oryn.{name}')
    logger.setLevel(logging.DEBUG)

    # Main log file
    main_handler = RotatingFileHandler(
        os.path.join(LOGS_DIR, 'oryn.log'),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding='utf-8'
    )
    main_handler.setLevel(logging.DEBUG)

    # Console handler (warnings+)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)

    # Formatter
    fmt = logging.Formatter(
        '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    main_handler.setFormatter(fmt)
    console_handler.setFormatter(fmt)

    logger.addHandler(main_handler)
    logger.addHandler(console_handler)

    _loggers[name] = logger
    return logger


def log_command(command, output=None, error=None, returncode=0):
    """Log a command execution."""
    logger = get_logger('commands')
    status = 'OK' if returncode == 0 else f'FAIL({returncode})'
    logger.info(f"[{status}] {command}")
    if error:
        logger.warning(f"[STDERR] {error}")


def log_error(error, context=None):
    """Log an error with optional context."""
    logger = get_logger('errors')
    if context:
        logger.error(f"{context}: {error}")
    else:
        logger.error(str(error))


def log_ai(provider, model, tokens_in=0, tokens_out=0, latency_ms=0):
    """Log an AI API call."""
    logger = get_logger('ai')
    logger.info(f"{provider}/{model} in={tokens_in} out={tokens_out} latency={latency_ms}ms")


def log_startup():
    """Log Oryn startup."""
    logger = get_logger('oryn')
    logger.info("=" * 50)
    logger.info(f"Oryn started at {datetime.now().isoformat()}")
    logger.info("=" * 50)


def log_shutdown():
    """Log Oryn shutdown."""
    logger = get_logger('oryn')
    logger.info(f"Oryn shutdown at {datetime.now().isoformat()}")
