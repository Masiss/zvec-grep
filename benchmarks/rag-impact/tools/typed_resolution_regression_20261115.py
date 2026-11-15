#!/usr/bin/env python3
"""Regression + metamorphic checks for typed grounding.

These fixtures are regression evidence, not a held-out qualification set.
They are synthetic where possible and do not expose gold labels to runtime code.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from refint_20261115 import RefIndex
from selection_s1_20261115 import Source
from typed_resolution_20261115 import (
    KIND_PROPERTY, KIND_TYPE, KIND_VIEW, ROLE_CONTROL, ROLE_PROPERTY, ROLE_TYPE, ROLE_VIEW,
    merge_typed_units, resolve_query, typed_candidates,
)
from grounding_20261115 import analyze


class Checks:
    def __init__(self):
        self.rows = []

    def check(self, name, condition, detail=""):
        self.rows.append({"name": name, "pass": bool(condition), "detail": detail})
        return bool(condition)

    def result(self):
        failures = [r for r in self.rows if not r["pass"]]
        return {"schema": "rag-gs-regression/v1", "checks": len(self.rows),
                "failures": len(failures), "PASS": not failures,
                "results": self.rows}


def _write_synth(root: Path, widget="Widget", extra_sibling=False, hidden_commented=True):
    (root / "DB").mkdir(parents=True, exist_ok=True)
    (root / "DB" / "WidgetViews.sql").write_text(
        "CREATE VIEW WidgetViews AS\nSELECT w.Id FROM Widgets w;\n"
    )
    (root / "DB" / "WidgetTouch.sql").write_text(
        "CREATE PROCEDURE sp_WidgetTouch\nAS\nBEGIN\n  UPDATE Widgets SET Id = Id;\nEND\n"
    )
    (root / "Models").mkdir(exist_ok=True)
    sibling = "\npublic class WidgetExtra { public int Id { get; set; } }\n" if extra_sibling else ""
    (root / "Models" / f"{widget}.cs").write_text(
        f"namespace Demo;\npublic class {widget}\n{{\n    public int Id {{ get; set; }}\n}}\n{sibling}"
    )
    (root / "Models" / "WidgetMap.cs").write_text(
        "namespace Demo;\npublic class WidgetMap : DbContext\n{\n"
        f"    public DbSet<{widget}> {widget}s {{ get; set; }}\n"
        "}\n"
    )
    comment = "//" if hidden_commented else ""
    (root / "Models" / "WidgetService.cs").write_text(
        "namespace Demo;\npublic class WidgetService : CoreService\n{\n"
        f"    {comment}public void HiddenWidget()\n"
        f"    {comment}{{\n"
        f"    {comment}    CheckDbConnect();\n"
        f"    {comment}}}\n"
        "    public void LiveWidget()\n    {\n        CheckDbConnect();\n    }\n"
        "}\n"
    )


def synth_checks(c: Checks):
    # Multi-role merge must preserve all roles and provenance, not overwrite by
    # processing order.
    merged = merge_typed_units([
        {"path": "x", "start_line": 1, "end_line": 9, "role": ROLE_TYPE, "roles": [ROLE_TYPE],
         "entity_kind": KIND_TYPE, "resolved": {"name": "X"}, "provenance": [{"edge": "type"}], "ambiguity": []},
        {"path": "x", "start_line": 1, "end_line": 9, "role": ROLE_PROPERTY, "roles": [ROLE_PROPERTY],
         "entity_kind": KIND_PROPERTY, "resolved": {"name": "Xs"}, "provenance": [{"edge": "prop"}], "ambiguity": []},
    ])
    c.check("synth.multi_role_union", len(merged) == 1 and set(merged[0]["roles"]) == {ROLE_TYPE, ROLE_PROPERTY}
            and len(merged[0]["provenance"]) == 2, repr(merged))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_synth(root)
        refs = RefIndex(root); src = Source(root)
        p = resolve_query("Which DbSet property maps Widget?", refs)
        c.check("synth.property_mapping_typed", p.type_names == {"Widget"} and "Widgets" in p.property_names,
                repr(p.as_dict()))
        units = typed_candidates(p, refs, src)
        kinds = {(u["role"], u["entity_kind"]) for u in units}
        c.check("synth.property_has_property_and_type",
                (ROLE_PROPERTY, KIND_PROPERTY) in kinds and (ROLE_TYPE, KIND_TYPE) in kinds, repr(kinds))
        c.check("synth.property_is_not_view", all(u["role"] != ROLE_VIEW for u in units), repr(kinds))

        v = resolve_query("Is WidgetViews a view?", refs)
        vunits = typed_candidates(v, refs, src)
        c.check("synth.view_exact", v.view_names == {"WidgetViews"} and
                any(u["role"] == ROLE_VIEW and u["entity_kind"] == KIND_VIEW for u in vunits), repr(v.as_dict()))

        # Intent switch: the same identifier resolves by the requested kind.
        prop2 = resolve_query("Which type is Widget?", refs)
        c.check("synth.intent_switch_type", prop2.type_names == {"Widget"} and not prop2.view_names,
                repr(prop2.as_dict()))

        # Sibling noise must not change target resolution.
        _write_synth(root, extra_sibling=True)
        refs2 = RefIndex(root); src2 = Source(root)
        p2 = resolve_query("Which DbSet property maps Widget?", refs2)
        u2 = typed_candidates(p2, refs2, src2)
        c.check("synth.sibling_does_not_change_target",
                p2.type_names == {"Widget"} and p2.property_names == {"Widgets"} and
                not any("WidgetExtra" in json.dumps(u.get("resolved", {})) for u in u2), repr(p2.as_dict()))

        # Rename must preserve entity kind and typed mapping.
        _write_synth(root, widget="Gadget")
        refs3 = RefIndex(root); src3 = Source(root)
        p3 = resolve_query("Which DbSet property maps Gadget?", refs3)
        u3 = typed_candidates(p3, refs3, src3)
        c.check("synth.rename_preserves_kind",
                p3.type_names == {"Gadget"} and "Gadgets" in p3.property_names and
                any(u["role"] == ROLE_PROPERTY and u["entity_kind"] == KIND_PROPERTY for u in u3),
                repr(p3.as_dict()))

        # Comment/uncomment changes liveness; hidden method is absent while commented.
        _write_synth(root, widget="Gadget", hidden_commented=True)
        refs4 = RefIndex(root)
        c.check("synth.commented_method_absent", not refs4.method_by.get("HiddenWidget"),
                repr(refs4.method_by.get("HiddenWidget")))
        _write_synth(root, widget="Gadget", hidden_commented=False)
        refs5 = RefIndex(root)
        c.check("synth.uncommented_method_live", bool(refs5.method_by.get("HiddenWidget")),
                repr([m["start"] for m in refs5.method_by.get("HiddenWidget", [])]))

        # Source drift: inserting a line changes the indexed range.
        _write_synth(root, widget="Gadget", hidden_commented=False)
        refs6 = RefIndex(root)
        old = refs6.type_by["Gadget"][0]
        pth = root / old["path"]
        text = pth.read_text()
        pth.write_text("// drift\n" + text)
        refs7 = RefIndex(root)
        new = refs7.type_by["Gadget"][0]
        c.check("synth.source_drift_changes_range",
                (new["start"], new["end"]) != (old["start"], old["end"]),
                f"{old['start']}-{old['end']} -> {new['start']}-{new['end']}")


def corpus_checks(c: Checks, root: Path):
    refs = RefIndex(root); src = Source(root)
    q = "Which DbSet property maps EmployeeBaseView, and where is EmployeeBaseView declared?"
    p = resolve_query(q, refs)
    c.check("corpus.property_not_view", p.property_mapping and not p.view_names,
            repr(p.as_dict()))
    units = typed_candidates(p, refs, src)
    c.check("corpus.property_typed_only",
            {u["role"] for u in units} <= {ROLE_TYPE, ROLE_PROPERTY} and
            any(u["role"] == ROLE_PROPERTY for u in units) and any(u["role"] == ROLE_TYPE for u in units),
            repr([(u["role"], u["entity_kind"]) for u in units]))

    qv = "Is EmployeeBaseViews a view or a procedure?"
    pv = resolve_query(qv, refs)
    c.check("corpus.view_exact", pv.view_names == {"EmployeeBaseViews"} and not pv.property_mapping,
            repr(pv.as_dict()))
    uv = typed_candidates(pv, refs, src)
    c.check("corpus.view_not_type", any(u["role"] == ROLE_VIEW and u["entity_kind"] == KIND_VIEW for u in uv)
            and not any(u["role"] == ROLE_TYPE for u in uv), repr([(u["role"], u["entity_kind"]) for u in uv]))

    qn = "Where is sp_UpdateOrgTotalEmployee_2028 defined?"
    pn = resolve_query(qn, refs)
    c.check("corpus.unknown_sql", pn.unknown_sql_names == {"sp_UpdateOrgTotalEmployee_2028"} and not pn.sql_names,
            repr(pn.as_dict()))

    qm = "UpdateEmployeeAttr writes which entity?"
    pm = resolve_query(qm, refs)
    c.check("corpus.method_exact", "UpdateEmployeeAttr" in pm.method_names and pm.wants_method, repr(pm.as_dict()))

    qneg = "Does UpdateEmployeeAttr invoke sp_UpdateOrgTotalEmployee?"
    pneg = resolve_query(qneg, refs)
    c.check("corpus.negative_no_sql_intent", pneg.negative and not pneg.wants_sql, repr(pneg.as_dict()))
    an = analyze(qneg, refs, {"bindings": []}, src)
    c.check("corpus.negative_control_only",
            all(u["role"] == ROLE_CONTROL for u in an["candidates"]),
            repr([(u["role"], u["path"]) for u in an["candidates"]]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    c = Checks()
    synth_checks(c)
    corpus_checks(c, a.root)
    result = c.result()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"checks": result["checks"], "failures": result["failures"], "PASS": result["PASS"]}, indent=1))
    return 0 if result["PASS"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
