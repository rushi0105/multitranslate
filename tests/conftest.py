"""Offline test setup: the fake provider is selected before mt.engine is imported."""
import os
import sys
from pathlib import Path

os.environ["MT_FAKE_PROVIDER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from mt.engine import Translator  # noqa: E402


@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "cache.sqlite3"


@pytest.fixture
def translator(cache_path):
    t = Translator(cache_path=cache_path, log=lambda m: None, workers=2, requests_per_second=1000)
    yield t
    t.close()


@pytest.fixture
def fake(translator):
    return translator.providers[0]
