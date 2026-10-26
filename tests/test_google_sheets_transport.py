from types import SimpleNamespace

from google.auth.credentials import AnonymousCredentials
import pytest
import requests

from lib.google_sheets.transport import RequestLimiter, SheetsSession, get_request_limiter
from lib.google_sheets import auth


def test_gspread_clients_use_the_shared_service_account_transport(tmp_path, monkeypatch):
    credentials = AnonymousCredentials()
    credentials.project_id = "audit-project"
    credentials.service_account_email = "audit@example.com"
    monkeypatch.setattr(auth.Credentials, "from_service_account_info", lambda *args, **kwargs: credentials)
    path = tmp_path / "credentials.json"
    path.write_text("{}", encoding="utf-8")
    first, second = auth.get_gspread_client(path), auth.get_gspread_client(path)
    first_session = getattr(first, "http_client", first).session
    second_session = getattr(second, "http_client", second).session
    assert isinstance(first_session, SheetsSession)
    assert first_session._limiter is second_session._limiter


def test_limiter_shares_account_budget_and_separates_reads_from_writes():
    now, waits = [0.0], []

    def advance(seconds):
        waits.append(seconds)
        now[0] += seconds

    limiter = RequestLimiter(limit=2, clock=lambda: now[0], sleep=advance)
    limiter.acquire(True)
    limiter.acquire(True)
    limiter.acquire(False)
    assert waits == []
    limiter.acquire(True)
    assert waits == [60.0]
    assert get_request_limiter("p", "email") is get_request_limiter("p", "email")
    assert get_request_limiter("p", "other") is not get_request_limiter("p", "email")


@pytest.fixture
def session(monkeypatch):
    waits, calls, budgets = [], [], []
    client = SheetsSession(AnonymousCredentials(), SimpleNamespace(acquire=budgets.append), sleep=waits.append, jitter=lambda: 0)
    responses = []

    def send(*args, **kwargs):
        calls.append(args)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(client, "_request_once", send)
    return client, responses, waits, calls, budgets


def _response(status, retry_after=None):
    response = requests.Response()
    response.status_code = status
    response._content = b"{}"
    response._content_consumed = True
    if retry_after:
        response.headers["Retry-After"] = retry_after
    return response


@pytest.mark.parametrize("method", ["get", "post"])
def test_429_waits_for_retry_after_and_retries_same_operation(session, method):
    client, responses, waits, calls, budgets = session
    responses.extend([_response(429, "5"), _response(200)])
    assert client.request(method, "https://sheets.googleapis.com/v4/spreadsheets/id").status_code == 200
    assert waits == [5.0]
    assert len(calls) == 2
    assert budgets == [method == "get"] * 2


def test_retries_are_bounded_and_cover_quota_refill_window(session):
    client, responses, waits, calls, budgets = session
    responses.extend([_response(429) for _ in range(7)])
    assert client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/id").status_code == 429
    assert len(calls) == 7
    assert waits == [1, 2, 4, 8, 16, 32]


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError(), _response(503)])
def test_reads_retry_temporary_failures(session, failure):
    client, responses, waits, calls, budgets = session
    responses.extend([failure, _response(200)])
    assert client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/id").status_code == 200
    assert len(calls) == 2


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError()])
def test_uncertain_write_is_not_blindly_retried(session, failure):
    client, responses, waits, calls, budgets = session
    responses.append(failure)
    with pytest.raises(type(failure)):
        client.request("post", "https://sheets.googleapis.com/v4/spreadsheets/id:batchUpdate")
    assert len(calls) == 1
    assert waits == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 503])
def test_non_quota_write_errors_are_returned_without_retry(session, status):
    client, responses, waits, calls, budgets = session
    responses.append(_response(status))
    assert client.request("post", "https://sheets.googleapis.com/v4/spreadsheets/id:batchUpdate").status_code == status
    assert len(calls) == 1
    assert waits == []
