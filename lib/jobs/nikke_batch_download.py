# -*- coding: utf-8 -*-
"""Headless Nikke batch download job."""

from __future__ import annotations

import functools
from typing import Any, Dict, List, Tuple

from ..api import (
    build_character_details_path,
    fetch_union_info,
    fetch_guild_members,
    fetch_user_character_details,
    fetch_user_characters,
)
from ..constants import CHAR_DETAILS_DIR
from ..healthcheck_config import (
    HealthcheckSnapshot,
    build_download_context,
    ensure_healthcheck_snapshot_current,
    load_healthcheck_snapshot,
    validate_training_headers,
)
from ..google_sheets.auth import get_gspread_client
from ..log import log_exc
from ..utils import load_session, run_with_session_context
from .common import JobResult, resolve_account, run_coro_sync
from .nikke_upload import resolve_character_sheet_target


def _filter_checklist_items(
    checklist_items: List[Tuple[int, str]],
    name_codes: List[Any],
) -> List[Tuple[int, str]]:
    code_set: set[int] = set()
    for name_code in name_codes:
        try:
            code_set.add(int(name_code))
        except Exception:
            continue
    if not code_set:
        return []
    return [item for item in checklist_items if item[0] in code_set]


def _to_int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _build_user_character_lookup(user_characters: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for item in user_characters:
        if not isinstance(item, dict):
            continue
        name_code = item.get("name_code")
        if name_code is None:
            continue
        grade = item.get("grade")
        core = item.get("core")
        lookup[str(name_code)] = {
            "grade": grade,
            "core": core,
            "breakthrough": _to_int_or_zero(grade) + _to_int_or_zero(core),
        }
    return lookup


async def _batch_download_with_ctx(
    ctx,
    checklist_items: List[Tuple[int, str]],
    function_id_targets: Dict[str, List[int | None]],
    healthcheck_snapshot: HealthcheckSnapshot,
) -> Dict[str, Any]:
    guild_id, _fetched_open_id, nikke_area_id = await fetch_union_info(ctx)
    target_guild_id = str(guild_id or "").strip()
    target_nikke_area_id = str(nikke_area_id or "").strip()
    if not target_guild_id or not target_nikke_area_id:
        return {"guild_id": "", "files": [], "ok_count": 0, "total_count": 0}
    try:
        target_sheet_id, _target_sheet_name = resolve_character_sheet_target(target_guild_id)
    except ValueError as exc:
        return {"guild_id": target_guild_id, "files": [], "ok_count": 0, "total_count": 0, "skip_reason": str(exc)}
    spreadsheet = get_gspread_client().open_by_key(target_sheet_id)
    ensure_healthcheck_snapshot_current(healthcheck_snapshot)
    header_fingerprint = validate_training_headers(spreadsheet, healthcheck_snapshot)
    healthcheck_context = build_download_context(healthcheck_snapshot, target_sheet_id, header_fingerprint)
    guild_id, members = await fetch_guild_members(
        ctx,
        guild_id=target_guild_id,
        nikke_area_id=target_nikke_area_id,
    )
    if not guild_id:
        return {"guild_id": "", "files": [], "ok_count": 0, "total_count": 0}

    created_files: List[str] = []
    failed_members: List[Dict[str, str]] = []
    skipped_members: List[Dict[str, str]] = []
    expected_members: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    ok_count = 0
    total_count = 0
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("聯盟成員名單格式不完整，停止下載")
        nickname = str(member.get("nickname", "") or "").strip()
        member_id = str(member.get("member_id", "") or "").strip()
        if not nickname or not member_id:
            raise ValueError("聯盟成員名單缺少名稱或 Blabla ID，停止下載")
        output_key = str(build_character_details_path(nickname)).casefold()
        if member_id in seen_ids or output_key in seen_paths:
            raise ValueError("聯盟名單包含重複 ID 或資料檔名，停止下載")
        seen_ids.add(member_id)
        seen_paths.add(output_key)

        total_count += 1
        try:
            user_characters = await fetch_user_characters(
                ctx,
                intl_open_id=member_id,
                nikke_area_id=target_nikke_area_id,
            )
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            failed_members.append({"nickname": nickname, "member_id": member_id, "message": f"持有角色查詢失敗：{exc}"})
            continue
        if not user_characters:
            failed_members.append({"nickname": nickname, "member_id": member_id, "message": "無法取得持有角色資料"})
            continue
        name_codes = [item.get("name_code") for item in user_characters if isinstance(item, dict)]
        filtered = _filter_checklist_items(checklist_items, name_codes)
        if not filtered:
            skipped_members.append({"nickname": nickname, "member_id": member_id, "message": "未持有本次檢查角色"})
            continue
        user_character_lookup = _build_user_character_lookup(user_characters)

        try:
            is_downloaded = await fetch_user_character_details(
                ctx,
                filtered,
                nickname,
                intl_open_id=member_id,
                nikke_area_id=target_nikke_area_id,
                function_id_targets=function_id_targets,
                user_character_lookup=user_character_lookup,
                healthcheck_context=healthcheck_context,
            )
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            failed_members.append({"nickname": nickname, "member_id": member_id, "message": f"角色詳情下載失敗：{exc}"})
            continue
        if not is_downloaded:
            failed_members.append({"nickname": nickname, "member_id": member_id, "message": "角色詳情下載失敗或不完整"})
            continue
        outfile = build_character_details_path(nickname)
        if not outfile.exists():
            failed_members.append({"nickname": nickname, "member_id": member_id, "message": "下載完成後找不到資料檔"})
            continue
        ok_count += 1
        created_files.append(outfile.name)
        expected_members.append({"nickname": nickname, "member_id": member_id, "expected_name_codes": [str(code) for code, _ in filtered]})

    return {
        "guild_id": guild_id,
        "files": created_files,
        "ok_count": ok_count,
        "total_count": total_count,
        "failed_members": failed_members,
        "skipped_members": skipped_members,
        "expected_members": expected_members,
    }


def run_nikke_batch_download(
    account: str | None = None,
    function_id_targets: Dict[str, List[int | None]] | None = None,
    healthcheck_snapshot: HealthcheckSnapshot | None = None,
) -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="缺少帳號")

    state = load_session(slug)
    if not state:
        return JobResult(ok=False, message="找不到登入 Session")

    try:
        snapshot = healthcheck_snapshot or load_healthcheck_snapshot()
        if function_id_targets is not None and function_id_targets != snapshot.function_id_targets:
            raise ValueError("下載詞條設定與共用健檢名單不同")
        result = run_coro_sync(
            functools.partial(
                run_with_session_context,
                state,
                functools.partial(
                    _batch_download_with_ctx,
                    checklist_items=snapshot.checklist_items,
                    function_id_targets=snapshot.function_id_targets,
                    healthcheck_snapshot=snapshot,
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    guild_id = str(result.get("guild_id", "") or "").strip()
    files = [str(item).strip() for item in result.get("files", []) if str(item).strip()]
    ok_count = int(result.get("ok_count", 0) or 0)
    total_count = int(result.get("total_count", 0) or 0)
    skipped_members = result.get("skipped_members", [])
    failed_members = result.get("failed_members", [])
    skipped_count = len(skipped_members)
    outputs = {"char_details_dir": str(CHAR_DETAILS_DIR), "guild_id": guild_id, "files": files,
               "failed_members": failed_members, "skipped_members": skipped_members,
               "expected_members": result.get("expected_members", []), "is_upload_ready": False}
    stats = {"account": slug, "guild_id": guild_id, "ok_count": ok_count, "total_count": total_count,
             "skipped_count": skipped_count, "failed_count": max(len(failed_members), total_count - ok_count - skipped_count)}
    skip_reason = str(result.get("skip_reason", "") or "").strip()
    if skip_reason:
        return JobResult(ok=False, message=skip_reason, outputs={"guild_id": guild_id, "files": files, "skip_reason": skip_reason})

    if not guild_id:
        return JobResult(
            ok=False,
            message="找不到聯盟資訊",
            stats={"account": slug, "ok_count": ok_count, "total_count": total_count},
            outputs={"char_details_dir": str(CHAR_DETAILS_DIR), "files": files},
        )

    if total_count <= 0:
        return JobResult(
            ok=False,
            message="聯盟成員清單為空",
            stats={"account": slug, "guild_id": guild_id, "ok_count": ok_count, "total_count": total_count},
            outputs={"char_details_dir": str(CHAR_DETAILS_DIR), "guild_id": guild_id, "files": files},
        )

    if len(files) != ok_count or len(outputs["expected_members"]) != ok_count:
        return JobResult(ok=False, message="下載資料清單不一致，停止上傳", stats=stats, outputs=outputs)

    outputs["is_upload_ready"] = ok_count > 0
    if stats["failed_count"]:
        names = "、".join(str(item.get("nickname", "")) for item in failed_members)
        return JobResult(
            ok=False, message=f"批次下載不完整 ({ok_count}/{total_count}，失敗 {stats['failed_count']}，正常略過 {skipped_count})" + (f"，失敗成員：{names}" if names else ""),
            stats=stats, outputs=outputs,
        )

    if ok_count == 0 and skipped_count == total_count:
        message = f"全部 {skipped_count} 名成員均未持有本次檢查角色，無需上傳"
        return JobResult(ok=False, message=message, stats=stats, outputs={**outputs, "skip_reason": message})

    if ok_count <= 0:
        return JobResult(
            ok=False,
            message="批次下載妮姬資料失敗",
            stats={"account": slug, "guild_id": guild_id, "ok_count": ok_count, "total_count": total_count},
            outputs={"char_details_dir": str(CHAR_DETAILS_DIR), "guild_id": guild_id, "files": files},
        )

    return JobResult(
        ok=True,
        message=f"批次下載妮姬資料成功 ({ok_count}/{total_count})",
        stats=stats,
        outputs=outputs,
    )
