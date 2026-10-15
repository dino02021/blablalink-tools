"""Synthetic local messages only. Contains no Discord URL or network client."""

import json
import sqlite3
import uuid

from application.delivery import NotSent
from domain.snapshots import canonical_json


class MockDiscord:
    def __init__(self, path):
        self.connection = sqlite3.connect(str(path))
        self.connection.execute("CREATE TABLE IF NOT EXISTS messages(message_id TEXT PRIMARY KEY, target TEXT NOT NULL, payload TEXT NOT NULL)")
        self.connection.commit()
        self.fault = None

    def close(self):
        self.connection.close()

    def send(self, target: str, payload: dict) -> str:
        fault, self.fault = self.fault, None
        if fault == "before":
            raise NotSent(5)
        if fault not in (None, "after"):
            raise ValueError("invalid mock failure")
        message_id = uuid.uuid4().hex
        with self.connection:
            self.connection.execute("INSERT INTO messages VALUES(?,?,?)", (message_id, target, canonical_json(payload)))
        if fault == "after":
            raise TimeoutError("mock accepted message but response was lost")
        return message_id

    def messages(self) -> tuple[dict, ...]:
        return tuple({"message_id": row[0], "target": row[1], "payload": json.loads(row[2])}
                     for row in self.connection.execute("SELECT message_id,target,payload FROM messages ORDER BY rowid"))
