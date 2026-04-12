# -*- coding: utf-8 -*-
import asyncio
import datetime
from typing import Any, Dict, List

from playwright.async_api import async_playwright

from .api import check_login as api_check_login, fetch_role_info as api_fetch_role_info
from .constants import LOGIN_URL, REGION_TARGET
from .log import log_msg, log_exc
from .utils import load_session, save_session


async def save_state_snapshot(ctx, username: str, label: str) -> None:
    """儲存 storage_state（僅記錄數量與網域），集中共用。"""
    try:
        state = await ctx.storage_state()
        save_session(username, state)
        domains = {c.get("domain") for c in state.get("cookies", [])}
        log_msg(f"saved storage_state cookies={len(state.get('cookies', []))} domains={list(domains)}")
    except Exception as exc:
        log_exc(exc)


async def verify_via_api(ctx):
    """共用：呼叫 API 檢查登入並嘗試取得角色資訊。"""
    is_api_ok = False
    role_name = None
    role_id = None
    try:
        is_api_ok = await api_check_login(ctx)
        if is_api_ok:
            role_name, role_id = await api_fetch_role_info(ctx)
        log_msg(f"ok={is_api_ok} name={role_name} id={role_id}")
    except Exception as exc:
        log_exc(exc)
    return is_api_ok, role_name, role_id


async def click_cookie_once(page, steps: list[str]) -> bool:
    """嘗試關閉 OneTrust cookie 橫幅。"""
    btn = page.locator("button.onetrust-close-btn-handler.onetrust-close-btn-ui.banner-close-button.ot-close-icon")
    try:
        cnt = await btn.count()
        log_msg(f"locator count={cnt}")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
    try:
        await btn.wait_for(state="visible", timeout=5000)
        await btn.click()
        await btn.wait_for(state="hidden", timeout=5000)
        steps.append("cookieClosed")
        log_msg("cookie hidden (locator)")
        return True
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


async def choose_region_once(page, steps: list[str]) -> bool:
    """點擊地區選單中的目標地區，確認消失才算成功。"""
    try:
        li = page.locator(
            "li.flex.items-center.justify-between.mb-\\[14px\\].last-of-type\\:mb-0.cursor-pointer",
            has_text=REGION_TARGET,
        )
        count = await li.count()
        log_msg(f"region li count (target={REGION_TARGET})={count}")
        if count == 0:
            return False
        await li.first.click(timeout=2000, force=True)
        await page.wait_for_timeout(300)
        still_visible = await li.first.is_visible(timeout=3000)
        if not still_visible:
            steps.append(f"region:{REGION_TARGET}")
            log_msg("region selected and dismissed (locator)")
            return True
        log_msg("region clicked but still visible")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
    return False


async def _first_visible(page, sel: str):
    loc = page.locator(sel)
    cnt = await loc.count()
    for i in range(cnt):
        item = loc.nth(i)
        try:
            if await item.is_visible() and await item.is_enabled():
                return item
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            continue
    return None


async def fill_creds_once(page, username: str, password: str, steps: list[str]) -> bool:
    """填入帳號與密碼欄位。"""
    try:
        user_input = await _first_visible(page, "#loginPwdForm_account")
        pass_input = await _first_visible(page, "#loginPwdForm_password")
        log_msg(f"user_found={user_input is not None}, pass_found={pass_input is not None}")
        if not user_input or not pass_input:
            return False
        await user_input.fill(username, timeout=5000)
        await pass_input.fill(password, timeout=5000)
        steps.append("filledUserPass")
        log_msg("filled user/pass (locator)")
        return True
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


async def click_login_once(page, steps: list[str]) -> bool:
    """按下登入按鈕。"""
    try:
        btn = page.locator("button[type='submit'], button.login-btn, .ant-btn[type='button'], button[name='confirm']").first
        await btn.click(timeout=2000)
        steps.append("loginClicked")
        log_msg("login clicked (locator)")
        return True
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


