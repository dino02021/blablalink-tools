"""Portable prepared-statement wire contract shared by local D1 ports."""

from dataclasses import dataclass

from coordination.d1_binary import D1Binary
from domain.snapshots import canonical_json


@dataclass(frozen=True)
class D1Statement:
    sql: str
    parameters: tuple

    def __post_init__(self):
        if type(self.sql) is not str or not 0 < len(self.sql.encode()) <= 4096:
            raise ValueError('bounded D1 statement required')
        if type(self.parameters) not in (tuple, list) or len(self.parameters) > 100:
            raise ValueError('bounded D1 parameters required')
        object.__setattr__(self, 'parameters', tuple(self.parameters))
        for value in self.parameters:
            if value is not None and type(value) not in (str, int) and not isinstance(value, D1Binary):
                raise ValueError('invalid D1 binding')
            if type(value) is int and not -(2 ** 53 - 1) <= value <= 2 ** 53 - 1:
                raise ValueError('D1 wire integer exceeds exact JavaScript range')
        if len(canonical_json(self.wire).encode()) > 65536:
            raise ValueError('D1 control frame exceeds 64 KiB')

    @property
    def wire(self):
        return {'sql': self.sql, 'parameters': [value.wire if isinstance(value, D1Binary) else value for value in self.parameters]}
