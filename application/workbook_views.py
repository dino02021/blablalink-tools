"""Render snapshot-bound generated views, independently of provider and human inputs."""

from application.healthcheck import healthcheck_input_hash
from domain.execution import check_execution
from domain.healthcheck import ELEMENTS, evaluate_metric
from domain.snapshots import apply_overrides, canonical_json, metric_values, read_config, read_training
from domain.standard_layout import ABBREVIATIONS, GROUPS, METRICS
from domain.view_grid import ViewCell, ViewTab, WorkbookView

FIELD_LABELS = dict(zip((*METRICS, "trait_1", "trait_2", "trait_3"),
    ("突破", "一技", "二技", "爆裂", "頭", "手", "身", "腳", "收藏品", "詞條1", "詞條2", "詞條3")))

def render_healthcheck_view(config, training, members, display_names, result, overrides=()):
    if (result.config_id != config.snapshot_id or result.snapshot_id != training.snapshot_id
            or tuple(report.member for report in result.reports) != tuple(members)
            or result.input_hash != healthcheck_input_hash(members, display_names, overrides)):
        raise ValueError("view source differs from fixed calculation")
    rules, labels = read_config(config)
    label_by_role = {label.name_code: label for label in labels}
    records = {record.key: record for record in apply_overrides(read_training(training), overrides)}
    blocks = tuple((element, rule) for element in ELEMENTS for rule in rules if element in rule.elements)
    estimated = 6*(1+12*len(blocks)) + sum((2+len(members))*(2+12*sum(element in r.elements for r in rules)) for element in ELEMENTS)
    estimated += (len(members)+1)*(len(rules)+19)
    if estimated > 500000 or any(metric.metric_id not in (*METRICS, "trait_1", "trait_2", "trait_3") for rule in rules for metric in rule.metrics):
        raise ValueError("view exceeds bounded template or contains unsupported metric identities")
    standard = _standard_tab(blocks, label_by_role)
    training_tabs = tuple(_training_tab(element, tuple(rule for rule in rules if element in rule.elements),
        members, display_names, label_by_role, records) for element in ELEMENTS)
    return WorkbookView(result.config_id, result.snapshot_id, result.result_id, result.code_version,
        (standard, *training_tabs, _score_tab(members, display_names, rules, label_by_role, result), _report_tab(members, display_names, result)))


def _standard_tab(blocks, labels):
    columns = ["standard_row_label"]
    rows = [[ViewCell(label, "identity")] for label in ("屬性", "角色", "項目", "門檻", "評分係數", "總分權重")]
    merges = []
    previous_element, element_start = None, 1
    for element, rule in blocks:
        check_execution()
        start = len(columns)
        if previous_element is not None and element != previous_element:
            if start - element_start > 1:
                merges.append((0, 1, element_start, start))
            element_start = start
        display = labels.get(rule.name_code)
        metrics = {metric.metric_id: metric for metric in rule.metrics}
        field_ids = (*METRICS, "trait_1", "trait_2", "trait_3")
        for field in field_ids:
            metric = metrics.get(field)
            columns.append(_column_id(element, rule.name_code, field, metric.function_id if metric else None))
        rows[0].extend(ViewCell(element if previous_element != element and offset == 0 else None, "header") for offset in range(12))
        rows[1].extend(ViewCell((display.display_name if display else rule.name_code) if offset == 0 else None, "header") for offset in range(12))
        rows[2].extend(ViewCell(None, "header") for _ in range(12))
        trait_names = [dict(display.trait_labels).get(metric.function_id, metric.function_id) if display else metric.function_id
                       for field in field_ids[9:] if (metric := metrics.get(field)) is not None]
        for c0, c1, title in GROUPS:
            label = title + (" (" + "/".join(ABBREVIATIONS.get(name, name) for name in trait_names) + ")" if title == "詞條" and trait_names else "")
            rows[2][start+c0] = ViewCell(label, "header")
            if c1-c0 > 1:
                merges.append((2, 3, start+c0, start+c1))
        for row, field, style in ((3, "threshold", "standard"), (4, "coefficient", "coefficient")):
            rows[row].extend(ViewCell(getattr(metrics[metric], field) if metric in metrics else None, style) for metric in field_ids)
        rows[5].extend(ViewCell(rule.total_weight if offset == 0 else None, "weight", format_kind="number") for offset in range(12))
        merges.extend(((1, 2, start, start+12), (5, 6, start, start+12)))
        previous_element = element
    if blocks:
        merges.append((0, 1, element_start, len(columns)))
    return ViewTab("standards", "角色練度標準_唯讀", tuple(columns), tuple(f"header_{i}" for i in range(6)), tuple(tuple(r) for r in rows), tuple(merges), 6, 1)


