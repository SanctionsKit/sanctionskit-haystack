import os
import socket

import pytest

os.environ["HAYSTACK_TELEMETRY_ENABLED"] = "false"


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """A test must fail if any transport escapes the explicit mock."""
    monkeypatch.setenv("SANCTIONSKIT_API_KEY", "not-a-real-key-offline-fixture")

    def reject_network(*args, **kwargs):
        raise AssertionError("These tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
