"""Shared fixtures.

Tests run against an isolated data root so no project data is read or modified,
with MCP and AI disabled for deterministic, offline execution.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def isolated_environment(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("aurelia-data")
    os.environ["FINWELL_DATA_DIR"] = str(data_dir)
    os.environ["FINWELL_MCP_ENABLED"] = "0"
    os.environ["FINWELL_DISABLE_AI"] = "1"
    os.environ.pop("FINWELL_AI_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("FINWELL_ALERT_WEBHOOK", None)

    from core.config import reset_config_cache

    reset_config_cache()
    yield data_dir
    reset_config_cache()


@pytest.fixture
def config():
    from core.config import get_config, reset_config_cache

    reset_config_cache()
    return get_config()


@pytest.fixture
def store(config):
    from core.storage import create_dataset

    return create_dataset("Test dataset", kind="upload", config=config)


@pytest.fixture
def demo_store(config):
    from core.storage import create_dataset

    return create_dataset("Test demonstration", kind="demo", config=config)
