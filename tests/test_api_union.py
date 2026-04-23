# -*- coding: utf-8 -*-
"""針對 api 中較重的資料處理邏輯做單元測試（使用假 context，不打網路）。"""

import json
from pathlib import Path

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
        self.posts = []

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.mapping[url]

    async def get(self, url, **kwargs):
        return self.mapping[url]


class FakeContext:
    def __init__(self, mapping):
        self.request = FakeRequest(mapping)


@pytest.mark.asyncio
async def test_fetch_union_raid_data(tmp_path, monkeypatch):
    # 準備輸出路徑改到 tmp
    out_dir = tmp_path / "UnionRaid"
    out_file = out_dir / "tmp.json"
    monkeypatch.setattr(api, "UNION_RAID_TEMP_FILE", out_file)

    sample = {
        "data": {
            "participate_data": [
                {
                    "day": 1,
                    "username": "A",
                    "total_damage": 100,
                    "level": 1,
                    "step": 1,
                    "is_final_hit": False,
                    "squad_id": [{"tid": 123456, "lv": 10}, {"tid": 223456, "lv": 8}],
                },
                {
                    "day": 2,
                    "username": "A",
                    "total_damage": 200,
                    "level": 2,
                    "step": 2,
                    "is_final_hit": True,
                    "squad_id": [{"tid": 323456, "lv": 9}],
                },
                {
                    "day": 0,  # 會被過濾
                    "username": "B",
                    "squad_id": [],
                },
            ]
        }
    }
    mapping = {api.UNION_RAID_URL: FakeResponse(200, sample)}
    ctx = FakeContext(mapping)

    ok = await api.fetch_union_raid_data(ctx, "gid", "intl", "area")
    assert ok is True

    # 確認檔案存在且結構正確（檔名會以 stem + union_id + 時間組合）
    files = list(out_dir.glob("tmp_*json"))
    assert files, "輸出檔案未建立"
    content = json.loads(files[0].read_text(encoding="utf-8"))
    assert content["union_id"] == "gid"
    pdata = content["participate_data"]
    assert len(pdata) == 1
    assert pdata[0]["username"] == "A"
    # data 倒序、lv 取最大、tid 取前 4 碼
    assert len(pdata[0]["data"]) == 2
    assert pdata[0]["data"][0]["squad_id"] == [3234]
    assert pdata[0]["lv"] == 10


