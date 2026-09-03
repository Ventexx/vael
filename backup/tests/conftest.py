import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def _quiet_logger():
    """Prevent the module's operational logger from writing to real files
    or spamming stderr during tests; each test gets a clean, silent logger.
    """
    import logging

    import backup

    backup.logger.handlers.clear()
    backup.logger.addHandler(logging.NullHandler())
    backup.logger.propagate = False
    yield
    backup.logger.handlers.clear()
