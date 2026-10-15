"""Scripted source pages; explicitly no provider URL, session or network support."""

import copy


class MockCollection:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch_page(self, member, source, cursor, requested_ids):
        self.calls.append((member.key, source, cursor, requested_ids))
        response = self.pages[(member.key, source, cursor)]
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)