@pytest.mark.asyncio
async def test_fetch_union_raid_data_of_guild_season(tmp_path, monkeypatch):
    out_dir = tmp_path / "UnionRaid"
    out_file = out_dir / "tmp.json"
    monkeypatch.setattr(api, "UNION_RAID_TEMP_FILE", out_file)

    sample = {
        "data": {
            "participate_data": [
                {
                    "day": 1,
                    "nickname": "A",
                    "total_damage": 100,
                    "level": 1,
                    "step": 1,
                    "is_final_hit": False,
                    "squad": [{"tid": 123456, "lv": 10}],
                }
            ]
        }
    }
    mapping = {api.UNION_RAID_SEASON_URL: FakeResponse(200, sample)}
    ctx = FakeContext(mapping)

    ok = await api.fetch_union_raid_data_of_guild_season(ctx, "area-1", "gid", "123")
    assert ok is True
    assert ctx.request.posts == [
        (
            api.UNION_RAID_SEASON_URL,
            {"data": {"area_id": "area-1", "guild_id": "gid", "season_id": "10000123"}},
        )
    ]

    files = list(out_dir.glob("tmp_*json"))
    assert files, "輸出檔案未建立"
    content = json.loads(files[0].read_text(encoding="utf-8"))
    assert content == {
        "union_id": "gid",
        "participate_data": [
            {
                "username": "A",
                "lv": 10,
                "data": [
                    {
                        "damage": 100,
                        "level": 1,
                        "step": 1,
                        "is_final_hit": False,
                        "squad_id": [1234],
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_fetch_nikkes_info(tmp_path, monkeypatch):
    target = tmp_path / "nikkes_info.json"
    monkeypatch.setattr(api, "NIKKES_INFO_FILE", target)
    monkeypatch.setattr(api, "read_cloud_data_json", lambda path, default=None: [])
    monkeypatch.setattr(
        api,
        "write_data_text_and_sync_if_changed",
        lambda path, text, encoding="utf-8": path.write_text(text, encoding=encoding),
    )

    sample = [
        {
            "id": 12345,
            "class": "c",
            "corporation": "corp",
            "element_id": {"element": {"element": "fire"}},
            "name_code": "nc",
            "name_localkey": {"name": "nick"},
            "original_rare": 3,
            "shot_id": {"element": {"weapon_type": "gun"}},
            "use_burst_skill": "Step1",
        }
    ]
    mapping = {api.NIKKES_INFO_URL: FakeResponse(200, sample)}
    ctx = FakeContext(mapping)

    ok = await api.fetch_nikkes_info(ctx)
    assert ok is True
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data[0]["id"] == 1234  # 只取前 4 碼並轉 int
    assert data[0]["element"] == "fire"
    assert data[0]["weapon_type"] == "gun"


@pytest.mark.asyncio
async def test_fetch_nikkes_info_merges_with_cloud(tmp_path, monkeypatch):
    target = tmp_path / "nikkes_info.json"
    monkeypatch.setattr(api, "NIKKES_INFO_FILE", target)
    monkeypatch.setattr(
        api,
        "write_data_text_and_sync_if_changed",
        lambda path, text, encoding="utf-8": path.write_text(text, encoding=encoding),
    )
    monkeypatch.setattr(
        api,
        "read_cloud_data_json",
        lambda path, default=None: [
            {
                "id": 1001,
                "class": "old-class",
                "corporation": "old-corp",
                "element": "water",
                "name_code": "old_nc",
                "name": "Old Nikke",
                "original_rare": 2,
                "weapon_type": "smg",
                "use_burst_skill": "Step1",
            },
            {
                "id": 1234,
                "class": "old-c",
                "corporation": "old-corp",
                "element": "wind",
                "name_code": "nc",
                "name": "Old Name",
                "original_rare": 2,
                "weapon_type": "old-gun",
                "use_burst_skill": "OldStep",
            },
        ],
    )

    sample = [
        {
            "id": 12345,
            "class": "c",
            "corporation": "corp",
            "element_id": {"element": {"element": "fire"}},
            "name_code": "nc",
            "name_localkey": {"name": "nick"},
            "original_rare": 3,
            "shot_id": {"element": {"weapon_type": "gun"}},
            "use_burst_skill": "Step1",
        }
    ]
    mapping = {api.NIKKES_INFO_URL: FakeResponse(200, sample)}
    ctx = FakeContext(mapping)

    ok = await api.fetch_nikkes_info(ctx)
    assert ok is True
    data = json.loads(target.read_text(encoding="utf-8"))
    assert [item["name_code"] for item in data] == ["old_nc", "nc"]
    assert data[1]["name"] == "nick"
    assert data[1]["weapon_type"] == "gun"
    assert data[1]["use_burst_skill"] == "Step1"


@pytest.mark.asyncio
async def test_fetch_function_names_merges_with_cloud(tmp_path, monkeypatch):
    target = tmp_path / "function_names.json"
    monkeypatch.setattr(api, "FUNCTION_NAMES_FILE", target)
    monkeypatch.setattr(
        api,
        "write_data_text_and_sync_if_changed",
        lambda path, text, encoding="utf-8": path.write_text(text, encoding=encoding),
    )
    monkeypatch.setattr(
        api,
        "read_cloud_data_json",
        lambda path, default=None: [
            {"function_name": "Existing", "function_id_list": [70001, 70002]},
            {"function_name": "CloudOnly", "function_id_list": [70003]},
        ],
    )

    sample = [
        {"description_localkey": "Existing", "state_effect_id_list": [70002, 70004]},
        {"description_localkey": "New", "state_effect_id_list": [70005]},
        {"description_localkey": "Ignored", "state_effect_id_list": [60001]},
    ]
    mapping = {api.FUNCTION_NAMES_URL: FakeResponse(200, sample)}
    ctx = FakeContext(mapping)

    ok = await api.fetch_function_names(ctx)
    assert ok is True
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == [
        {"function_name": "CloudOnly", "function_id_list": [70003]},
        {"function_name": "Existing", "function_id_list": [70001, 70002, 70004]},
        {"function_name": "New", "function_id_list": [70005]},
    ]
