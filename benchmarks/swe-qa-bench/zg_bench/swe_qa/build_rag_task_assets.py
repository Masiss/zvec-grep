"""Build the frozen public-source R4 answer-quality task assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[3]
SOURCE_LOCK = REPO / "research/public-source-evaluation-20261123/source.lock.json"
CORPUS = Path("/tmp/zvec-public-source-20261123")
OUTPUT = PACKAGE / "data/rag-r4-v1"


TASKS: list[dict[str, Any]] = [
    {"id":"a01","split":"development","type":"abstention","repo":"antlr","question":"Can this token stream serialize its cursor and restore parser state in a different process after a restart?","steps":["The checked source does not define cross-process token-stream state persistence, so the answer must abstain."]},
    {"id":"a02","split":"development","type":"direct_lookup","repo":"antlr","question":"After a buffered token stream has advanced, what does Reset do to its current position?","steps":["Reset delegates to Seek(0), which initializes the stream if needed and sets the adjusted token index."] ,"ranges":[("antlr/runtime/CSharp/src/BufferedTokenStream.cs",146,154)]},
    {"id":"a03","split":"development","type":"multi_step","repo":"antlr","question":"How does the buffered stream fetch tokens in batches, and what makes it stop fetching at end-of-input?","steps":["Fetch returns immediately after EOF has already been recorded and otherwise asks the token source for at most the requested batch size.","When a fetched token is EOF, the stream records that state and returns the short batch size; Fill stops when a batch is shorter than its limit."] ,"ranges":[("antlr/runtime/CSharp/src/BufferedTokenStream.cs",232,253),("antlr/runtime/CSharp/src/BufferedTokenStream.cs",687,700)]},
    {"id":"a04","split":"development","type":"vocabulary_divergent","repo":"antlr","question":"How does an unbuffered stream keep already-consumed input available while a caller holds a checkpoint, and when can it discard that prefix?","steps":["Mark creates a nested marker and records the current buffer start for the outermost marker.","Release validates LIFO marker order and only shifts away consumed tokens when the final marker is released."] ,"ranges":[("antlr/runtime/CSharp/src/UnbufferedTokenStream.cs",282,313)]},
    {"id":"d05","split":"development","type":"direct_lookup","repo":"dapper","question":"What value does ExecuteScalar<T> return from a command, and how does it pass the command to the implementation?","steps":["The generic overload returns the first selected cell converted to T.","It delegates to ExecuteScalarImpl<T> with the connection and command definition."] ,"ranges":[("dapper/Dapper/SqlMapper.cs",600,625)]},
    {"id":"d06","split":"development","type":"multi_step","repo":"dapper","question":"When Execute receives an enumerable of parameter objects, how are commands run by default, and what changes when Pipelined is enabled?","steps":["The multi-execution branch detects an enumerable parameter collection and reuses a command while clearing and rebinding parameters for later objects.","Without Pipelined it executes sequentially and sums affected rows; with Pipelined it delegates to the asynchronous multi-execution path."] ,"ranges":[("dapper/Dapper/SqlMapper.cs",636,690)]},
    {"id":"d07","split":"development","type":"direct_lookup","repo":"dapper","question":"What does DynamicParameters.RemoveUnused control when Dapper builds the command parameter list?","steps":["RemoveUnused defaults to true and is passed to the parameter-info generator when DynamicParameters adds command parameters."] ,"ranges":[("dapper/Dapper/DynamicParameters.cs",29,39),("dapper/Dapper/DynamicParameters.cs",169,205)]},
    {"id":"d08","split":"development","type":"multi_step","repo":"dapper","question":"How does SqlBuilder combine accumulated clauses into a template and keep their parameter values available?","steps":["Clause resolution adds each clause's parameters to DynamicParameters and joins inclusive clauses inside an OR group while joining ordinary clauses with the configured joiner.","Template resolution replaces matching comment markers with resolved clauses, removes leftover markers, and caches the result until the builder sequence changes."] ,"ranges":[("dapper/Dapper.SqlBuilder/SqlBuilder.cs",29,54),("dapper/Dapper.SqlBuilder/SqlBuilder.cs",64,108)]},
    {"id":"n09","split":"development","type":"abstention","repo":"newtonsoft","question":"Does JsonTextWriter validate each emitted document against a JSON Schema before writing it?","steps":["The pinned writer source does not establish JSON Schema validation, so the answer must abstain."]},
    {"id":"n10","split":"development","type":"direct_lookup","repo":"newtonsoft","question":"How does JsonTextWriter serialize a decimal value?","steps":["It advances writer state as a Float token and writes JsonConvert.ToString(value) through WriteValueInternal."] ,"ranges":[("newtonsoft/Src/Newtonsoft.Json/JsonTextWriter.cs",622,640)]},
    {"id":"n11","split":"development","type":"multi_step","repo":"newtonsoft","question":"How does JsonTextReader handle comments while reading tokens, and which comment forms does its parser recognize?","steps":["Read dispatches to value, object, or post-value parsing based on reader state and permits comments after the root value.","ParseComment distinguishes slash-slash from slash-star and emits or skips the comment according to setToken."] ,"ranges":[("newtonsoft/Src/Newtonsoft.Json/JsonTextReader.cs",414,459),("newtonsoft/Src/Newtonsoft.Json/JsonTextReader.cs",2258,2300)]},
    {"id":"n12","split":"development","type":"vocabulary_divergent","repo":"newtonsoft","question":"When asked to close whichever JSON container is currently open, how does JsonWriter choose the matching closing token?","steps":["WriteEnd inspects the current container type and routes object, array, or constructor state to its matching end token."] ,"ranges":[("newtonsoft/Src/Newtonsoft.Json/JsonWriter.cs",490,506),("newtonsoft/Src/Newtonsoft.Json/JsonWriter.cs",715,750)]},
    {"id":"a13","split":"validation","type":"abstention","repo":"antlr","question":"Can the C# lexer runtime guarantee that its token position survives machine failure and can be resumed from a saved checkpoint?","steps":["The source provides in-process marker and seek behavior but no crash-safe persistence guarantee, so the answer must abstain."]},
    {"id":"a14","split":"validation","type":"multi_step","repo":"antlr","question":"What bookkeeping lets an unbuffered token stream support nested checkpoints, and how is the buffer compacted after checkpoint use ends?","steps":["The outermost Mark records the last-token buffer start and every marker is assigned a decreasing negative identifier.","Release rejects a marker that is not the most recently opened one and compacts consumed tokens only after the marker count reaches zero."] ,"ranges":[("antlr/runtime/CSharp/src/UnbufferedTokenStream.cs",282,313)]},
    {"id":"a15","split":"validation","type":"direct_lookup","repo":"antlr","question":"When a lexer rule requests More, what marker does Lexer store for the current token attempt?","steps":["More sets the current token type to TokenTypes.More so lexing continues without emitting the current match as a separate token."] ,"ranges":[("antlr/runtime/CSharp/src/Lexer.cs",224,233)]},
    {"id":"a16","split":"validation","type":"vocabulary_divergent","repo":"antlr","question":"How can a lexer temporarily enter a nested lexical context and return to the previous context?","steps":["PushMode stores the current mode before switching to the requested mode.","PopMode throws if the stack is empty, otherwise restores and returns the previous mode."] ,"ranges":[("antlr/runtime/CSharp/src/Lexer.cs",234,250)]},
    {"id":"d17","split":"validation","type":"abstention","repo":"dapper","question":"Does Dapper itself configure TLS certificate validation for the database connection, regardless of provider?","steps":["The source delegates connection security configuration to the supplied connection/provider and does not establish a universal TLS policy, so the answer must abstain."]},
    {"id":"d18","split":"validation","type":"direct_lookup","repo":"dapper","question":"What distinguishes QueryFirstOrDefault<T> from QueryFirst<T> when the result set contains no rows?","steps":["QueryFirst uses Row.First and QueryFirstOrDefault uses Row.FirstOrDefault, delegating their empty-result behavior to QueryRowImpl."] ,"ranges":[("dapper/Dapper/SqlMapper.cs",868,894),("dapper/Dapper/SqlMapper.cs",1297,1338)]},
    {"id":"d19","split":"validation","type":"multi_step","repo":"dapper","question":"How does Query<T> decide whether to materialize results before returning or expose deferred enumeration?","steps":["The buffered argument is converted into the CommandFlags.Buffered flag in the CommandDefinition.","QueryImpl creates the row sequence, and the public overload materializes it with ToList only when the command is buffered."] ,"ranges":[("dapper/Dapper/SqlMapper.cs",846,867),("dapper/Dapper/SqlMapper.cs",1204,1235)]},
    {"id":"d20","split":"validation","type":"multi_step","repo":"dapper","question":"When SqlBuilder resolves a template, how do clause parameters and unfilled template markers behave?","steps":["Resolving clauses merges their parameter objects into DynamicParameters.","Template resolution substitutes markers for registered clauses and removes markers that remain unmatched."] ,"ranges":[("dapper/Dapper.SqlBuilder/SqlBuilder.cs",29,54),("dapper/Dapper.SqlBuilder/SqlBuilder.cs",64,108)]},
    {"id":"n21","split":"validation","type":"direct_lookup","repo":"newtonsoft","question":"Which characters does JsonTextWriter emit to begin an object and an array?","steps":["WriteStartObject updates writer state and emits an opening brace.","WriteStartArray updates writer state and emits an opening bracket."] ,"ranges":[("newtonsoft/Src/Newtonsoft.Json/JsonTextWriter.cs",212,230)]},
    {"id":"n22","split":"validation","type":"multi_step","repo":"newtonsoft","question":"When WriteToken is given an object or array start token, how does JsonWriter translate those tokens into writer operations?","steps":["The token dispatcher routes StartObject to WriteStartObject and StartArray to WriteStartArray.","The writer's end-token operations update container state and emit matching object or array closure."] ,"ranges":[("newtonsoft/Src/Newtonsoft.Json/JsonWriter.cs",525,555),("newtonsoft/Src/Newtonsoft.Json/JsonWriter.cs",422,450)]},
    {"id":"n23","split":"validation","type":"direct_lookup","repo":"newtonsoft","question":"After JsonTextReader reaches the completed root value, how does Read treat trailing whitespace, comments, and other text?","steps":["In Finished state, Read consumes whitespace and returns false at EOF.","It can return a trailing comment token, while other trailing content raises JsonReaderException."] ,"ranges":[("newtonsoft/Src/Newtonsoft.Json/JsonTextReader.cs",414,459)]},
    {"id":"n24","split":"validation","type":"vocabulary_divergent","repo":"newtonsoft","question":"When a JSON-like value starts with a constructor keyword followed by a name and parentheses, how does the reader capture the constructor name and enter that value?","steps":["The parser matches the constructor keyword, scans the constructor name until whitespace or an opening parenthesis, and rejects invalid characters or an unterminated name.","It requires an opening parenthesis, advances past it, clears recent string state, and emits a StartConstructor token carrying the captured name."] ,"ranges":[("newtonsoft/Src/Newtonsoft.Json/JsonTextReader.cs",1878,1960)]},
]


def main() -> None:
    lock = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
    repositories = {row["name"]: row for row in lock["repositories"]}
    prompt_tasks = []
    gold_tasks = []
    references = []
    for task in TASKS:
        repository = repositories[task["repo"]]
        prompt = task["question"]
        prompt_tasks.append(
            {
                "task_id": task["id"],
                "question": prompt,
                "question_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "repository": task["repo"],
                "repository_commit": repository["upstream_revision"],
                "split": task["split"],
                "task_type": task["type"],
            }
        )
        accepted = []
        for range_index, (path, start, end) in enumerate(task.get("ranges", []), start=1):
            contents = (CORPUS / path).read_bytes()
            digest = hashlib.sha256(contents).hexdigest()
            locked = next((entry for entry in repository["files"] if entry["path"] == path), None)
            if locked is None or locked["sha256"] != digest:
                raise ValueError(f"source lock mismatch for {path}")
            accepted.append(
                {
                    "repository": task["repo"],
                    "range_id": f"{task['id']}-range-{range_index}",
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "source_sha256": digest,
                }
            )
        range_ids = [source_range["range_id"] for source_range in accepted]
        required_steps = []
        for index, description in enumerate(task["steps"], start=1):
            supporting_ranges = (
                [range_ids[index - 1]]
                if len(range_ids) >= len(task["steps"])
                else list(range_ids)
            )
            required_steps.append(
                {
                    "step_id": f"{task['id']}-step-{index}",
                    "description": description,
                    "accepted_range_ids": supporting_ranges,
                }
            )
        gold_tasks.append(
            {
                "task_id": task["id"],
                "split": task["split"],
                "outcome": "abstain" if task["type"] == "abstention" else "answerable",
                "required_steps": required_steps,
                "accepted_source_ranges": accepted,
            }
        )
        references.append(
            {
                "task_id": task["id"],
                "question": prompt,
                "reference_answer": " ".join(step["description"] for step in required_steps),
            }
        )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    prompts = {
        "schema_version": 1,
        "corpus_id": lock["corpus_id"],
        "gold_visibility": "grader-only",
        "answer_instructions": (
            "Answer only the repository behavior asked about. Cite every material claim with "
            "a source path and line or line range from the evidence provided. Distinguish "
            "source-visible behavior from deployment behavior. State when the evidence is "
            "insufficient and abstain from unsupported claims. Do not modify the repository."
        ),
        "tasks": prompt_tasks,
    }
    gold = {"schema_version": 1, "corpus_id": lock["corpus_id"], "tasks": gold_tasks}
    reference_asset = {
        "schema_version": 1,
        "corpus_id": lock["corpus_id"],
        "visibility": "judge-only",
        "references": references,
    }
    (OUTPUT / "prompts.json").write_text(json.dumps(prompts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "gold.json").write_text(json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "references.json").write_text(json.dumps(reference_asset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "corpus_id": lock["corpus_id"],
        "source_lock_sha256": hashlib.sha256(SOURCE_LOCK.read_bytes()).hexdigest(),
        "assets": {
            name: hashlib.sha256((OUTPUT / name).read_bytes()).hexdigest()
            for name in ("prompts.json", "gold.json", "references.json")
        },
        "task_count": len(prompt_tasks),
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"task_count": len(prompt_tasks), "prompt_count": len(prompt_tasks), "gold_count": len(gold_tasks)}))


if __name__ == "__main__":
    main()
