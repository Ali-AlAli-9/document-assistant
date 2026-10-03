import logging
import os

_configured = False


def setup_logging():
    global _configured
    if _configured:
        return
    level_name = os.environ.get('LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s | %(name)-25s | %(levelname)-5s | %(message)s',
    )
    _configured = True
