# -*- coding: utf-8 -*-
"""測試 api char/fetch_user_character_details 的錯誤分支與 edge case。"""
import pytest

from lib import api


class FakeResponse:
    def __init__(self, status: int, data):
        self.status = status
        self._data = data

    async def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeRequest:
    def __init__(self, mapping):
        self.mapping = mapping

    async def post(self, url, **kwargs):
        return self.mapping[url]

    async def get(self, url, **kwargs):
        return self.mapping[url]


class FakeContext:
    def __init__(self, mapping):
        self.request = FakeRequest(mapping)


@pytest.mark.asyncio
async def test_fetch_user_character_details_no_name_items(monkeypatch):
    async def fake_union(_ctx):
        return "u", "intl", "area"
    monkeypatch.setattr(api, "fetch_union_info", fake_union)
    ctx = FakeContext({api.CHAR_DETAILS_URL: FakeResponse(200, {"data": {}})})
    ok = await api.fetch_user_character_details(ctx, [], "nick")
    assert ok is False


@pytest.mark.asyncio
async def test_fetch_user_character_details_bad_status(monkeypatch):
    async def fake_union(_ctx):
        return "u", "intl", "area"
    monkeypatch.setattr(api, "fetch_union_info", fake_union)
    ctx = FakeContext({api.CHAR_DETAILS_URL: FakeResponse(500, {})})
    ok = await api.fetch_user_character_details(ctx, [("nc1", "NAME1")], "nick")
    assert ok is False
