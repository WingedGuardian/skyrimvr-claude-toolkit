"""Optional Nexus discovery contracts; credentials and HTTP belong to consumers.

Schema: https://graphql.nexusmods.com/ (ModsFilter, ModPage, Mod).
Search results are candidate identities, never proof of installation or identity.
No request is made on import and no credentials are accepted or logged here.
"""
from __future__ import annotations

import re


def request(domain, name, count=15, wildcard=False):
    if not isinstance(domain, str) or not re.fullmatch(r"[a-z0-9]+", domain):
        raise ValueError("a Nexus game domain is required")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a nonempty search name is required")
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError("search count must be from 1 to 100")
    field = "name" if wildcard else "nameStemmed"
    term = {"value": name}
    if wildcard:
        term["op"] = "WILDCARD"
    return {"query": "query($f: ModsFilter, $count: Int){ mods(filter:$f, count:$count){ "
            "totalCount nodes{ modId name version author updatedAt status } } }",
            "variables": {"f": {"gameDomainName": [{"value": domain, "op": "EQUALS"}],
                                 field: [term]}, "count": count}}


def parse(payload, domain, name, count=15):
    """Complete/zero/partial/unavailable over this filtered search, not all mods.

    A capped page is partial. GraphQL errors, malformed rows, absent totals and
    missing version/date/status stay visible even when usable candidates remain.
    Never reproduce raw server error messages (they may contain request secrets).
    """
    request(domain, name, count)  # validate identifiers even for a stubbed response
    result = {"v": 1, "provider": "nexus", "game": domain, "query": name,
              "state": "unavailable", "candidates": [], "total": None,
              "returned": 0, "malformed": 0, "limit": count, "reasons": [],
              "installed_evidence": "not_checked"}
    if not isinstance(payload, dict):
        result["reasons"].append("response is not an object")
        return result
    errors = payload.get("errors")
    if errors:
        result["reasons"].append("GraphQL reported errors")
    data = payload.get("data")
    page = data.get("mods") if isinstance(data, dict) else None
    if not isinstance(page, dict) or not isinstance(page.get("nodes"), list):
        result["reasons"].append("no usable search page")
        return result
    total = page.get("totalCount")
    if type(total) is int and total >= 0:
        result["total"] = total
    else:
        result["reasons"].append("search total is unavailable")
    seen = set()
    for row in page["nodes"]:
        try:
            raw_id = row["modId"]
            if isinstance(raw_id, bool) or not re.fullmatch(r"[0-9]+", str(raw_id)):
                raise ValueError("invalid mod id")
            identity = int(raw_id)
            if identity < 1 or identity in seen or not isinstance(row["name"], str) or not row["name"].strip():
                raise ValueError("missing or duplicate candidate identity")
            seen.add(identity)
            candidate = {"mod_id": identity, "name": row["name"], "game": domain,
                         "url": "https://www.nexusmods.com/{}/mods/{}".format(domain, identity),
                         "identity_evidence": "search_candidate", "installed_evidence": "not_checked"}
            missing = []
            for source, target in (("version", "version"), ("updatedAt", "updated_at"),
                                   ("status", "status"), ("author", "author")):
                value = row.get(source)
                candidate[target] = value if isinstance(value, str) and value.strip() else None
                if candidate[target] is None:
                    missing.append(target)
            candidate["unavailable_fields"] = missing
            result["candidates"].append(candidate)
        except (KeyError, TypeError, ValueError):
            result["malformed"] += 1
    result["returned"] = len(result["candidates"])
    if result["malformed"]:
        result["reasons"].append("malformed or duplicate candidates were excluded")
    if any(row["unavailable_fields"] for row in result["candidates"]):
        result["reasons"].append("some candidate metadata is unavailable")
    if result["total"] is not None and result["total"] != result["returned"]:
        result["reasons"].append("returned candidates do not cover the search total")
    if result["reasons"]:
        result["state"] = "partial" if result["candidates"] else "unavailable"
    else:
        result["state"] = "complete" if result["candidates"] else "zero"
    return result