async def cookie_worker(page, steps: List[str], state: Dict[str, Any], start_ts: datetime.datetime) -> None:
    """迴圈偵測並關閉 cookie。"""
    attempt = 0
    while not state["is_abort"] and not state["is_cookie_ok"] and (datetime.datetime.now() - start_ts).total_seconds() < 120:
        try:
            cookie_count = await page.locator("button.onetrust-close-btn-handler.onetrust-close-btn-ui.banner-close-button.ot-close-icon").count()
            cookie_present = cookie_count > 0
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            cookie_count = -1
            cookie_present = False
        log_msg(f"attempt={attempt}, present={cookie_present} (count={cookie_count})")
        if not state["is_cookie_ok"]:
            state["is_cookie_ok"] = await click_cookie_once(page, steps)
        await page.wait_for_timeout(500)
        attempt += 1


async def region_worker(page, steps: List[str], state: Dict[str, Any], start_ts: datetime.datetime) -> None:
    """迴圈偵測並點擊地區。"""
    attempt = 0
    while not state["is_abort"] and not state["is_region_ok"] and (datetime.datetime.now() - start_ts).total_seconds() < 120:
        try:
            region_count = await page.locator(
                "li.flex.items-center.justify-between.mb-\\[14px\\].last-of-type\\:mb-0.cursor-pointer",
                has_text=REGION_TARGET,
            ).count()
            region_present = region_count > 0
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            region_count = -1
            region_present = False
        log_msg(f"attempt={attempt}, present={region_present} (count={region_count})")
        if not state["is_region_ok"] and region_present:
            state["is_region_ok"] = await choose_region_once(page, steps)
        await page.wait_for_timeout(500)
        attempt += 1


async def creds_worker(page, username: str, password: str, steps: List[str], state: Dict[str, Any], start_ts: datetime.datetime) -> None:
    """等地區完成後再填帳密。"""
    attempt = 0
    while not state["is_abort"] and not state["is_creds_ok"] and (datetime.datetime.now() - start_ts).total_seconds() < 120:
        if not state["is_region_ok"]:
            log_msg(f"attempt={attempt}, region_ok={state['is_region_ok']} -> wait")
            await page.wait_for_timeout(500)
            attempt += 1
            continue
        try:
            user_count = await page.locator("input[name='username'], input[type='email'], input[type='text']").count()
            pass_count = await page.locator("input[name='password'], input[type='password']").count()
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            user_count = pass_count = -1
        log_msg(f"attempt={attempt}, user_count={user_count}, pass_count={pass_count}")
        if not state["is_creds_ok"]:
            state["is_creds_ok"] = await fill_creds_once(page, username, password, steps)
        await page.wait_for_timeout(500)
        attempt += 1


async def login_worker(page, steps: List[str], state: Dict[str, Any], start_ts: datetime.datetime) -> None:
    """全部完成後嘗試登入。"""
    attempt = 0
    while not state["is_abort"] and not state["is_login_ok"] and (datetime.datetime.now() - start_ts).total_seconds() < 120:
        log_msg(
            f"attempt={attempt}, cookie_ok={state['is_cookie_ok']}, region_ok={state['is_region_ok']}, creds_ok={state['is_creds_ok']}",
        )
        if state["is_cookie_ok"] and state["is_region_ok"] and state["is_creds_ok"] and not state["is_login_ok"]:
            state["is_login_ok"] = await click_login_once(page, steps)
        await page.wait_for_timeout(500)
        attempt += 1


