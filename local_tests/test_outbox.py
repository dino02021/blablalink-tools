from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from adapters.local_outbox import LocalOutbox
from adapters.mock_discord import MockDiscord
from application.delivery import DeliveryClaim, DeliverySpec, send_next
from application.tasks import StateConflict


@pytest.fixture
def delivery(tmp_path):
    clock = Clock()
    outbox = LocalOutbox(tmp_path / "state.sqlite", clock)
    provider = MockDiscord(tmp_path / "messages.sqlite")
    spec = DeliverySpec("intent_demo", "report_demo", "area:member_demo", "route_v1", "thread_demo", ("<@123> report", "more @everyone"), ("123",))
    outbox.enqueue("request_demo", spec, spec.outbox_id)
    yield outbox, provider, spec, clock
    outbox.close()
    provider.close()


def test_request_and_intent_dedup_keep_confirmed_content(delivery):
    outbox, provider, spec, _ = delivery
    assert outbox.enqueue("request_demo", spec, spec.outbox_id) == spec.outbox_id
    assert outbox.enqueue("request_alias", spec, spec.outbox_id) == spec.outbox_id
    changed = replace(spec, parts=("different content",))
    with pytest.raises(StateConflict):
        outbox.enqueue("request_alias", changed, changed.outbox_id)
    with pytest.raises(StateConflict):
        outbox.enqueue("unconfirmed", changed, spec.outbox_id)
    assert provider.messages() == ()


def test_ordered_parts_and_mentions_only_first_part(delivery):
    outbox, provider, spec, _ = delivery
    assert send_next(outbox, provider, spec.outbox_id, "worker")
    assert send_next(outbox, provider, spec.outbox_id, "worker")
    assert not send_next(outbox, provider, spec.outbox_id, "worker")
    messages = provider.messages()
    assert len(messages) == 2
    assert messages[0]["payload"]["allowed_mentions"]["users"] == ["123"]
    assert messages[1]["payload"]["allowed_mentions"]["users"] == []
    assert all(m["payload"]["allowed_mentions"]["parse"] == [] for m in messages)
    assert all(p["state"] == "sent" and p["message_id"] for p in outbox.parts(spec.outbox_id))


def test_accepted_but_response_lost_blocks_all_retries_until_verified(delivery):
    outbox, provider, spec, clock = delivery
    provider.fault = "after"
    assert not send_next(outbox, provider, spec.outbox_id, "worker")
    assert outbox.parts(spec.outbox_id)[0]["state"] == "delivery_uncertain"
    clock.now += 1000000
    assert not send_next(outbox, provider, spec.outbox_id, "other_worker")
    assert len(provider.messages()) == 1
    claim = DeliveryClaim(spec.outbox_id, 0, "worker", 1)
    message = provider.messages()[0]
    with pytest.raises(StateConflict):
        outbox.reconcile(claim, False, message["message_id"], message["payload"])
    with pytest.raises(StateConflict):
        outbox.reconcile(claim, True, None)
    with pytest.raises(StateConflict):
        outbox.reconcile(claim, True, message["message_id"], message["payload"], observed_target="wrong_thread")
    outbox.reconcile(claim, True, message["message_id"], message["payload"], observed_target=message["target"])
    assert send_next(outbox, provider, spec.outbox_id, "new_worker")
    assert len(provider.messages()) == 2


def test_known_not_sent_rate_limit_respects_delay_and_attempt_bound(delivery):
    outbox, provider, spec, clock = delivery
    for attempt in range(3):
        provider.fault = "before"
        assert not send_next(outbox, provider, spec.outbox_id, "worker")
        assert not send_next(outbox, provider, spec.outbox_id, "worker")
        clock.now += 5
    assert outbox.parts(spec.outbox_id)[0]["state"] == "failed"
    assert provider.messages() == ()


def test_interrupted_sender_is_quarantined_across_restart(delivery, tmp_path):
    outbox, provider, spec, clock = delivery
    claim = outbox.claim(spec.outbox_id, "worker")
    assert outbox.claim(spec.outbox_id, "other") is None
    outbox.close()
    restarted = LocalOutbox(tmp_path / "state.sqlite", clock)
    try:
        assert restarted.claim(spec.outbox_id, "other") is None
        restarted.recover_interrupted(spec.outbox_id)
        with pytest.raises(StateConflict):
            restarted.sent(claim, "late_receipt")
        restarted.reconcile(claim, True, None, is_absence_verified=True)
        assert send_next(restarted, provider, spec.outbox_id, "other")
        assert len(provider.messages()) == 1
    finally:
        restarted.close()


@pytest.mark.parametrize("parts", [(), ("",), ("x" * 2001,), ("😀" * 1001,)])
def test_message_size_uses_utf16_limit(parts):
    with pytest.raises(ValueError):
        DeliverySpec("intent", "report", "member", "route", "thread", parts)


def test_explicit_new_intent_can_send_same_report_again(delivery):
    outbox, provider, spec, _ = delivery
    changed = replace(spec, intent_id="second_explicit_send")
    assert outbox.enqueue("new_request", changed, changed.outbox_id) != spec.outbox_id
    assert send_next(outbox, provider, changed.outbox_id, "worker")


def test_two_connections_cannot_send_same_part(delivery, tmp_path):
    _, _, spec, _ = delivery
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(_claim_in_connection, (tmp_path / "state.sqlite",) * 2,
                                 (spec.outbox_id,) * 2, (barrier,) * 2, ("one", "two")))
    assert sum(claim is not None for claim in results) == 1


def test_corrupted_spec_cannot_be_sent(delivery):
    outbox, provider, spec, _ = delivery
    outbox.connection.execute("UPDATE report_outbox SET content_hash='corrupted'")
    with pytest.raises(StateConflict):
        send_next(outbox, provider, spec.outbox_id, "worker")
    assert provider.messages() == ()


def _claim_in_connection(path, outbox_id, barrier, worker):
    outbox = LocalOutbox(path, Clock())
    try:
        barrier.wait()
        return outbox.claim(outbox_id, worker)
    finally:
        outbox.close()


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now
