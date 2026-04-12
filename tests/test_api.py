# -*- coding: utf-8 -*-
"""lib.api 的基本單元測試（純邏輯，不打真網路）。"""

import asyncio
import pytest

from lib import api


class FakeResponse:
    def __init__(self, status: int, data):
        self.status = status
        self._data = data

    async def json(self):
        # 模擬 playwright 的非同步 json() 介面
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeRequest:
    """簡易的 request stub，根據 URL 回傳預先設定的 FakeResponse。"""

    def __init__(self, mapping):
        self.mapping = mapping

    async def post(self, url, **kwargs):
        return await self._get_resp(url)

    async def get(self, url, **kwargs):
        return await self._get_resp(url)

    async def _get_resp(self, url):
        resp = self.mapping.get(url)
        if resp is None:
            return FakeResponse(404, {})
        # 若提供的是 coroutine，先 await 取得 FakeResponse
        if asyncio.iscoroutine(resp):
            return await resp
        return resp


class FakeContext:
    def __init__(self, mapping):
        self.request = FakeRequest(mapping)


@pytest.mark.asyncio
async def test_check_login_ok(monkeypatch):
    mapping = {
        api.CHECKLOGIN_URL: FakeResponse(200, {"msg": "ok"}),
    }
    ctx = FakeContext(mapping)
    assert await api.check_login(ctx) is True


@pytest.mark.asyncio
async def test_check_login_status_fail(monkeypatch):
    mapping = {api.CHECKLOGIN_URL: FakeResponse(500, {})}
    ctx = FakeContext(mapping)
    assert await api.check_login(ctx) is False


@pytest.mark.asyncio
async def test_fetch_union_info_ok():
    sample = {
        "data": {
            "card": {
                "guild_id": "g1",
                "intl_open_id": "io1",
                "nikke_area_id": "na1",
            }
        }
    }
    mapping = {api.UNIONINFO_URL: FakeResponse(200, sample)}
    ctx = FakeContext(mapping)
    union_id, intl_open_id, nikke_area_id = await api.fetch_union_info(ctx)
    assert (union_id, intl_open_id, nikke_area_id) == ("g1", "io1", "na1")


@pytest.mark.asyncio
async def test_fetch_union_info_invalid():
    mapping = {api.UNIONINFO_URL: FakeResponse(500, {})}
    ctx = FakeContext(mapping)
    union_id, intl_open_id, nikke_area_id = await api.fetch_union_info(ctx)
    assert (union_id, intl_open_id, nikke_area_id) == (None, None, None)
