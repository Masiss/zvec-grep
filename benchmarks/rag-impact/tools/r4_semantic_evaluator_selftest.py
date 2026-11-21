#!/usr/bin/env python3
"""r4_semantic_evaluator_selftest.py — contract fixtures for the packet/accept workflow.

Self-contained: builds a tiny synthetic corpus and a frozen-shaped answer/pack in
a temporary directory, then exercises build_packet and validate_response. No
private source, no network, no adapter.

Usage: r4_semantic_evaluator_selftest.py <work-dir>
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r4_semantic_evaluator as se  # noqa: E402

CASE = "T01"
PROFILE = "arm-c"
PATH = "src/Widget.cs"
SOURCE = "\n".join([
    "class Widget {",
    "    public void Rename(string name) { this.Name = name; }",
    "    public void Save() { Context.SaveChanges(); }",
    "}",
])
SHA = hashlib.sha256(SOURCE.encode()).hexdigest()
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("PASS " if condition else "FAIL ") + name + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(name)


def write_json(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def build_world(root: Path) -> tuple[Path, Path, Path]:
    corpus = root / "corpus"
    (corpus / "src").mkdir(parents=True)
    (corpus / PATH).write_text(SOURCE, encoding="utf-8")
    labels = root / "labels"
    write_json(labels / "cases" / "dev.json",
               {"cases": [{"id": CASE, "question": "What does Widget.Rename do?",
                           "formal": True}]})
    write_json(labels / "cases" / "c3-steps.json",
               {"rubric": {"C2": {"name": "citation support"}},
                "c3_steps": {CASE: ["Cite `Widget.Rename`."]}})
    (labels / "REPORT-CONTRACT.md").write_text("contract", encoding="utf-8")
    answers = root / "answers"
    answers.mkdir(parents=True, exist_ok=True)
    pack = {
        "schema_version": 1, "status": "ok", "gaps": [], "deduplicated": 0,
        "items": [{
            "project": "test", "relative_path": PATH, "sha256": SHA,
            "verification": "verified",
            "range": {"kind": "text", "start_line": 1, "end_line": 2,
                      "start_byte_offset": 0, "end_byte_offset": 0,
                      "start_byte_column": 0, "end_byte_column": 0},
            "content": "\n".join(SOURCE.split("\n")[0:2]),
        }],
    }
    pack_raw = json.dumps(pack, indent=1).encode()
    (answers / f"pack-{CASE}-{PROFILE}.json").write_bytes(pack_raw)
    (answers / f"pack-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(pack_raw).hexdigest() + f"  pack-{CASE}-{PROFILE}.json\n")
    claim = {"text": "Widget.Rename assigns this.Name", "citations": [
        {"path": PATH, "start_line": 1, "end_line": 2, "sha256": SHA}]}
    answer = {
        "case": CASE, "profile": PROFILE, "status": "OK", "error": None,
        "question": {"sha256": hashlib.sha256(
            "What does Widget.Rename do?".encode()).hexdigest(),
            "source": "frozen-case", "formal": True},
        "pack": {"sha256": hashlib.sha256(pack_raw).hexdigest(), "kind": "evidence"},
        "claims": [claim], "gaps": [],
        "final": {"answer": claim["text"], "abstain": False,
                  "claims": [claim], "gaps": []},
    }
    raw = json.dumps(answer, indent=1).encode()
    (answers / f"answer-{CASE}-{PROFILE}.json").write_bytes(raw)
    (answers / f"answer-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + f"  answer-{CASE}-{PROFILE}.json\n")
    return corpus, labels, answers


def response(packet: dict, verdict: str, judgements: list[str], **dims) -> dict:
    dimensions = {name: {"score": dims.get(name, 2 if verdict == "PASS" else 0),
                         "rationale": "fixture"} for name in se.DIMENSIONS}
    return {"packet_sha256": packet["packet_sha256"],
            "verdict": verdict, "rationale": "fixture rationale",
            "claims": [{"index": i, "judgement": j, "reason": "fixture reason"}
                       for i, j in enumerate(judgements)],
            "dimensions": dimensions}


def main() -> int:
    work = Path(sys.argv[1])
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    corpus, labels, answers = build_world(work)

    (corpus / "src" / "Other.cs").write_text("GOLD-SENTINEL-NOT-CITED\n")
    packet = se.build_packet(answers, labels, corpus, CASE, PROFILE, "eval-fixture")
    check("packet builds from frozen artefacts", packet["kind"] == se.PACKET_KIND)
    check("packet binds five frozen inputs",
          set(packet["inputs"]) == {"case", "answer_sha256", "pack_sha256",
                                    "question_sha256", "rubric_sha256"})
    check("packet carries only the cited span content",
          len(packet["cited_spans"]) == 1 and packet["cited_spans"][0]["hash_ok"])
    packet_text = json.dumps(packet)
    check("packet never contains the C3 checklist",
          "Cite `Widget.Rename`." not in packet_text)
    check("packet never contains an uncited corpus file",
          "GOLD-SENTINEL-NOT-CITED" not in packet_text)
    check("packet never contains a scope manifest path",
          "scope-manifest" not in packet_text)
    check("packet item content matches the cited lines",
          packet["evidence"]["items"][0]["content"] == "\n".join(SOURCE.split("\n")[0:2])
          and packet["cited_spans"][0]["content"] == "\n".join(SOURCE.split("\n")[0:2]))
    check("packet_sha256 is stable",
          packet["packet_sha256"] == se.build_packet(
              answers, labels, corpus, CASE, PROFILE, "eval-fixture")["packet_sha256"])
    check("instructions digest is recorded",
          packet["evaluator"]["instructions_sha256"] == hashlib.sha256(
              se.INSTRUCTIONS.encode()).hexdigest())

    good = se.validate_response(packet, response(packet, "PASS", ["supported"]))
    check("valid response accepted", good["verdict"] == "PASS"
          and good["supported_claims"] == [0] and not good["unsupported_claims"])
    check("identity and inputs come from the packet",
          good["evaluator_id"] == "eval-fixture"
          and good["inputs"] == packet["inputs"])
    check("evaluation binds the packet digest",
          good["packet_sha256"] == packet["packet_sha256"])

    def rejected(name: str, payload) -> None:
        try:
            se.validate_response(packet, payload)
        except se.EvaluatorError:
            check(name, True)
        else:
            check(name, False, "accepted")

    rejected("non-object response rejected", ["PASS"])
    rejected("extra top-level key rejected",
             {**response(packet, "PASS", ["supported"]), "evaluator_id": "trusted-evaluator"})
    rejected("missing dimensions rejected",
             {**response(packet, "PASS", ["supported"]), "dimensions": {}})
    rejected("unknown dimension rejected",
             {**response(packet, "PASS", ["supported"]),
              "dimensions": {**response(packet, "PASS", ["supported"])["dimensions"],
                             "vibes": {"score": 2, "rationale": "x"}}})
    bad = response(packet, "PASS", ["supported"])
    bad["dimensions"]["completeness"]["score"] = 3
    rejected("score outside 0..2 rejected", bad)
    bad = response(packet, "PASS", ["supported"])
    bad["dimensions"]["completeness"]["score"] = 1.0
    rejected("float score rejected", bad)
    bad = response(packet, "PASS", ["supported"])
    bad["claims"][0]["index"] = True
    rejected("bool claim index rejected", bad)
    bad = response(packet, "PASS", ["supported", "supported"])
    bad["claims"][1]["index"] = 0
    rejected("duplicate claim index rejected", bad)
    bad = response(packet, "PASS", ["supported"])
    bad["claims"][0]["index"] = 5
    rejected("out-of-range claim index rejected", bad)
    rejected("PASS with an unsupported claim rejected",
             response(packet, "PASS", ["unsupported"]))
    rejected("FAIL without an unsupported claim rejected",
             response(packet, "FAIL", ["supported"]))
    rejected("PASS with citation_support 1 rejected",
             response(packet, "PASS", ["supported"], citation_support=1))
    rejected("PASS with uncertainty 0 rejected",
             response(packet, "PASS", ["supported"], uncertainty=0))
    rejected("empty rationale rejected",
             {**response(packet, "PASS", ["supported"]), "rationale": "  "})
    bad = response(packet, "PASS", ["supported"])
    bad["claims"][0]["reason"] = ""
    rejected("empty claim reason rejected", bad)
    # A response that judges no claim while the answer has one is incomplete.
    rejected("unjudged claim rejected",
             {**response(packet, "PASS", ["supported"]), "claims": []})
    # Wrong types inside a claim entry.
    bad = response(packet, "PASS", ["supported"])
    bad["claims"][0]["judgement"] = ["supported"]
    rejected("non-string judgement rejected", bad)

    tampered = json.loads(json.dumps(packet))
    tampered["answer"]["claims"][0]["text"] = "Widget.Rename launches missiles"

    # A tamper inside the packet body must be caught by the digest, not by luck.
    try:
        se.verify_packet(tampered)
        check("packet digest detects tampering", False)
    except se.EvaluatorError:
        check("packet digest detects tampering", True)

    # A citation outside the delivered pack is never read from the corpus, let
    # alone traversed. It is reported as in_pack=false with no content.
    outside = work / "outside.txt"
    outside.write_text("SECRET-OUTSIDE-CORPUS", encoding="utf-8")
    traversal = work / "traversal"
    shutil.copytree(answers, traversal)
    artifact = json.loads((traversal / f"answer-{CASE}-{PROFILE}.json").read_text())
    artifact["claims"][0]["citations"][0]["path"] = "../outside.txt"
    se_canonical = json.dumps(artifact)
    (traversal / f"answer-{CASE}-{PROFILE}.json").write_text(se_canonical)
    (traversal / f"answer-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(se_canonical.encode()).hexdigest()
        + f"  answer-{CASE}-{PROFILE}.json\n")
    packet_out = se.build_packet(traversal, labels, corpus, CASE, PROFILE, "eval-fixture")
    check("out-of-pack citation is unresolved and not read",
          packet_out["cited_spans"][0]["in_pack"] is False
          and packet_out["cited_spans"][0]["resolved"] is False
          and packet_out["cited_spans"][0]["content"] is None)
    check("out-of-pack file text never enters the packet",
          "SECRET-OUTSIDE-CORPUS" not in json.dumps(packet_out))

    # A citation wider than the delivered pack span is not delivered evidence,
    # even when its start line is inside the pack: overlap is not delivery.
    wide = work / "wide"
    shutil.copytree(answers, wide)
    artifact = json.loads((wide / f"answer-{CASE}-{PROFILE}.json").read_text())
    artifact["claims"][0]["citations"][0]["end_line"] = 4
    raw = json.dumps(artifact, indent=1)
    (wide / f"answer-{CASE}-{PROFILE}.json").write_text(raw)
    (wide / f"answer-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw.encode()).hexdigest() + f"  answer-{CASE}-{PROFILE}.json\n")
    packet_wide = se.build_packet(wide, labels, corpus, CASE, PROFILE, "eval-fixture")
    check("citation wider than the pack span is in_pack=false",
          packet_wide["cited_spans"][0]["in_pack"] is False)

    # A citation to a directory must not crash the packet build: an unreadable
    # span is reported as unresolved, never as content.
    directory_cite = work / "directory-cite"
    shutil.copytree(answers, directory_cite)
    artifact = json.loads((directory_cite / f"answer-{CASE}-{PROFILE}.json").read_text())
    artifact["claims"][0]["citations"][0]["path"] = "src"
    raw = json.dumps(artifact, indent=1)
    (directory_cite / f"answer-{CASE}-{PROFILE}.json").write_text(raw)
    (directory_cite / f"answer-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw.encode()).hexdigest() + f"  answer-{CASE}-{PROFILE}.json\n")
    packet_dir = se.build_packet(directory_cite, labels, corpus, CASE, PROFILE,
                                 "eval-fixture")
    check("citation to a directory is unresolved, not a crash",
          packet_dir["cited_spans"][0]["resolved"] is False
          and packet_dir["cited_spans"][0]["hash_ok"] is False)

    # Oversized delivered spans are refused rather than truncated (a truncated
    # span would be judged as if it were the whole text). The pack item here has
    # no content, as workflow packs do, so the corpus read path is exercised.
    (corpus / "src" / "Big.cs").write_text("x = 1;\n" * 12000, encoding="utf-8")
    big_sha = hashlib.sha256((corpus / "src" / "Big.cs").read_bytes()).hexdigest()
    big = work / "big"
    shutil.copytree(answers, big)
    pack_path = big / f"pack-{CASE}-{PROFILE}.json"
    pack_doc = json.loads(pack_path.read_text())
    pack_doc["items"].append({
        "project": "test", "relative_path": "src/Big.cs", "sha256": big_sha,
        "verification": "verified",
        "range": {"kind": "text", "start_line": 1, "end_line": 12000,
                  "start_byte_offset": 0, "end_byte_offset": 0,
                  "start_byte_column": 0, "end_byte_column": 0},
    })
    raw_pack = json.dumps(pack_doc, indent=1)
    pack_path.write_text(raw_pack)
    (big / f"pack-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw_pack.encode()).hexdigest()
        + f"  pack-{CASE}-{PROFILE}.json\n")
    artifact = json.loads((big / f"answer-{CASE}-{PROFILE}.json").read_text())
    artifact["claims"][0]["citations"][0].update(
        {"path": "src/Big.cs", "start_line": 1, "end_line": 12000, "sha256": big_sha})
    artifact["pack"]["sha256"] = hashlib.sha256(raw_pack.encode()).hexdigest()
    raw = json.dumps(artifact, indent=1)
    (big / f"answer-{CASE}-{PROFILE}.json").write_text(raw)
    (big / f"answer-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw.encode()).hexdigest() + f"  answer-{CASE}-{PROFILE}.json\n")
    try:
        se.build_packet(big, labels, corpus, CASE, PROFILE, "eval-fixture")
        check("oversized cited span refused", False)
    except se.EvaluatorError:
        check("oversized cited span refused", True)

    # A claimless answer: PASS only when it abstains, never by silence.
    for abstain, expected_pass in ((False, False), (True, False)):
        empty = work / f"empty-{abstain}"
        shutil.copytree(answers, empty)
        artifact = json.loads((empty / f"answer-{CASE}-{PROFILE}.json").read_text())
        artifact["claims"] = []
        artifact["final"]["claims"] = []
        artifact["final"]["abstain"] = abstain
        raw = json.dumps(artifact, indent=1)
        (empty / f"answer-{CASE}-{PROFILE}.json").write_text(raw)
        (empty / f"answer-{CASE}-{PROFILE}.sha256").write_text(
            hashlib.sha256(raw.encode()).hexdigest() + f"  answer-{CASE}-{PROFILE}.json\n")
        packet_empty = se.build_packet(empty, labels, corpus, CASE, PROFILE, "eval-fixture")
        try:
            se.validate_response(packet_empty, response(packet_empty, "PASS", [], abstention=2))
            check(f"claimless answer (abstain={abstain}) PASS rule", expected_pass)
        except se.EvaluatorError:
            check(f"claimless answer (abstain={abstain}) PASS rule", not expected_pass)

    # A NUL byte in a citation path must be a structured refusal, not a crash.
    nul = work / "nul"
    shutil.copytree(answers, nul)
    artifact = json.loads((nul / f"answer-{CASE}-{PROFILE}.json").read_text())
    artifact["claims"][0]["citations"][0]["path"] = "src/Wid\u0000get.cs"
    raw = json.dumps(artifact, indent=1)
    (nul / f"answer-{CASE}-{PROFILE}.json").write_text(raw)
    (nul / f"answer-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw.encode()).hexdigest() + f"  answer-{CASE}-{PROFILE}.json\n")
    try:
        packet_nul = se.build_packet(nul, labels, corpus, CASE, PROFILE, "eval-fixture")
        refused = packet_nul["cited_spans"][0]["in_pack"] is False
    except se.EvaluatorError:
        refused = True
    check("NUL in citation path refused structurally", refused)

    # An answer whose sidecar no longer matches is refused before a packet exists.
    broken = work / "broken"
    shutil.copytree(answers, broken)
    (broken / f"answer-{CASE}-{PROFILE}.json").write_text("{}", encoding="utf-8")
    try:
        se.build_packet(broken, labels, corpus, CASE, PROFILE, "eval-fixture")
        check("unfrozen answer refused", False)
    except Exception:
        check("unfrozen answer refused", True)

    # Identity is not the model's to choose: accept() refuses an override that
    # disagrees with the packet, before any response is read.
    write_json(work / "packet.json", packet)
    write_json(work / "response.json", response(packet, "PASS", ["supported"]))
    try:
        se.accept(work / "packet.json", work / "response.json", work / "eval.json",
                  identity="trusted-evaluator", corpus=corpus)
        check("identity override refused", False)
    except se.EvaluatorError:
        check("identity override refused", True)

    # P1-2 exploit: rewrite the text the evaluator "read", recompute the packet
    # digest, and bind a PASS response to it. accept must rebuild the span from
    # the delivered pack item and refuse.
    doctored = json.loads(json.dumps(packet))
    doctored["cited_spans"][0]["content"] = "Widget.Rename launches missiles"
    body = {key: value for key, value in doctored.items() if key != "packet_sha256"}
    doctored["packet_sha256"] = hashlib.sha256(
        se.canonical_json(body).encode("utf-8")).hexdigest()
    write_json(work / "doctored-packet.json", doctored)
    write_json(work / "doctored-response.json",
               response(doctored, "PASS", ["supported"]))
    try:
        se.accept(work / "doctored-packet.json", work / "doctored-response.json",
                  work / "doctored-eval.json", corpus=corpus)
        check("doctored cited-span text refused", False)
    except se.EvaluatorError:
        check("doctored cited-span text refused", True)

    # A cited span no claim cites is extra material, not deliverable evidence.
    extra = json.loads(json.dumps(packet))
    extra["cited_spans"].append({**extra["cited_spans"][0],
                                 "path": "src/Other.cs"})
    body = {key: value for key, value in extra.items() if key != "packet_sha256"}
    extra["packet_sha256"] = hashlib.sha256(
        se.canonical_json(body).encode("utf-8")).hexdigest()
    write_json(work / "extra-packet.json", extra)
    write_json(work / "extra-response.json", response(extra, "PASS", ["supported"]))
    try:
        se.accept(work / "extra-packet.json", work / "extra-response.json",
                  work / "extra-eval.json", corpus=corpus)
        check("uncited extra span refused", False)
    except se.EvaluatorError:
        check("uncited extra span refused", True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("ALL SEMANTIC EVALUATOR FIXTURES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
