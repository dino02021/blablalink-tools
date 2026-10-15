"""Read-only, bounded raid workbook projection from a sealed problem and final plan."""

from dataclasses import asdict, dataclass
import hashlib

from domain.execution import check_execution
from domain.raid import FixedInputs
from domain.raid_view import build_raid_view
from domain.snapshots import canonical_json
from domain.view_grid import ViewCell, ViewTab


@dataclass(frozen=True)
class RaidWorkbookView:
    problem_id: str
    draft_plan_id: str
    raid_view_id: str
    code_sha: str
    tabs: tuple[ViewTab, ...]
    schema_version: int = 1

    def __post_init__(self):
        tabs = tuple(self.tabs)
        if (type(self.schema_version) is not int or self.schema_version != 1 or not tabs
                or any(not isinstance(tab, ViewTab) for tab in tabs)
                or len({tab.tab_key for tab in tabs}) != len(tabs)
                or len({tab.title.casefold() for tab in tabs}) != len(tabs)
                or sum(len(tab.rows)*len(tab.column_ids) for tab in tabs) > 500000
                or any(not isinstance(value, str) or not value for value in (self.problem_id, self.draft_plan_id, self.raid_view_id, self.code_sha))):
            raise ValueError("invalid bounded raid workbook view")
        object.__setattr__(self, "tabs", tabs)

    @property
    def view_id(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


def render_raid_workbook(problem, final) -> RaidWorkbookView:
    if len(problem.members) > 3000 or len(problem.history) > 9000 or len(final.warnings) > 9000:
        raise ValueError("raid workbook exceeds member or shot bounds")
    view = build_raid_view(problem, final)
    names = {member.member.key: member.display_name for member in problem.members}
    if any(member not in names for member, _ in view.warnings):
        raise ValueError("raid warning is outside roster")
    tabs = (_overview(view), _shots(view, final), _input_mirror(problem), _warnings(view, names), _bosses(final))
    return RaidWorkbookView(problem.problem_id, final.draft_plan_id, view.view_id, problem.code_sha, tabs)


def restore_raid_workbook(data: dict) -> RaidWorkbookView:
    tabs = tuple(ViewTab(**{**tab, "rows": tuple(tuple(ViewCell(**cell) for cell in row) for row in tab["rows"])}) for tab in data["tabs"])
    return RaidWorkbookView(**{**data, "tabs": tabs})


def _overview(view):
    columns = ("member_id", "display_name", "sync", "new_knife_count", *(f"boss_{boss}_{kind}" for boss in range(1, 6) for kind in ("damage", "stage")))
    top = [ViewCell(label, "header") for label in ("成員 ID", "名稱", "同步器", "新出刀數")]
    lower = [ViewCell() for _ in range(4)]
    merges = [(0, 2, column, column+1) for column in range(4)]
    for boss in range(1, 6):
        top.extend((ViewCell(f"BOSS {boss}", "header"), ViewCell()))
        lower.extend((ViewCell("傷害（億）", "header"), ViewCell("階段", "header")))
        start = 4+2*(boss-1)
        merges.append((0, 1, start, start+2))
    rows = [tuple(top), tuple(lower)]
    for row in view.rows:
        check_execution()
        cells = [ViewCell(row.member.key, "identity"), ViewCell(row.display_name),
            ViewCell(row.sync, format_kind="number"), ViewCell(row.new_knife_count, format_kind="number")]
        for boss in row.bosses:
            style = "history" if boss.is_all_history else "data"
            note = "含歷史與新出刀，逐刀來源見出刀明細" if any(shot.is_history for shot in boss.shots) and not boss.is_all_history else ""
            cells.extend((ViewCell(boss.damage_text, style, note), ViewCell(boss.stage_text, style, note)))
        rows.append(tuple(cells))
    return ViewTab("raid_overview", "出刀總覽", columns, ("group_header", "field_header", *(row.member.key for row in view.rows)), tuple(rows), tuple(merges), 2, 2, (0,))


def _shots(view, final):
    labels = ("成員 ID", "名稱", "刀 ID", "BOSS", "真實傷害", "傷害（億）", "階段", "歷史刀", "有效傷害")
    columns = ("member_id", "display_name", "knife_id", "boss", "damage_true", "damage_yi", "stage", "is_history", "effective_true")
    rows, ids = [tuple(ViewCell(label, "header") for label in labels)], ["header"]
    effective = dict(final.simulation.effective_true)
    for row in view.rows:
        check_execution()
        for boss in row.bosses:
            for shot in boss.shots:
                style = "history" if shot.is_history else "data"
                values = (row.member.key, row.display_name, shot.knife_id, boss.boss, shot.damage_true,
                    shot.damage_yi, shot.stage, shot.is_history, effective.get(shot.knife_id))
                rows.append(tuple(ViewCell(value, "identity" if index == 0 else style, format_kind="number" if index == 3 else "text") for index, value in enumerate(values)))
                ids.append(canonical_json([row.member.key, shot.knife_id]))
    return ViewTab("raid_shots", "出刀明細", columns, tuple(ids), tuple(rows), frozen_rows=1, frozen_columns=2, hidden_columns=(0, 2))


def _input_mirror(problem):
    labels = ("成員 ID", "名稱", "指定一", "指定二", "指定三", *(f"BOSS {boss} 傷害覆寫" for boss in range(1, 6)))
    columns = ("member_id", "display_name", "slot_1", "slot_2", "slot_3", *(f"damage_override_{boss}" for boss in range(1, 6)))
    rows, ids = [tuple(ViewCell(label, "header") for label in labels)], ["header"]
    inputs = dict(problem.fixed_inputs)
    for member in problem.members:
        check_execution()
        fixed = inputs.get(member.member, FixedInputs(("", "", "")))
        values = (member.member.key, member.display_name, *fixed.slots, *(str(value) if value is not None else None for value in fixed.damage_overrides_yi))
        rows.append(tuple(ViewCell(value, "identity" if index == 0 else "data") for index, value in enumerate(values)))
        ids.append(member.member.key)
    return ViewTab("raid_inputs_mirror", "指定刀輸入鏡像", columns, tuple(ids), tuple(rows), frozen_rows=1, frozen_columns=2, hidden_columns=(0,))


def _warnings(view, names):
    rows = [tuple(ViewCell(label, "header") for label in ("成員 ID", "名稱", "警示代碼"))]
    ids = ["header"]
    for index, (member, code) in enumerate(view.warnings):
        check_execution()
        rows.append((ViewCell(member, "identity"), ViewCell(names[member]), ViewCell(code, "unknown")))
        ids.append(canonical_json([member, code, index]))
    return ViewTab("raid_warnings", "排刀警示", ("member_id", "display_name", "warning_code"), tuple(ids), tuple(rows), frozen_rows=1, frozen_columns=2, hidden_columns=(0,))


def _bosses(final):
    rows = [tuple(ViewCell(label, "header") for label in ("階段", "BOSS", "剩餘真實血量", "缺口"))]
    ids = ["header"]
    for stage, remaining in enumerate(final.simulation.remaining_hp, 1):
        for boss, hp in enumerate(remaining, 1):
            is_deficit = boss in final.simulation.deficit_bosses and stage == 3
            rows.append((ViewCell(("I", "II", "III")[stage-1]), ViewCell(boss, format_kind="number"), ViewCell(hp, "failed" if is_deficit else "data"), ViewCell(is_deficit)))
            ids.append(f"{stage}:{boss}")
    rows.append((ViewCell("III 溢出"), ViewCell(5, format_kind="number"), ViewCell(final.simulation.phase_three_overflow_true), ViewCell(False)))
    ids.append("phase_three_overflow")
    return ViewTab("raid_bosses", "BOSS 狀態", ("stage", "boss", "remaining_true", "is_deficit"), tuple(ids), tuple(rows), frozen_rows=1, frozen_columns=1)
