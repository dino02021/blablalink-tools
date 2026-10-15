"""New-system tests are isolated from legacy settings and all external networking."""

import socket

import pytest


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", _deny_network)
    monkeypatch.setattr(socket.socket, "sendto", _deny_network)


def _deny_network(*args, **kwargs):
    raise AssertionError("Network is forbidden in the local mock test suite")
