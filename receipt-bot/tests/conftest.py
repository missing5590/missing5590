import pytest

from app.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(bot_token="", api_key="test-key", data_dir=tmp_path)
