"""Small, schema-pinned D1 modules with atomic bootstrap guards."""

from dataclasses import dataclass
import hashlib
import re

from coordination.d1_statement import D1Statement
from domain.snapshots import canonical_json


@dataclass(frozen=True)
class D1Schema:
    statements: tuple[str, ...]

    def __post_init__(self):
        if type(self.statements) not in (tuple, list) or not 1 <= len(self.statements) <= 32:
            raise ValueError('bounded D1 schema module required')
        object.__setattr__(self, 'statements', tuple(self.statements))
        names = [entry[1] for entry in self.objects]
        if len(names) != len(set(names)):
            raise ValueError('duplicate D1 schema object')
        # Validate the actual control frame now, before creating any workspace.
        self.guard()
        if len(canonical_json(self.bootstrap).encode()) > 65536:
            raise ValueError('D1 schema bootstrap frame exceeds bound')

    @property
    def schema_id(self):
        return hashlib.sha256(canonical_json(self.statements).encode()).hexdigest()

    @property
    def objects(self):
        entries = []
        for statement in self.statements:
            if type(statement) is not str:
                raise ValueError('trusted static D1 schema SQL required')
            sql = statement.replace(' IF NOT EXISTS', '', 1)
            match = re.match(r'CREATE (TABLE|INDEX|TRIGGER) ([a-z][a-z0-9_]*)\b', sql)
            if match is None:
                raise ValueError('unsupported D1 schema declaration')
            kind, name = match.groups()
            owner = name if kind == 'TABLE' else None
            if owner is None:
                on = re.search(r'\bON ([a-z][a-z0-9_]*)\b', sql)
                if on is None:
                    raise ValueError('D1 index/trigger owner required')
                owner = on.group(1)
            entries.append((kind.lower(), name, sql, owner))
        return tuple(entries)

    @property
    def bootstrap(self):
        declarations = []
        for kind, _, sql, _ in self.objects:
            declarations.append(sql.replace('CREATE ' + kind.upper() + ' ',
                'CREATE ' + kind.upper() + ' IF NOT EXISTS ', 1))
        return [self.guard(is_allow_empty=True).wire, *declarations, self.guard().wire]

    def guard(self, *, is_allow_empty=False):
        if type(is_allow_empty) is not bool:
            raise ValueError('explicit D1 bootstrap mode required')
        entries = self.objects
        # Identifiers come exclusively from validated static declarations.
        owners = ','.join("'" + owner + "'" for owner in sorted({entry[3] for entry in entries}))
        count = '(SELECT COUNT(*) FROM sqlite_master WHERE tbl_name IN(' + owners + '))'
        predicates = ' AND '.join('EXISTS(SELECT 1 FROM sqlite_master WHERE type=? AND name=? AND sql=?)' for _ in entries)
        condition = count + '=' + str(len(entries)) + ' AND ' + predicates
        if is_allow_empty:
            condition = count + '=0 OR (' + condition + ')'
        return D1Statement("SELECT CASE WHEN " + condition + " THEN 1 ELSE json('coordinator_schema_mismatch') END AS schema_ok",
                           tuple(value for kind, name, sql, _ in entries for value in (kind, name, sql)))