def _training_tab(element, rules, members, names, labels, records):
    columns = ["member_identity", "display_name"]
    header = [ViewCell("成員ID", "header"), ViewCell("名稱", "header")]
    subheader = [ViewCell(None, "header"), ViewCell(None, "header")]
    merges = []
    for rule in rules:
        label = labels.get(rule.name_code)
        metric_by_id = {metric.metric_id: metric for metric in rule.metrics}
        fields = (*METRICS, "trait_1", "trait_2", "trait_3")
        start = len(columns)
        header.extend(ViewCell((label.display_name if label else rule.name_code) if offset == 0 else None, "header") for offset in range(12))
        merges.append((0, 1, start, start+12))
        for field in fields:
            metric = metric_by_id.get(field)
            columns.append(_column_id(element, rule.name_code, field, metric.function_id if metric else None))
            title = dict(label.trait_labels).get(metric.function_id, metric.function_id) if label and metric and metric.function_id else FIELD_LABELS[field]
            subheader.append(ViewCell(title, "header"))
    rows = [tuple(header), tuple(subheader)]
    for member in members:
        check_execution()
        row = [ViewCell(member.key, "identity"), ViewCell(names.get(member.key, member.member_id), "identity")]
        for rule in rules:
            check_execution()
            record = records.get((member.key, rule.name_code))
            values = metric_values(rule, record) if record is not None and record.ownership_status == "owned" else {}
            metrics = {metric.metric_id: metric for metric in rule.metrics}
            for field in (*METRICS, "trait_1", "trait_2", "trait_3"):
                metric = metrics.get(field)
                if metric is None:
                    row.append(ViewCell())
                elif record is None or record.ownership_status == "unknown":
                    row.append(ViewCell(None, "unknown", "ownership_unknown"))
                elif record.ownership_status == "not_owned":
                    row.append(ViewCell(None, "not_owned", "not_owned"))
                else:
                    value = values[field]
                    evaluation = evaluate_metric(metric, value)
                    style = evaluation.status if evaluation.status in ("passed", "failed", "unknown") else "data"
                    row.append(ViewCell(value, style, evaluation.status))
        rows.append(tuple(row))
    return ViewTab("training_"+element, "角色練度_"+element, tuple(columns), ("roles", "metrics", *(m.key for m in members)), tuple(rows), tuple(merges), 2, 2, hidden_columns=(0,))


def _score_tab(members, names, rules, labels, result):
    role_scores = {(score.member_id, score.key): score.value for score in result.scores.roles}
    group_scores = {(score.member_id, score.key): score for score in result.scores.groups}
    role_ids = tuple(rule.name_code for rule in rules if any(key[1] == rule.name_code for key in role_scores))
    groups = tuple(group for group in (*ELEMENTS, "輔助") if any(key[1] == group for key in group_scores))
    columns = ("member_identity", "display_name", *(canonical_json(["role", role]) for role in role_ids),
        *(canonical_json(["group", group, field]) for group in groups for field in ("score", "rank")))
    header = [ViewCell(label, "header") for label in ("成員ID", "名稱", *(labels[role].display_name if role in labels else role for role in role_ids), *(group+suffix for group in groups for suffix in ("總分", "排名")))]
    rows = [tuple(header)]
    for member in members:
        check_execution()
        row = [ViewCell(member.key, "identity"), ViewCell(names.get(member.key, member.member_id), "identity")]
        row.extend(ViewCell(role_scores[(member.key, role)], format_kind="number") for role in role_ids)
        for group in groups:
            score = group_scores[(member.key, group)]
            row.extend((ViewCell(score.value, format_kind="number"), ViewCell(score.rank, format_kind="number")))
        rows.append(tuple(row))
    return ViewTab("scores", "評分結果", columns, ("headers", *(m.key for m in members)), tuple(rows), frozen_rows=1, frozen_columns=2, hidden_columns=(0,))


def _report_tab(members, names, result):
    rows = [(ViewCell("成員ID", "header"), ViewCell("名稱", "header"), ViewCell("完整", "header"), ViewCell("報告", "header"), ViewCell("警示", "header"))]
    for report in result.reports:
        rows.append((ViewCell(report.member.key, "identity"), ViewCell(names.get(report.member.key, report.member.member_id), "identity"),
            ViewCell(report.is_complete), ViewCell(report.text), ViewCell("\n".join(report.warnings))))
    return ViewTab("reports", "健檢報告", ("member_identity", "display_name", "complete", "report", "warnings"),
        ("headers", *(m.key for m in members)), tuple(rows), frozen_rows=1, frozen_columns=2, hidden_columns=(0,))


def _column_id(element, role, metric, function_id):
    return canonical_json([element, role, metric, function_id])
