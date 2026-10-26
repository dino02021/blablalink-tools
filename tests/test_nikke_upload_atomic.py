"""Exercise the GUI pipeline with real gspread and an offline Sheets transport."""
from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import gspread
import pytest
import requests
from google.auth.credentials import AnonymousCredentials
from gspread.utils import a1_range_to_grid_range

from lib import healthcheck_config as config
from lib.google_sheets import auth, healthcheck as source
from lib.google_sheets.transport import RequestLimiter, SheetsSession
from lib.gui import nikke_update as gui
from lib.gui.dialogs import nikke_upload as helpers
from lib.jobs import nikke_batch_download as download, nikke_upload as upload


ELEMENTS = [("電擊", 6), ("水冷", 7), ("燃燒", 4), ("風壓", 7), ("鐵甲", 10)]


def _headers(roles):
    names, groups = ["成員"], [""]
    for role in roles:
        names.extend([role["name"]] + [""] * 11)
        block = [""] * 12
        for position, label in [(0, "突破"), (1, "技能"), (4, "裝備 (頭/手/身/腳)"), (8, "收藏品"), (9, "詞條")]:
            block[position] = label
        groups.extend(block)
    return [names, groups]


class SheetsTransport(requests.Session):
    def __init__(self, count):
        super().__init__()
        self.members = [{"nickname": f"Member{i:02}", "member_id": f"id{i:02}"} for i in range(count)]
        self.roles, self.grids = [], {}
        for element, number in ELEMENTS:
            roles = [{"name": f"Role{element}{i}", "name_code": len(self.roles) + i + 1001, "element": element, "type": "輸出"} for i in range(number)]
            self.roles.extend(roles)
            self.grids["角色練度_" + element] = _headers(roles) + [[member["nickname"]] + [1] * (12 * number) for member in self.members]
        self.grids["角色練度_變化"] = [[""]] + _headers(self.roles) + [[member["nickname"]] + ["+5"] * (12 * len(self.roles)) for member in self.members]
        self.grids["成員名稱"] = [["名稱", "同步", "Blabla ID"]] + [[member["nickname"], 400, member["member_id"]] for member in self.members]
        self.common_rows = [{key: role.get(key, "") for key in source.HEALTHCHECK_KEYS} for role in self.roles]
        self.common_grid = [source.HEALTHCHECK_HEADERS] + [[row[key] for key in source.HEALTHCHECK_KEYS] for row in self.common_rows]
        self.calls, self.bodies = [], []
        self.phase = "snapshot"
        self.fail_read_title = ""
        self.fail_write_title = ""
        self.is_timeout_after_commit = False
        self.is_timeout_before_commit = False
        self.read_quota_responses = 0
        self.write_quota_responses = 0
        self.now, self.waits = 0.0, []
        self.limiter = RequestLimiter(clock=lambda: self.now, sleep=self._advance)
        self.notifications, self.results, self.statuses = [], {}, []

    def request(self, method, url, params=None, json=None, **kwargs):
        path, params = unquote(urlsplit(url).path), params or {}
        self.calls.append((self.phase, method.lower(), path))
        if self.phase == "upload" and method.lower() == "get" and self.read_quota_responses:
            self.read_quota_responses -= 1
            return self._response({"error": {"code": 429, "message": "read quota"}}, 429)
        if method.lower() != "get":
            self.bodies.append(deepcopy(json))
            if self.write_quota_responses:
                self.write_quota_responses -= 1
                return self._response({"error": {"code": 429, "message": "write quota"}}, 429)
            if self.is_timeout_before_commit:
                self.is_timeout_before_commit = False
                raise requests.Timeout("connection lost before commit")
            self._write(json)
            if self.is_timeout_after_commit:
                self.is_timeout_after_commit = False
                raise requests.Timeout("response lost after atomic commit")
            return self._response({"replies": [{} for _ in json.get("requests", [])]})
        if path.endswith("/values:batchGet"):
            return self._response({"valueRanges": [self._read(item) for item in params["ranges"]]})
        if "/values/" in path:
            return self._response(self._read(path.split("/values/", 1)[1], params.get("majorDimension", "ROWS")))
        grids = {source.HEALTHCHECK_WORKSHEET: self.common_grid} if path.endswith("/common") else self.grids
        return self._response({"spreadsheetId": "common" if path.endswith("/common") else "target", "properties": {"title": "Audit"}, "sheets": [
            {"properties": {"sheetId": index, "title": title, "gridProperties": {"rowCount": len(grid), "columnCount": max(map(len, grid))}}}
            for index, (title, grid) in enumerate(grids.items())
        ]})

    def _read(self, full_range, major="ROWS"):
        title, notation = full_range.split("!", 1)
        title = title.strip("'").replace("''", "'")
        if self.phase == "upload" and title == self.fail_read_title and not notation.startswith(("1:", "2:", "3:")):
            raise gspread.exceptions.APIError(self._response({"error": {"code": 429, "message": "injected read quota"}}, 429))
        grid = self.common_grid if title == source.HEALTHCHECK_WORKSHEET else self.grids[title]
        bounds = a1_range_to_grid_range(notation)
        first_row, last_row = bounds.get("startRowIndex", 0), bounds.get("endRowIndex", len(grid))
        first_col, last_col = bounds.get("startColumnIndex", 0), bounds.get("endColumnIndex", max(map(len, grid)))
        values = [list(row[first_col:last_col]) for row in grid[first_row:last_row]]
        if major == "COLUMNS":
            values = [[row[col] if col < len(row) else "" for row in values] for col in range(last_col-first_col)]
        return {"range": full_range, "majorDimension": major, "values": values}

    def _write(self, body):
        operations = []
        for item in body.get("data", []):
            title, notation = item["range"].split("!", 1)
            operations.append((title.strip("'"), a1_range_to_grid_range(notation), item["values"]))
        for item in body.get("requests", []):
            assert set(item) == {"updateCells"}, "training upload must never insert or resize rows"
            update = item["updateCells"]
            assert update["fields"] == "userEnteredValue"
            start = update["start"]
            title = list(self.grids)[start["sheetId"]]
            rows = [[next(iter(cell.get("userEnteredValue", {}).values()), "") for cell in row["values"]] for row in update["rows"]]
            operations.append((title, {"startRowIndex": start["rowIndex"], "startColumnIndex": start["columnIndex"]}, rows))
        if any(title == self.fail_write_title for title, _, _ in operations):
            raise gspread.exceptions.APIError(self._response({"error": {"code": 403, "message": "protected target sheet"}}, 403))
        for title, bounds, rows in operations:
            for offset, values in enumerate(rows):
                row = self.grids[title][bounds.get("startRowIndex", 0) + offset]
                col = bounds.get("startColumnIndex", 0)
                assert col + len(values) <= len(row)
                row[col:col + len(values)] = values

    @staticmethod
    def _response(body, status=200):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(body).encode("utf-8")
        response._content_consumed = True
        return response

    def _advance(self, seconds):
        self.now += seconds
        self.waits.append(seconds)


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    transport = SheetsTransport(30)
    def client():
        session = SheetsSession(AnonymousCredentials(), transport.limiter, sleep=transport._advance, jitter=lambda: 0)
        monkeypatch.setattr(session, "_request_once", transport.request)
        return gspread.Client(auth=None, session=session)

    def snapshot():
        source.read_healthcheck_rows()
        return config.HealthcheckSnapshot("common", source.healthcheck_rows_fingerprint(transport.common_rows), transport.roles,
            [(role["name_code"], role["name"]) for role in transport.roles], {str(role["name_code"]): [1, 2, 3] for role in transport.roles})

    async def union_info(ctx):
        return "guild", "self", "area"

    async def guild_members(*args, **kwargs):
        return "guild", transport.members

    async def characters(*args, **kwargs):
        return [{"name_code": role["name_code"]} for role in transport.roles]

    async def session_context(state, callback):
        return await callback(object())

    async def details(ctx, checklist, nickname, **kwargs):
        document = {"nickname": nickname, "member_id": kwargs["intl_open_id"], "healthcheck_context": kwargs["healthcheck_context"],
                    "expected_name_codes": [str(role["name_code"]) for role in transport.roles],
                    "character_details": [{**role, "breakthrough": 2, "skill1_lv": 2, "skill2_lv": 2, "ulti_skill_lv": 2,
                       "favorite_item_type": "N", "functions": [{"function_id": value, "function_value": 2} for value in [1, 2, 3]]} for role in transport.roles]}
        (tmp_path / (nickname + ".json")).write_text(json.dumps(document), encoding="utf-8")
        return True

    real_download, real_upload = download.run_nikke_batch_download, upload.run_nikke_upload

    def download_flow(*args, **kwargs):
        transport.phase = "download"
        result = real_download(*args, **kwargs)
        transport.results["download"] = result
        return result

    def upload_flow(*args, **kwargs):
        transport.phase = "upload"
        result = real_upload(*args, **kwargs)
        transport.results["upload"] = result
        return result

    for module, attributes in [
        (source, {"get_gspread_client": client, "get_common_config_spreadsheet_id": lambda: "common"}),
        (auth, {"get_gspread_client": client}),
        (config, {"get_common_config_spreadsheet_id": lambda: "common"}),
        (download, {"get_gspread_client": client, "resolve_account": lambda _: "account", "load_session": lambda _: {"fake": True},
            "run_with_session_context": session_context, "fetch_union_info": union_info, "fetch_guild_members": guild_members,
            "fetch_user_characters": characters, "fetch_user_character_details": details,
            "build_character_details_path": lambda name: tmp_path / (name + ".json"), "resolve_character_sheet_target": lambda _: ("target", "Audit"), "log_exc": lambda *args: None}),
        (upload, {"CHAR_DETAILS_DIR": tmp_path, "resolve_character_sheet_for_account": lambda *args, **kwargs: ("guild", "target", "Audit"), "log_exc": lambda *args: None}),
        (gui, {"load_healthcheck_snapshot": snapshot, "run_nikke_batch_download": download_flow, "run_nikke_upload": upload_flow, "resolve_character_sheet_target": lambda _: ("target", "Audit")}),
        (helpers, {"log_exc": lambda *args: None, "log_msg": lambda *args: None, "_notify_web_app_batch": lambda items, **kwargs: transport.notifications.extend(items)}),
    ]:
        for name, value in attributes.items():
            monkeypatch.setattr(module, name, value)
    app = SimpleNamespace(_last_slug="account", root=SimpleNamespace(after=lambda delay, callback: callback()),
        _set_nikke_status=lambda *args: transport.statuses.append(args), _set_busy=lambda *args: None,
        nikke_status_var=SimpleNamespace(set=lambda msg: None), nikke_status_label=SimpleNamespace(configure=lambda **kwargs: None))
    transport.run = lambda: gui._process_single_account(app)
    return transport


