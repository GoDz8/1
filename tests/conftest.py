"""Shared fixtures."""

import pytest

from axiom.config import Config
from axiom.data.robinhood_mcp import MockRobinhoodAdapter
from axiom.storage.db import Database


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    yield database
    database.close()


@pytest.fixture
def adapter():
    # Tier 2 NLV so position sizing has room to produce ENTERs in tests.
    return MockRobinhoodAdapter(nlv=5000.0)
