import pytest

from aiarch.adapters.telemetry.tracer import JsonTracer
from aiarch.config import Settings
from aiarch.container import build_container
from aiarch.domain.models import Principal


@pytest.fixture
def spans() -> list[dict]:
    return []


@pytest.fixture
def container(spans):
    return build_container(Settings(), tracer=JsonTracer(sink=spans))


@pytest.fixture
def alice() -> Principal:
    return Principal("alice", "acme", frozenset({"all"}))


@pytest.fixture
def hr_user() -> Principal:
    return Principal("hema", "acme", frozenset({"all", "hr"}))
