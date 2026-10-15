"""Bounded binary control-frame values for the offline D1 object adapter."""

import base64
import binascii
import hashlib
from dataclasses import dataclass

from domain.chunks import CHUNK_BYTES


@dataclass(frozen=True)
class D1Binary:
    data: bytes

    def __post_init__(self):
        if type(self.data) is not bytes or not 1 <= len(self.data) <= CHUNK_BYTES:
            raise ValueError('D1 binary chunk must contain 1..32768 bytes')

    @property
    def wire(self):
        return {'type': 'd1_binary_v1', 'base64': base64.b64encode(self.data).decode('ascii'),
                'sha256': hashlib.sha256(self.data).hexdigest()}

    @classmethod
    def from_wire(cls, value):
        if type(value) is not dict or set(value) != {'type', 'base64', 'sha256'}:
            raise ValueError('invalid D1 binary wire value')
        if value['type'] != 'd1_binary_v1' or type(value['base64']) is not str or len(value['base64']) > 43692:
            raise ValueError('invalid D1 binary wire encoding')
        try:
            data = base64.b64decode(value['base64'], validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError('invalid D1 binary base64') from error
        result = cls(data)
        if result.wire != value:
            raise ValueError('D1 binary integrity or canonical encoding mismatch')
        return result
