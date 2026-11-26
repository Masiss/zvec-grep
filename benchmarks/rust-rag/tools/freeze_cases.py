#!/usr/bin/env python3
"""Freeze a Rust RAG corpus manifest and source-verified gold cases.

The case file is evaluation-only. Runtime policies receive `question` only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CORPUS_COMMIT = "4201754a400f676c9744da66383c89eb9f41af88"

CASES = [
    {
        "id": "R01",
        "question": "When a command line value is accepted, where is the typed conversion actually performed and how does the converted value reach the matcher?",
        "expected_evidence": "Parser pushes raw OsStrings through the argument's value parser; ValueParser dispatches to a concrete parser and the AnyValue is added to ArgMatcher with an index.",
        "source_refs": [
            {"path": "clap_builder/src/parser/parser.rs", "spans": [{"start_line": 1092, "end_line": 1115}]},
            {"path": "clap_builder/src/builder/value_parser.rs", "spans": [{"start_line": 235, "end_line": 268}]},
            {"path": "clap_builder/src/builder/arg.rs", "spans": [{"start_line": 4482, "end_line": 4484}]},
        ],
        "facts": [
            "push_arg_values iterates each raw value and increments the parser index",
            "arg.get_value_parser().parse_ref converts the OsString before matcher insertion",
            "ValueParser dispatches through any_value_parser to a concrete AnyValueParser",
        ],
        "grader_keywords": ["parse_ref", "value_parser", "matcher", "OsString"],
        "gaps": ["No claim about how a specific user type implements TypedValueParser."],
    },
    {
        "id": "R02",
        "question": "If a derived options struct declares a default with an expression, how does that expression become a builder default before parsing?",
        "expected_evidence": "The derive attribute handler emits a default_value method with a OnceLock-backed string, then field methods are attached to the generated Command.",
        "source_refs": [
            {"path": "clap_derive/src/item.rs", "spans": [{"start_line": 559, "end_line": 611}]},
            {"path": "clap_derive/src/derives/parser.rs", "spans": [{"start_line": 29, "end_line": 46}]},
            {"path": "clap_derive/src/derives/args.rs", "spans": [{"start_line": 176, "end_line": 205}]},
        ],
        "facts": [
            "default_value_t without an expression uses Default::default on the field type",
            "the default is stored as a OnceLock-backed &'static str",
            "ValueEnum defaults are converted to possible-value names",
        ],
        "grader_keywords": ["OnceLock", "default_value", "Default", "ValueEnum"],
        "gaps": ["No claim that runtime command construction is executed here."],
    },
    {
        "id": "R03",
        "question": "When help is requested during parsing, what turns the rendered help text into the special help error the caller sees?",
        "expected_evidence": "Command::render_help calls write_help; Parser::help_err wraps the styled text with Error::display_help.",
        "source_refs": [
            {"path": "clap_builder/src/parser/parser.rs", "spans": [{"start_line": 1621, "end_line": 1624}]},
            {"path": "clap_builder/src/builder/command.rs", "spans": [{"start_line": 1004, "end_line": 1012}]},
            {"path": "clap_builder/src/output/help.rs", "spans": [{"start_line": 9, "end_line": 39}]},
            {"path": "clap_builder/src/error/mod.rs", "spans": [{"start_line": 362, "end_line": 370}]},
        ],
        "facts": [
            "help_err calls write_help_err then ClapError::display_help",
            "render_help builds the command and passes Usage into write_help",
            "display_help uses ErrorKind::DisplayHelp",
        ],
        "grader_keywords": ["display_help", "DisplayHelp", "render_help", "write_help"],
        "gaps": ["No claim about terminal styling implementation."],
    },
    {
        "id": "R04",
        "question": "How does the parser hand control to a selected nested command and keep its matches separate from the parent?",
        "expected_evidence": "Parser::parse_subcommand builds a nested parser for the chosen command and stores its ArgMatcher under the parent matcher's subcommand entry.",
        "source_refs": [
            {"path": "clap_builder/src/parser/parser.rs", "spans": [{"start_line": 715, "end_line": 762}]},
            {"path": "clap_builder/src/builder/command.rs", "spans": [{"start_line": 3932, "end_line": 3940}]},
        ],
        "facts": [
            "parse_subcommand is called with the matched subcommand name and remaining raw args",
            "the nested parser owns a separate ArgMatcher",
            "get_subcommands exposes the configured subcommand definitions",
        ],
        "grader_keywords": ["parse_subcommand", "subcommand", "ArgMatcher", "raw_args"],
        "gaps": ["No claim about external subcommands."],
    },
    {
        "id": "R05",
        "question": "When an option name is misspelled, where does clap compute possible corrections and attach them to the error?",
        "expected_evidence": "suggestions.rs computes string-distance candidates; parser builds did_you_mean_error and unknown_argument stores them as error context.",
        "source_refs": [
            {"path": "clap_builder/src/parser/features/suggestions.rs", "spans": [{"start_line": 8, "end_line": 40}]},
            {"path": "clap_builder/src/parser/parser.rs", "spans": [{"start_line": 1552, "end_line": 1618}]},
            {"path": "clap_builder/src/error/mod.rs", "spans": [{"start_line": 717, "end_line": 760}]},
        ],
        "facts": [
            "did_you_mean returns sorted candidate strings based on string similarity",
            "did_you_mean_error computes a suggestion before calling unknown_argument",
            "unknown_argument stores suggestions in the error context",
        ],
        "grader_keywords": ["did_you_mean", "suggestion", "UnknownArgument", "context"],
        "gaps": ["No claim about the exact Jaro threshold."],
    },
    {
        "id": "R06",
        "question": "When an option is absent from argv but configured with an environment variable, how is that value admitted with the correct provenance?",
        "expected_evidence": "Parser::add_env reads arg.env and calls react with ValueSource::EnvVariable; the shared path converts and inserts the value.",
        "source_refs": [
            {"path": "clap_builder/src/parser/parser.rs", "spans": [{"start_line": 1411, "end_line": 1439}]},
            {"path": "clap_builder/src/parser/parser.rs", "spans": [{"start_line": 1092, "end_line": 1115}]},
        ],
        "facts": [
            "add_env iterates configured arguments and checks arg.env",
            "react is called with ValueSource::EnvVariable",
            "the value goes through the same parse_ref and matcher insertion path",
        ],
        "grader_keywords": ["add_env", "EnvVariable", "react", "parse_ref"],
        "gaps": ["No claim about OS-specific environment encoding."],
    },
    {
        "id": "R07",
        "question": "How is a cluster of one-letter flags split into individual flags, and how does the parser stop and treat the remainder as a value?",
        "expected_evidence": "clap_lex ShortFlags::next_flag yields chars then invalid UTF-8 suffix; Parser::parse_short_arg loops over flags and may return a hyphen value or value source.",
        "source_refs": [
            {"path": "clap_lex/src/lib.rs", "spans": [{"start_line": 439, "end_line": 465}]},
            {"path": "clap_builder/src/parser/parser.rs", "spans": [{"start_line": 881, "end_line": 940}]},
        ],
        "facts": [
            "next_flag consumes utf8_prefix before invalid_suffix",
            "parse_short_arg checks allow_hyphen_values and negative numbers before iterating",
            "an unknown short flag is returned as NoMatchingArg",
        ],
        "grader_keywords": ["next_flag", "short_arg", "hyphen", "NoMatchingArg"],
        "gaps": ["No claim about shell preprocessing."],
    },
    {
        "id": "R08",
        "question": "Where does shell completion generation build the command and delegate to a shell-specific writer?",
        "expected_evidence": "clap_complete::generate sets bin_name then _generate builds the Command and calls Generator::generate; Bash writes the completion function.",
        "source_refs": [
            {"path": "clap_complete/src/aot/generator/mod.rs", "spans": [{"start_line": 284, "end_line": 291}]},
            {"path": "clap_complete/src/aot/shells/bash.rs", "spans": [{"start_line": 14, "end_line": 45}]},
        ],
        "facts": [
            "generate sets the binary name before delegating",
            "_generate calls cmd.build() then generator.generate(cmd, buf)",
            "Bash generates a shell function named from the binary name",
        ],
        "grader_keywords": ["set_bin_name", "build", "Generator", "Bash"],
        "gaps": ["No claim about dynamic completion engine."],
    },
    {
        "id": "R09",
        "question": "What is the ROFF assembly order when clap_mangen renders a full manual page?",
        "expected_evidence": "Man::render creates a Roff writer and appends title/name/synopsis/description/options/subcommands/extra/version/authors sections with feature checks.",
        "source_refs": [
            {"path": "clap_mangen/src/lib.rs", "spans": [{"start_line": 151, "end_line": 174}]},
        ],
        "facts": [
            "render starts from Roff::default",
            "options are emitted only when the command has arguments",
            "subcommands are emitted only when app_has_subcommands is true",
        ],
        "grader_keywords": ["Roff", "render", "options", "subcommands"],
        "gaps": ["No claim about installed man page layout."],
    },
    {
        "id": "R10",
        "question": "How does ArgMatches return a typed value and prevent a caller from requesting the wrong Rust type?",
        "expected_evidence": "get_one delegates to try_get_one; try_get_arg_t verifies AnyValueId before downcast_ref.",
        "source_refs": [
            {"path": "clap_builder/src/parser/matches/arg_matches.rs", "spans": [{"start_line": 118, "end_line": 120}]},
            {"path": "clap_builder/src/parser/matches/arg_matches.rs", "spans": [{"start_line": 1081, "end_line": 1096}]},
            {"path": "clap_builder/src/parser/matches/arg_matches.rs", "spans": [{"start_line": 1249, "end_line": 1261}]},
        ],
        "facts": [
            "get_one unwraps MatchesError from try_get_one",
            "try_get_one downcasts the stored AnyValue after try_get_arg_t",
            "try_get_arg_t calls verify_arg_t",
        ],
        "grader_keywords": ["get_one", "try_get_one", "downcast", "verify_arg_t"],
        "gaps": ["No claim about remove_one semantics."],
    },
    {
        "id": "R11",
        "question": "After raw matching, which checks decide that required options are missing or that mutually exclusive options were supplied together?",
        "expected_evidence": "Validator::validate builds Conflicts then runs validate_conflicts and validate_required before returning Ok.",
        "source_refs": [
            {"path": "clap_builder/src/parser/validator.rs", "spans": [{"start_line": 24, "end_line": 62}]},
            {"path": "clap_builder/src/parser/validator.rs", "spans": [{"start_line": 62, "end_line": 100}]},
            {"path": "clap_builder/src/parser/validator.rs", "spans": [{"start_line": 221, "end_line": 270}]},
        ],
        "facts": [
            "validate constructs Conflicts::with_args",
            "validate_conflicts runs before validate_required",
            "subcommand_negates_reqs can skip required validation",
        ],
        "grader_keywords": ["validate_conflicts", "validate_required", "Conflicts", "subcommand"],
        "gaps": ["No claim about group-level conflict details."],
    },
    {
        "id": "R12",
        "question": "For a derived struct field marked as flattened, how are the nested argument type's builder methods merged into the parent command?",
        "expected_evidence": "derive args gen_augment recognizes Kind::Flatten and calls augment_args or augment_args_for_update on the inner type with heading/display-order handling.",
        "source_refs": [
            {"path": "clap_derive/src/derives/args.rs", "spans": [{"start_line": 176, "end_line": 270}]},
            {"path": "clap_derive/src/item.rs", "spans": [{"start_line": 1026, "end_line": 1035}]},
        ],
        "facts": [
            "Kind::Flatten is handled in gen_augment",
            "the inner type is extracted before calling augment_args",
            "override_required switches to augment_args_for_update",
        ],
        "grader_keywords": ["Flatten", "augment_args", "augment_args_for_update", "inner_type"],
        "gaps": ["No claim about runtime command behavior."],
    },
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    corpus = args.corpus.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    files = []
    for p in sorted(corpus.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            rel = p.relative_to(corpus).as_posix()
            files.append({"path": rel, "sha256": sha256(p), "bytes": p.stat().st_size})

    manifest = {
        "schema_version": 1,
        "corpus": "clap-rs/clap",
        "commit": CORPUS_COMMIT,
        "file_count": len(files),
        "total_bytes": sum(f["bytes"] for f in files),
        "files": files,
    }
    (out / "corpus-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    cases = []
    for raw in CASES:
        case = json.loads(json.dumps(raw))
        refs = []
        for ref in case["source_refs"]:
            rel = ref["path"]
            p = corpus / rel
            if not p.exists():
                raise SystemExit(f"missing source: {rel}")
            lines = p.read_text(errors="replace").splitlines()
            for span in ref["spans"]:
                if not (1 <= span["start_line"] <= span["end_line"] <= len(lines)):
                    raise SystemExit(f"bad span {rel}:{span}")
            refs.append({
                "path": rel,
                "sha256": sha256(p),
                "spans": ref["spans"],
                "first_line": lines[ref["spans"][0]["start_line"] - 1].strip(),
                "last_line": lines[ref["spans"][-1]["end_line"] - 1].strip(),
            })
        case["source_refs"] = refs
        case["verified"] = True
        case["verification"] = "source hash + span bounds + boundary line check before baseline"
        cases.append(case)

    payload = {
        "schema_version": 1,
        "status": "FROZEN_BEFORE_BASELINE",
        "split": "rust-development",
        "corpus": "clap-rs/clap",
        "corpus_commit": CORPUS_COMMIT,
        "note": "Evaluation only. Runtime policies receive question text only; gold is never used for steering.",
        "cases": cases,
    }
    (out / "cases.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"froze {len(cases)} cases, {len(files)} files, {manifest['total_bytes']} bytes")


if __name__ == "__main__":
    main()