async def uid_worker(context, browser, page, username: str, steps: List[str], state: Dict[str, Any], start_ts: datetime.datetime) -> None:
    """登入完成後：先 CheckLogin，成功後取得角色資訊，最後儲存 state 關閉瀏覽器。"""
    attempt = 0
    is_api_ok = False
    while not state["is_abort"] and (datetime.datetime.now() - start_ts).total_seconds() < 180:
        if state["is_login_ok"]:
            log_msg(f"attempt={attempt}, login_ok={state['is_login_ok']}, api_ok={is_api_ok}")

            if not is_api_ok:
                is_api_ok, rn2, rid2 = await verify_via_api(context)
                if is_api_ok:
                    steps.append("apiOk")
                    if rn2:
                        steps.append(f"roleName:{rn2}")
                        state["role_name_value"] = rn2
                    if rid2:
                        steps.append(f"roleId:{rid2}")
                        state["uid_value"] = rid2
                    try:
                        await save_state_snapshot(context, username, "headful")
                    except Exception as exc:  # noqa: BLE001
                        log_exc(exc)
                    state["is_abort"] = True
                    try:
                        await browser.close()
                    except Exception as exc:  # noqa: BLE001
                        log_exc(exc)
                    break

            await page.wait_for_timeout(500)
            attempt += 1
            continue
        await page.wait_for_timeout(500)
        attempt += 1


async def run_playwright(username: str, password: str):
    # 不自動關閉瀏覽器，保持開啟；若已有 session 先用 headless 嘗試直接取 UID
    p = await async_playwright().start()
    steps: list[str] = []
    slug = username
    state: Dict[str, Any] = {
        "is_abort": False,
        "is_cookie_ok": False,
        "is_region_ok": False,
        "is_creds_ok": False,
        "is_login_ok": False,
        "uid_value": None,
        "role_name_value": None,
    }
    log_msg(f"using state account key: {slug}")

    # 先用 headless + storage_state 嘗試直接拿 UID，成功就直接返回，不再開瀏覽器
    existing_state = load_session(slug)
    if existing_state:
        log_msg(f"found stored state for {slug}, try headless reuse")
        try:
            hb = await p.chromium.launch(headless=True)
            hctx = await hb.new_context(storage_state=existing_state)
            hpage = await hctx.new_page()
            is_api_ok, rn2, rid2 = await verify_via_api(hctx)
            if is_api_ok:
                if rn2:
                    steps.append(f"roleName:{rn2}")
                    state["role_name_value"] = rn2
                if rid2:
                    steps.append(f"roleId:{rid2}")
                    state["uid_value"] = rid2
                steps.append("apiOk")
                log_msg("CheckLogin success (headless)")
                try:
                    await save_state_snapshot(hctx, username, "headless")
                except Exception as exc:  # noqa: BLE001
                    log_exc(exc)
                await hb.close()
                return hpage.url, steps, state["uid_value"], state["role_name_value"]
            else:
                log_msg("headless CheckLogin failed, fallback to headful flow")
            await hb.close()
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
    else:
        log_msg("storage_state not found, skip headless reuse")

# headful 流程：正常登入
    log_msg("starting headful login flow")
    browser = await p.chromium.launch(headless=False)
    context = await browser.new_context(viewport={"width": 900, "height": 780})
    page = await context.new_page()

    await page.goto(LOGIN_URL, wait_until="load")
    log_msg("opened login page (headful)")

    start_ts = datetime.datetime.now()

    try:
        await asyncio.gather(
            cookie_worker(page, steps, state, start_ts),
            region_worker(page, steps, state, start_ts),
            creds_worker(page, username, password, steps, state, start_ts),
            login_worker(page, steps, state, start_ts),
            uid_worker(context, browser, page, username, steps, state, start_ts),
        )
    except Exception as exc:
        log_exc(exc)

    log_msg(
        "final states: cookie={cookie}, region={region}, creds={creds}, login={login}".format(
            cookie=state["is_cookie_ok"],
            region=state["is_region_ok"],
            creds=state["is_creds_ok"],
            login=state["is_login_ok"],
        ),
    )

    # 保持瀏覽器開啟 30 分鐘供手動處理（除非已 abort/成功）
    if not state["is_abort"]:
        log_msg("keeping browser open (30 minutes) for manual puzzle/login")
        await page.wait_for_timeout(30 * 60 * 1000)
    return page.url, steps, state["uid_value"], state["role_name_value"]