def test_thirty_member_update_reads_in_batches_and_writes_atomically(pipeline):
    pipeline.run()
    assert pipeline.results["upload"].ok
    assert len(pipeline.bodies) == 1
    assert sum(method == "get" for _, method, _ in pipeline.calls) <= 22
    assert pipeline.grids["角色練度_電擊"][2][1] == 2
    assert pipeline.grids["角色練度_變化"][3][1] == "+6"


@pytest.mark.parametrize("title", ["角色練度_鐵甲", "角色練度_變化"])
def test_failed_sheet_write_leaves_every_grid_unchanged_and_reports_failure(pipeline, title):
    pipeline.fail_write_title = title
    original = deepcopy(pipeline.grids)
    pipeline.run()
    assert not pipeline.results["upload"].ok
    assert pipeline.results["upload"].stats.get("ok_count", 0) == 0
    assert pipeline.grids == original
    assert pipeline.notifications == []


@pytest.mark.parametrize("title", ["角色練度_鐵甲", "角色練度_變化", "成員名稱"])
def test_failed_source_read_cannot_become_an_empty_mapping(pipeline, title):
    pipeline.fail_read_title = title
    pipeline.run()
    assert not pipeline.results["upload"].ok
    assert pipeline.bodies == []


def test_same_id_rename_updates_training_and_changes(pipeline):
    pipeline.members[0]["nickname"] = "New Name"
    pipeline.run()
    assert pipeline.results["upload"].ok
    assert pipeline.grids["角色練度_電擊"][2][1] == 2
    assert pipeline.grids["角色練度_變化"][3][1] == "+6"


