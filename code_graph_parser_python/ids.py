from __future__ import annotations

import hashlib


def package_id(name: str) -> str:
    return "pkg:" + name.strip()


def unit_id(name: str) -> str:
    return "unit:" + name.strip()


def function_id(name: str) -> str:
    return "fn:" + name.strip()


def placeholder_function_id(name: str) -> str:
    return "placeholder:" + function_id(name)


def relationship_id(from_id: str, relationship_type: str, to_id: str) -> str:
    raw = "%s|%s|%s" % (from_id.strip(), relationship_type, to_id.strip())
    return "rel:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def endpoint_id(direction: str, endpoint_type: str, identity: str) -> str:
    raw = "%s:%s:%s" % (direction.strip(), endpoint_type.strip(), identity.strip())
    return "endpoint:%s:%s:%s" % (
        direction.strip(),
        endpoint_type.strip(),
        hashlib.sha1(raw.encode("utf-8")).hexdigest(),
    )
