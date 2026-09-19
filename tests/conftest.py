import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, external_fetch=False, background_jobs=False, dev_tools=True, admin_key="adm-key")