def test_partial_download_stops_before_upload_and_preserves_denominator(pipeline, monkeypatch):
    original = download.fetch_user_characters

    async def unavailable(*args, **kwargs):
        if kwargs.get("intl_open_id") == "id29":
            return []
        return await original(*args, **kwargs)

    monkeypatch.setattr(download, "fetch_user_characters", unavailable)
    pipeline.run()
    assert not pipeline.results["download"].ok
    assert pipeline.results["download"].stats["total_count"] == 30
    assert "upload" not in pipeline.results
    assert pipeline.bodies == []


def test_rerun_does_not_write_unchanged_training_or_add_changes_again(pipeline):
    pipeline.run()
    original = deepcopy(pipeline.grids)
    pipeline.bodies.clear()
    pipeline.run()
    assert pipeline.results["upload"].ok
    assert pipeline.grids == original
    assert pipeline.bodies == []


def test_lost_write_response_is_verified_without_reapplying_changes(pipeline):
    pipeline.is_timeout_after_commit = True
    pipeline.run()
    assert pipeline.results["upload"].ok
    assert len(pipeline.bodies) == 1
    assert pipeline.grids["角色練度_變化"][3][1] == "+6"


def test_unconfirmed_write_reports_failure_without_retrying_mutation(pipeline):
    pipeline.is_timeout_before_commit = True
    original = deepcopy(pipeline.grids)
    pipeline.run()
    assert not pipeline.results["upload"].ok
    assert len(pipeline.bodies) == 1
    assert pipeline.grids == original
    assert pipeline.notifications == []


@pytest.mark.parametrize("kind", ["read", "write"])
def test_quota_retry_completes_the_same_atomic_upload(pipeline, kind):
    setattr(pipeline, kind + "_quota_responses", 1)
    pipeline.run()
    assert pipeline.results["upload"].ok
    assert pipeline.waits == [1]
    assert pipeline.grids["角色練度_變化"][3][1] == "+6"
    if kind == "write":
        assert len(pipeline.bodies) == 2
        assert pipeline.bodies[0] == pipeline.bodies[1]


