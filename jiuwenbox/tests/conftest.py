# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest

pytest_plugins = ["pytest_asyncio"]

from jiuwenbox.models.policy import SecurityPolicy


def pytest_addoption(parser):
    parser.addoption(
        "--server-endpoint",
        action="store",
        default=None,
        help="Server endpoint (host:port or URL). Default: 127.0.0.1:8321",
    )
    parser.addoption(
        "--proxy-port",
        action="store",
        default=None,
        type=int,
        help="Proxy listen port. Default: 8322",
    )
    parser.addoption(
        "--test-llm-endpoint",
        action="store",
        default=None,
        help="LLM endpoint URL for testing (e.g., https://api.openai.com/v1)",
    )
    parser.addoption(
        "--test-llm-api-key",
        action="store",
        default=None,
        help="LLM API key for testing",
    )
    parser.addoption(
        "--test-llm-model",
        action="store",
        default=None,
        help="LLM model name. Default: gpt-4o-mini",
    )


@pytest.fixture
def server_endpoint(pytestconfig) -> str:
    """Server endpoint as host:port or URL string."""
    return (
        pytestconfig.getoption("server_endpoint")
        or os.environ.get("JIUWENBOX_TEST_SERVER")
        or "127.0.0.1:8321"
    )


@pytest.fixture
def proxy_port(pytestconfig) -> int:
    """Proxy listen port."""
    return (
        pytestconfig.getoption("proxy_port")
        or int(os.environ.get("JIUWENBOX_PROXY_PORT", "8322"))
    )


@pytest.fixture
def server_host_port(server_endpoint):
    """Parse server_endpoint into (host, port) tuple."""
    endpoint = server_endpoint
    if "://" in endpoint:
        endpoint = endpoint.split("://", 1)[1]
    host, port = endpoint.rsplit(":", 1)
    return host, int(port)


@pytest.fixture
def server_url(server_endpoint):
    """Server endpoint as full URL."""
    return server_endpoint if "://" in server_endpoint else f"http://{server_endpoint}"


@pytest.fixture(scope="session")
def server_url_session(pytestconfig):
    """Session-scoped server URL."""
    endpoint = (
        pytestconfig.getoption("server_endpoint")
        or os.environ.get("JIUWENBOX_TEST_SERVER")
        or "127.0.0.1:8321"
    )
    return endpoint if "://" in endpoint else f"http://{endpoint}"


@pytest.fixture(scope="session")
def server_host_port_session(pytestconfig):
    """Session-scoped parsed host and port."""
    endpoint = (
        pytestconfig.getoption("server_endpoint")
        or os.environ.get("JIUWENBOX_TEST_SERVER")
        or "127.0.0.1:8321"
    )
    if "://" in endpoint:
        endpoint = endpoint.split("://", 1)[1]
    host, port = endpoint.rsplit(":", 1)
    return host, int(port)


@pytest.fixture(scope="session")
def proxy_port_session(pytestconfig):
    """Session-scoped proxy port."""
    return (
        pytestconfig.getoption("proxy_port")
        or int(os.environ.get("JIUWENBOX_PROXY_PORT", "8322"))
    )


@pytest.fixture(scope="session")
def test_llm_endpoint(pytestconfig):
    """LLM endpoint URL for testing."""
    return (
        pytestconfig.getoption("test_llm_endpoint")
        or os.environ.get("JIUWENBOX_TEST_LLM_ENDPOINT")
    )


@pytest.fixture(scope="session")
def test_llm_api_key(pytestconfig):
    """LLM API key for testing."""
    return (
        pytestconfig.getoption("test_llm_api_key")
        or os.environ.get("JIUWENBOX_TEST_LLM_API_KEY")
    )


@pytest.fixture(scope="session")
def test_llm_model(pytestconfig):
    """LLM model name for testing."""
    return (
        pytestconfig.getoption("test_llm_model")
        or os.environ.get("JIUWENBOX_TEST_LLM_MODEL")
        or "gpt-4o-mini"
    )


@pytest.fixture(scope="session")
def llm_available(docker_gateway_ip):
    """Extract LLM availability from topology check result."""
    return docker_gateway_ip.get("llm_available", False)


@pytest.fixture
def policy() -> SecurityPolicy:
    """Return the default policy."""
    return SecurityPolicy()
