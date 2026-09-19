# Shared pytest fixtures for the test suite.
import sys
from pathlib import Path

import pytest

# Make sure the app package can be imported when tests are run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402


# Provide a Settings instance pointed at a temporary data directory for tests.
@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, external_fetch=False, background_jobs=False, dev_tools=True, admin_key="adm-key")