def test_multiple_clients_share_a_read_limit_across_updates(pipeline):
    for _ in range(3):
        pipeline.run()
        assert pipeline.results["upload"].ok
    assert pipeline.waits == [60.0]


def test_unowned_roles_are_explicit_skips_without_masking_download_errors(pipeline, monkeypatch):
    original = download.fetch_user_characters

    async def unowned(*args, **kwargs):
        if kwargs.get("intl_open_id") == "id29":
            return [{"name_code": 999999}]
        return await original(*args, **kwargs)

    monkeypatch.setattr(download, "fetch_user_characters", unowned)
    pipeline.run()
    assert pipeline.results["download"].ok
    assert pipeline.results["download"].stats["skipped_count"] == 1
    assert pipeline.results["download"].stats["total_count"] == 30
    assert pipeline.results["upload"].stats["ok_count"] == 29
    assert pipeline.grids["角色練度_電擊"][31][1] == 1


def test_all_unowned_roles_need_no_upload(pipeline, monkeypatch):
    async def unowned(*args, **kwargs):
        return [{"name_code": 999999}]

    monkeypatch.setattr(download, "fetch_user_characters", unowned)
    pipeline.run()
    assert pipeline.results["download"].stats["skipped_count"] == 30
    assert "upload" not in pipeline.results
    assert pipeline.statuses[-1][1] == "green"
    assert pipeline.bodies == []


def test_missing_target_is_not_reported_as_a_successful_single_account_skip(pipeline, monkeypatch):
    def missing_target(union):
        raise ValueError("找不到目標試算表")

    monkeypatch.setattr(download, "resolve_character_sheet_target", missing_target)
    pipeline.run()
    assert not pipeline.results["download"].ok
    assert pipeline.statuses[-1][1] == "red"
    assert "upload" not in pipeline.results


@pytest.mark.parametrize("count", [0, 29])
def test_missing_download_file_cannot_shrink_the_upload_denominator(pipeline, monkeypatch, count):
    original = gui.run_nikke_upload

    def missing_file(*args, **kwargs):
        kwargs["files"] = kwargs["files"][:count]
        return original(*args, **kwargs)

    monkeypatch.setattr(gui, "run_nikke_upload", missing_file)
    pipeline.run()
    assert not pipeline.results["upload"].ok
    assert pipeline.results["upload"].stats["total_count"] == 30
    assert pipeline.bodies == []


@pytest.mark.parametrize("title,row,column", [("成員名稱", 1, 0), ("角色練度_電擊", 2, 1), ("角色練度_變化", 3, 1)])
def test_changes_during_planning_abort_the_write(pipeline, monkeypatch, title, row, column):
    original = helpers._populate_change_updates

    def concurrent_edit(*args, **kwargs):
        original(*args, **kwargs)
        pipeline.grids[title][row][column] = "manually changed"

    monkeypatch.setattr(helpers, "_populate_change_updates", concurrent_edit)
    pipeline.run()
    assert not pipeline.results["upload"].ok
    assert pipeline.bodies == []
    assert pipeline.grids[title][row][column] == "manually changed"


def test_unchanged_fields_keep_their_existing_cumulative_changes(pipeline):
    pipeline.grids["角色練度_電擊"][2][2] = 2
    pipeline.run()
    assert pipeline.results["upload"].ok
    assert pipeline.grids["角色練度_變化"][3][1:3] == ["+6", "+5"]


@pytest.mark.parametrize("is_conflicting", [False, True])
def test_dual_element_roles_do_not_double_count_or_hide_conflicting_baselines(pipeline, is_conflicting):
    role = pipeline.roles[13]
    role["name"] = "拉毗：小紅帽"
    pipeline.common_rows[13]["name"] = role["name"]
    pipeline.common_grid[14][0] = role["name"]
    pipeline.grids["角色練度_燃燒"][0][1] = role["name"]
    pipeline.grids["角色練度_變化"][1][1 + 13 * 12] = role["name"]
    old_iron = pipeline.grids["角色練度_鐵甲"]
    pipeline.grids["角色練度_鐵甲"] = _headers([role] + pipeline.roles[-10:]) + [[row[0]] + [1] * 12 + row[1:] for row in old_iron[2:]]
    if is_conflicting:
        pipeline.grids["角色練度_鐵甲"][2][1] = 0
    pipeline.run()
    assert pipeline.results["upload"].ok is not is_conflicting
    if is_conflicting:
        assert pipeline.bodies == []
    else:
        assert pipeline.grids["角色練度_變化"][3][1 + 13 * 12] == "+6"
        assert len(pipeline.bodies) == 1
