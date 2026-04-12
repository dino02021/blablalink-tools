# -*- coding: utf-8 -*-
"""測試 fetch_role_info 與 fetch_user_character_details。"""
import json
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
async def test_fetch_role_info_ok():
    sample = {"data": {"role_info": {"role_name": "YABI", "role_id": "25058712"}}}
    ctx = FakeContext({api.ROLEINFO_URL: FakeResponse(200, sample)})
    name, rid = await api.fetch_role_info(ctx)
    assert (name, rid) == ("YABI", "25058712")


@pytest.mark.asyncio
async def test_fetch_role_info_fail():
    ctx = FakeContext({api.ROLEINFO_URL: FakeResponse(500, {})})
    name, rid = await api.fetch_role_info(ctx)
    assert (name, rid) == (None, None)


@pytest.mark.asyncio
async def test_fetch_user_character_details(tmp_path, monkeypatch):
    # 改寫路徑
    monkeypatch.setattr(api, "CHAR_DETAILS_DIR", tmp_path)
    # mock fetch_union_info 直接回傳 (union, intl, area)
    async def fake_union(_ctx):
        return "u1", "intl1", "area1"
    monkeypatch.setattr(api, "fetch_union_info", fake_union)

    # state_effects: id 前7碼比對，function_value 轉百分比
    data_payload = {
        "data": {
            "state_effects": [
                {"id": "7000501", "function_details": [{"function_value": 1234}]},
            ],
            "character_details": [
                {
                    "name_code": "nc1",
                    "skill1_lv": 1,
                    "skill2_lv": 2,
                    "ulti_skill_lv": 3,
                    "head_equip_tier": 9,
                    "head_equip_lv": 5,
                    "arm_equip_tier": 1,
                    "arm_equip_lv": 2,
                    "torso_equip_tier": 3,
                    "torso_equip_lv": 4,
                    "leg_equip_tier": 5,
                    "leg_equip_lv": 6,
                    "favorite_item_tid": "20",
                    "favorite_item_lv": 9,
                    "arm_equip_option1_id": 7000501,
                }
            ],
        }
    }
    mapping = {api.CHAR_DETAILS_URL: FakeResponse(200, data_payload)}
    ctx = FakeContext(mapping)

    # name_items: list of (name_code, name)
    ok = await api.fetch_user_character_details(ctx, [("nc1", "NAME1")], "nick")
    assert ok is True

    files = list(tmp_path.glob("nick_character_details.json"))
    assert files, "應該寫出角色檔案"
    content = json.loads(files[0].read_text(encoding="utf-8"))
    assert isinstance(content, dict) and "character_details" in content
    assert content.get("nickname") == "nick"
    details = content["character_details"]
    assert isinstance(details, list) and details, "輸出內容應為非空 list"
    entry = details[0]
    assert entry["name"] == "NAME1"
    # functions 結果應加總並取兩位
    assert entry["functions"][0]["function_value"] == pytest.approx(12.34, rel=1e-3)
    assert entry["favorite_item_type"] == "SR"
    assert entry["favorite_item_lv"] == 15
