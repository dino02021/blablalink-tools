# -*- coding: utf-8 -*-
"""Web App URL contract tests."""

from __future__ import annotations

import importlib


def test_union_web_app_defaults_to_main_web_app(monkeypatch):
    monkeypatch.delenv("BLABLALINK_WEB_APP_URL", raising=False)
    monkeypatch.delenv("BLABLALINK_UNION_WEB_APP_URL", raising=False)

    import lib.constants as constants

    constants = importlib.reload(constants)

    assert constants.UNION_WEB_APP_URL == constants.WEB_APP_URL


def test_retired_union_web_app_override_is_ignored(monkeypatch):
    import lib.constants as constants

    retired_url = next(iter(constants.RETIRED_UNION_WEB_APP_URLS))
    monkeypatch.setenv("BLABLALINK_UNION_WEB_APP_URL", retired_url)

    constants = importlib.reload(constants)

    assert constants.UNION_WEB_APP_URL == constants.WEB_APP_URL
    assert constants.UNION_WEB_APP_URL != retired_url
