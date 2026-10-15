"""Explicit membership incarnation evidence, separate from names and row positions."""

import json
import re

from domain.snapshots import MemberKey


def validate_member_epochs(value, members) -> dict[str, str]:
    if value is None:
        return {}
    allowed = {member.key for member in members}
    if not isinstance(value, dict) or len(value) > 300:
        raise ValueError("bounded explicit member epochs required")
    result = {}
    for key, epoch in value.items():
        try:
            decoded = json.loads(key)
            member = MemberKey(*decoded) if isinstance(decoded, list) and len(decoded) == 2 else None
        except (ValueError, TypeError):
            raise ValueError("invalid epoch member identity") from None
        if (member is None or member.key != key or key not in allowed or not isinstance(epoch, str)
                or re.fullmatch("[a-f0-9]{64}", epoch) is None):
            raise ValueError("member epoch is outside coverage or malformed")
        result[key] = epoch
    return result
