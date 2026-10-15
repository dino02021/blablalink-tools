"""Scripted source pages; explicitly no provider URL, session or network support."""

import copy


class MockCollection:
    def __init__(self, pages, is_stable_snapshot_revision=False):
        if type(is_stable_snapshot_revision) is not bool:
            raise ValueError("explicit mock source revision contract required")
        self.pages = pages
        self.calls = []
        self.is_stable_snapshot_revision = is_stable_snapshot_revision

    def fetch_page(self, member, source, cursor, requested_ids):
        self.calls.append((member.key, source, cursor, requested_ids))
        response = self.pages[(member.key, source, cursor)]
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)
