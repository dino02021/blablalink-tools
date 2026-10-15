"""Content-derived, immutable local reference dictionaries using existing snapshot storage."""

import hashlib

from adapters.legacy_standard import LegacyCatalog
from adapters.sheet_standard import StandardReference
from domain.snapshots import canonical_json, capture_document


def capture_reference(scope_id, captured_at, role_ids, function_prefixes, role_aliases=(), function_aliases=(), dual_elements=()):
    if not isinstance(function_prefixes, dict) or len(role_ids) > 1000 or len(function_prefixes) > 1000:
        raise ValueError("bounded explicit reference required")
    if any(not isinstance(prefix, str) or len(prefix) != 5 or not prefix.isascii() or not prefix.isdigit() for prefix in function_prefixes):
        raise ValueError("five-digit reference prefixes required")
    reference = StandardReference("pending", role_ids, tuple(function_prefixes.values()))
    catalog = LegacyCatalog(reference, role_aliases, function_aliases, dual_elements)
    if len(catalog.role_aliases) + len(catalog.function_aliases) > 10000:
        raise ValueError("reference alias count exceeds bound")
    payload = {"reference_kind": "healthcheck_reference", "reference_schema_version": 1,
        "role_ids": reference.role_ids, "function_prefixes": function_prefixes,
        "role_aliases": catalog.role_aliases, "function_aliases": catalog.function_aliases, "dual_elements": catalog.dual_elements}
    serialized = canonical_json(payload)
    if len(serialized.encode()) > 2 * 1024 * 1024:
        raise ValueError("reference content exceeds bound")
    version = hashlib.sha256(serialized.encode()).hexdigest()
    return capture_document("union_inputs", scope_id, captured_at, version, payload)


def read_reference(document):
    data = document.payload
    if (document.envelope["kind"] != "union_inputs" or set(data) != {"reference_kind", "reference_schema_version", "role_ids", "function_prefixes", "role_aliases", "function_aliases", "dual_elements"}
            or data["reference_kind"] != "healthcheck_reference" or type(data["reference_schema_version"]) is not int or data["reference_schema_version"] != 1):
        raise ValueError("unsupported reference snapshot")
    restored = capture_reference(document.envelope["scope_id"], document.envelope["captured_at"],
        data["role_ids"], data["function_prefixes"], data["role_aliases"], data["function_aliases"], data["dual_elements"])
    if restored != document:
        raise ValueError("reference content-derived version differs")
    reference = StandardReference(document.envelope["reference_version"], tuple(data["role_ids"]), tuple(data["function_prefixes"].values()))
    return LegacyCatalog(reference, data["role_aliases"], data["function_aliases"], data["dual_elements"]), dict(data["function_prefixes"])
