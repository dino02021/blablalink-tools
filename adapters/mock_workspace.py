"""Filesystem-only mock marker/fence gate; never opens a coordinator database."""

import json
from pathlib import Path

from domain.mock_workspace import MOCK_WORKSPACE_MARKER
from domain.snapshots import canonical_json


def require_mock_workspace(directory, is_writer=False):
    if type(is_writer) is not bool:
        raise ValueError('explicit mock workspace access mode required')
    directory = Path(directory).resolve()
    if is_writer and (directory/'restore-lock.json').exists():
        raise ValueError('restored workspace is fenced read-only')
    if not _is_mock_marker(directory/'mock-workspace.json') or not (directory/'state.sqlite').is_file():
        raise ValueError('not an initialized mock workspace')
    return directory


def _is_mock_marker(path):
    return path.is_file() and path.stat().st_size<=4096 and canonical_json(json.loads(path.read_text(encoding='utf-8')))==canonical_json(MOCK_WORKSPACE_MARKER)
