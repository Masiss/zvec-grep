#!/usr/bin/env python3
"""Typed query/entity resolution for the experimental RAG grounding layer.

This module is deliberately small: it classifies query intent and resolves only
exact, typed identifiers against the existing RefIndex. It does not parse C# or
SQL, and it never reads gold labels, required groups or evaluation answer keys.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

KIND_TYPE = "TYPE"
KIND_PROPERTY = "PROPERTY"
KIND_METHOD = "METHOD"
KIND_SQL_BODY = "SQL_BODY"
KIND_VIEW = "VIEW"
KIND_DATA = "DATA"
KIND_UNKNOWN = "UNKNOWN"

ROLE_CONTROL = "CONTROL"
ROLE_WRAPPER = "WRAPPER"
ROLE_TYPE = "TYPE"
ROLE_PROPERTY = "PROPERTY"
ROLE_SQL = "SQL"
ROLE_VIEW = "VIEW"
ROLE_SUPPORTING = "SUPPORTING"
ROLE_UNKNOWN = "UNKNOWN"

ROLE_PRIORITY = {
    ROLE_TYPE: 50, ROLE_PROPERTY: 60, ROLE_SQL: 70, ROLE_VIEW: 70,
    ROLE_WRAPPER: 40, ROLE_CONTROL: 45, ROLE_SUPPORTING: 20, ROLE_UNKNOWN: 0,
}

PROPERTY_WORDS = re.compile(
    r"\b(property|properties|field|dbset|dbcontext|maps?|mapped|mapping|"
    r"entity|entities|type|kiểu|thuộc tính|ánh xạ)\b",
    re.I,
)
VIEW_WORDS = re.compile(r"\b(view|views|khung nhìn|khung nhin)\b", re.I)
OBJECT_WORDS = re.compile(r"\b(database object|db object|object kind|what kind|loại đối tượng|loai doi tuong)\b", re.I)
SQL_WORDS = re.compile(r"\b(sql|procedure|proc|stored|table|bảng|bang|database|db)\b", re.I)
METHOD_WORDS = re.compile(
    r"\b(method|function|call|invoke|write|writes|saved?|update|updates|"
    r"delete|create|read|flow|declaration|declared|executes?|gọi|goi|thực thi|thuc thi|cập nhật|cap nhat)\b",
    re.I,
)
NEGATIVE_WORDS = re.compile(
    r"\b(does|do|did|có|co)\b.{0,100}\b(invoke|call|gọi|goi|use|dùng|dung)\b"
    r"|\b(không|khong)\s*\?",
    re.I,
)
SQL_TOKEN = re.compile(r"\bsp_[A-Za-z0-9_]+", re.I)
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def identifiers(question: str) -> list[str]:
    """Identifiers that are plausibly code symbols, not ordinary words."""
    out = []
    for x in IDENT.findall(question):
        if len(x) < 4:
            continue
        if x[0].isupper() or "_" in x or any(c.isupper() for c in x[1:]):
            out.append(x)
    return out


def context_parent(refs, prop: dict) -> bool:
    """A DbSet property on a DbContext-like parent, without hard-coded names."""
    return bool(prop.get("is_dbset"))


@dataclass
class QuerySpec:
    question: str
    identifiers: list[str]
    intent: set[str]
    type_names: set[str] = field(default_factory=set)
    property_names: set[str] = field(default_factory=set)
    method_names: set[str] = field(default_factory=set)
    sql_names: set[str] = field(default_factory=set)
    view_names: set[str] = field(default_factory=set)
    unknown_sql_names: set[str] = field(default_factory=set)
    property_mapping: bool = False
    wants_sql: bool = False
    wants_view: bool = False
    wants_method: bool = False
    negative: bool = False
    ambiguous: bool = False

    def as_dict(self):
        return {
            "intent": sorted(self.intent),
            "identifiers": self.identifiers,
            "types": sorted(self.type_names),
            "properties": sorted(self.property_names),
            "methods": sorted(self.method_names),
            "sql": sorted(self.sql_names),
            "views": sorted(self.view_names),
            "unknown_sql": sorted(self.unknown_sql_names),
            "property_mapping": self.property_mapping,
            "wants_sql": self.wants_sql,
            "wants_view": self.wants_view,
            "wants_method": self.wants_method,
            "negative": self.negative,
            "ambiguous": self.ambiguous,
        }


def resolve_query(question: str, refs) -> QuerySpec:
    """Resolve exact typed symbols named by a query.

    Rules:
    - type/property/method names are exact, case-insensitive identifiers;
    - SQL procedure names are exact after the optional ``sp_``/case normalization;
    - views are exact ``CREATE VIEW`` object names;
    - a view name is never matched as a substring of a type or property name;
    - property mapping is resolved by ``DbSet<T>`` type, not by name similarity.
    """
    ids = identifiers(question)
    low = question.lower()
    intent = set()
    property_mapping = bool(PROPERTY_WORDS.search(low))
    object_kind = bool(OBJECT_WORDS.search(low))
    wants_view = (bool(VIEW_WORDS.search(low)) or object_kind) and not property_mapping
    wants_method = bool(METHOD_WORDS.search(low))
    negative = bool(NEGATIVE_WORDS.search(low)) and not re.search(
        r"only through|indirect|gián tiếp|gian tiep", low, re.I
    )
    exact_sql_ids = {norm(x) for x in SQL_TOKEN.findall(question)}
    wants_sql = bool(exact_sql_ids or SQL_WORDS.search(low) or object_kind) and not property_mapping
    if negative and wants_method:
        wants_sql = False

    if property_mapping:
        intent.add("property")
    if wants_view:
        intent.add("view")
    if wants_sql:
        intent.add("sql")
    if wants_method:
        intent.add("method")
    if not intent:
        intent.add("generic")

    spec = QuerySpec(question=question, identifiers=ids, intent=intent)
    spec.property_mapping = property_mapping
    spec.wants_view = wants_view
    spec.wants_sql = wants_sql
    spec.wants_method = wants_method
    spec.negative = negative

    # Exact type/property/method matches. Constructors are methods, but are only
    # admitted when query intent is method-oriented.
    for name in ids:
        key = name.lower()
        for t in refs.type_by.get(name, []):
            if t["name"].lower() == key:
                spec.type_names.add(t["name"])
        for p in refs.prop_by_name.get(name, []):
            if p["name"].lower() == key:
                spec.property_names.add(p["name"])
        if wants_method:
            for m in refs.method_by.get(name, []):
                if m["name"].lower() == key:
                    spec.method_names.add(m["name"])

    # Typed DbSet mapping: EmployeeBaseView (type) -> EmployeeBaseViews (property)
    # is a resolved type relation, not a substring match.
    for tname in list(spec.type_names):
        for p in refs.props:
            if context_parent(refs, p) and p.get("type") == tname:
                spec.property_names.add(p["name"])

    # Exact SQL procedure names only. Unknown near-names remain unknown.
    by_sql = {norm(s["name"]): s for s in refs.sqls}
    for raw in SQL_TOKEN.findall(question):
        key = norm(raw)
        hit = by_sql.get(key)
        if hit:
            if re.search(r"CREATE\s+VIEW", hit.get("text", ""), re.I):
                spec.view_names.add(hit["name"])
            else:
                spec.sql_names.add(hit["name"])
        else:
            spec.unknown_sql_names.add(raw)

    # Database-object kind queries: exact named SQL object, no substring match.
    if object_kind and not property_mapping:
        for name in ids:
            for s in refs.sqls:
                if s["name"].lower() == name.lower():
                    if re.search(r"CREATE\s+VIEW", s.get("text", ""), re.I):
                        spec.view_names.add(s["name"])
                    else:
                        spec.sql_names.add(s["name"])

    # Exact view object names, only when the query explicitly asks for a view.
    if wants_view:
        by_name = {norm(s["name"]): s for s in refs.sqls}
        for name in ids:
            hit = by_name.get(norm(name))
            if hit and re.search(r"CREATE\s+VIEW", hit.get("text", ""), re.I):
                spec.view_names.add(hit["name"])

    # If an exact method and a SQL symbol are both named, keep both. Generic
    # "Employees"-style names do not veto the real method identifier.
    # A method whose name is also an exact type/property is a constructor-like
    # member, not task evidence unless explicitly requested as a method.
    spec.method_names = {x for x in spec.method_names if x not in spec.type_names and x not in spec.property_names}
    if spec.method_names:
        # Prefer the longest exact method identifier if a query contains a generic
        # noun that is also a method name. This is identifier resolution, not gold.
        maxlen = max(len(x) for x in spec.method_names)
        spec.method_names = {x for x in spec.method_names if len(x) >= max(5, int(maxlen * 0.7))}
    return spec


def typed_candidates(spec: QuerySpec, refs, source) -> list[dict]:
    """Produce typed evidence units from the resolved spec (pre-binding)."""
    out: list[dict] = []

    def add(path, start, end, role, kind, resolved, provenance, score=0.0):
        if not path or start < 1 or end < start:
            return
        out.append({
            "path": path, "start_line": start, "end_line": end, "last_line": end,
            "role": role, "roles": [role], "entity_kind": kind, "resolved": resolved,
            "provenance": [provenance], "liveness": "LIVE", "ambiguity": [],
            "score": score,
        })

    for tname in sorted(spec.type_names):
        for t in refs.type_by.get(tname, []):
            if t["name"] != tname:
                continue
            add(t["path"], t["start"], t["end"], ROLE_TYPE, KIND_TYPE,
                {"name": t["name"], "kind": t["kind"]},
                {"edge": "exact_type", "query": tname}, 3.0)
            for p in refs.props:
                if context_parent(refs, p) and p.get("type") == t["name"]:
                    add(p["path"], p["start"], p["end"], ROLE_PROPERTY, KIND_PROPERTY,
                        {"name": p["name"], "type": p["type"], "parent": p["parent"]},
                        {"edge": "dbset_type", "type": t["name"], "property": p["name"]}, 3.2)

    for pname in sorted(spec.property_names):
        for p in refs.prop_by_name.get(pname, []):
            if p["name"] != pname or not context_parent(refs, p):
                continue
            add(p["path"], p["start"], p["end"], ROLE_PROPERTY, KIND_PROPERTY,
                {"name": p["name"], "type": p["type"], "parent": p["parent"]},
                {"edge": "exact_property", "name": p["name"]}, 3.2)

    for mname in sorted(spec.method_names):
        for m in refs.method_by.get(mname, []):
            if m["name"] != mname:
                continue
            add(m["path"], m["start"], m["end"], ROLE_CONTROL, KIND_METHOD,
                {"name": m["name"], "parent": m.get("parent")},
                {"edge": "exact_method", "name": m["name"]}, 3.0)

    for sname in sorted(spec.sql_names):
        for s in refs.sqls:
            if s["name"] != sname:
                continue
            role = ROLE_VIEW if re.search(r"CREATE\s+VIEW", s.get("text", ""), re.I) else ROLE_SQL
            kind = KIND_VIEW if role == ROLE_VIEW else KIND_SQL_BODY
            add(s["path"], s["start"], s["end"], role, kind,
                {"name": s["name"], "object": kind.lower()},
                {"edge": "exact_sql", "name": s["name"]}, 3.0)

    for sname in sorted(spec.view_names):
        for s in refs.sqls:
            if s["name"] == sname and re.search(r"CREATE\s+VIEW", s.get("text", ""), re.I):
                add(s["path"], s["start"], s["end"], ROLE_VIEW, KIND_VIEW,
                    {"name": s["name"], "object": "view"},
                    {"edge": "exact_view", "name": s["name"]}, 3.0)

    return out


def merge_typed_units(units: list[dict]) -> list[dict]:
    """Merge identical ranges without order-dependent role overwrites."""
    by_key: dict[tuple, dict] = {}
    for u in units:
        key = (u["path"], u["start_line"], u["end_line"])
        if key not in by_key:
            by_key[key] = {**u, "roles": list(dict.fromkeys(u.get("roles", [u["role"]]))),
                           "provenance": list(u.get("provenance", []))}
            continue
        cur = by_key[key]
        cur["roles"] = list(dict.fromkeys(cur["roles"] + u.get("roles", [u["role"]])))
        for p in u.get("provenance", []):
            if p not in cur["provenance"]:
                cur["provenance"].append(p)
        cur["ambiguity"] = list(dict.fromkeys(cur.get("ambiguity", []) + u.get("ambiguity", [])))
        if ROLE_PRIORITY.get(u["role"], 0) > ROLE_PRIORITY.get(cur["role"], 0):
            cur["role"] = u["role"]
            cur["entity_kind"] = u["entity_kind"]
            cur["resolved"] = u["resolved"]
    return list(by_key.values())
