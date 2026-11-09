//! H7 E1S measurement harness (test-only, always `#[ignore]`).
//!
//! Three independent, manually-invoked measurements:
//!
//! * `h7_e1s_reconcile` — same 12 dev cases, four arms in one process:
//!   E1 full-facts refs-off/on and E1R compact refs-off/on. Attributes any
//!   per-case coverage delta to protocol or parser semantics.
//! * `h7_e1s_resource` — the fixed E1R gate harness (compact path) with extra
//!   phase samples. Single-test process: `VmHWM` is the gate value.
//! * `h7_e1s_attribution` — phase/drop/malloc-trim attribution. Not a gate run.
//!
//! No product semantics are exercised here beyond the public extraction APIs.

#![allow(clippy::too_many_lines)]

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

use super::candidate::{
    CandidateItem, ClosureDiagnostics, Seed, expand_with_closure, expand_with_declarations,
};
use super::csharp_facts::{
    DbSetFact, FieldFact, ProjectDeclarations, ProjectFacts, ProjectIdentity, TypeFact,
    build_declaration_index, parse_anchor_facts, parse_project, parse_project_with_references,
};
use super::sql_facts::SqlCatalog;

fn status_kib(key: &str) -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|text| {
            text.lines()
                .find(|line| line.starts_with(key))
                .and_then(|line| line.split_whitespace().nth(1))
                .and_then(|value| value.parse().ok())
        })
        .unwrap_or(0)
}

fn pss_kib() -> u64 {
    std::fs::read_to_string("/proc/self/smaps_rollup")
        .ok()
        .and_then(|text| {
            text.lines()
                .find(|line| line.starts_with("Pss:"))
                .and_then(|line| line.split_whitespace().nth(1))
                .and_then(|value| value.parse().ok())
        })
        .unwrap_or(0)
}

fn io_snapshot() -> BTreeMap<String, u64> {
    std::fs::read_to_string("/proc/self/io")
        .ok()
        .map(|text| {
            text.lines()
                .filter_map(|line| {
                    let (key, value) = line.split_once(':')?;
                    Some((key.trim().to_owned(), value.trim().parse().ok()?))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn io_delta(before: &BTreeMap<String, u64>, after: &BTreeMap<String, u64>) -> serde_json::Value {
    let keys = [
        "rchar",
        "wchar",
        "syscr",
        "syscw",
        "read_bytes",
        "write_bytes",
        "cancelled_write_bytes",
    ];
    let mut out = serde_json::Map::new();
    for key in keys {
        let a = after.get(key).copied().unwrap_or(0);
        let b = before.get(key).copied().unwrap_or(0);
        out.insert(key.to_owned(), serde_json::json!(a.saturating_sub(b)));
    }
    serde_json::Value::Object(out)
}

fn sample(label: &str, start: std::time::Instant) -> serde_json::Value {
    serde_json::json!({
        "label": label,
        "elapsed_ms": start.elapsed().as_secs_f64() * 1000.0,
        "rss_kib": status_kib("VmRSS:"),
        "rss_hwm_kib": status_kib("VmHWM:"),
        "pss_kib": pss_kib(),
    })
}

fn ranges_overlap(a_start: usize, a_end: usize, b_start: usize, b_end: usize) -> bool {
    a_start <= b_end && b_start <= a_end
}

fn covered(items: &[CandidateItem], gold: &[(String, usize, usize)]) -> usize {
    gold.iter()
        .filter(|(path, start, end)| {
            items.iter().any(|item| {
                item.path == *path && ranges_overlap(item.start_line, item.end_line, *start, *end)
            })
        })
        .count()
}

fn rank(items: &[CandidateItem], path: &str, start: usize, end: usize) -> Option<usize> {
    items.iter().position(|item| {
        item.path == path && ranges_overlap(item.start_line, item.end_line, start, end)
    })
}

fn coverage(items: &[CandidateItem], gold: &[(String, usize, usize)]) -> serde_json::Value {
    let at = |budget: usize| covered(&items[..items.len().min(budget)], gold);
    serde_json::json!({
        "10": at(10),
        "50": at(50),
        "100": at(100),
        "250": at(250),
        "500": at(500),
    })
}

fn sort_dedup(items: &mut Vec<CandidateItem>) {
    items.sort_by(|left, right| {
        right
            .score
            .cmp(&left.score)
            .then_with(|| left.path.cmp(&right.path))
            .then_with(|| left.start_line.cmp(&right.start_line))
            .then_with(|| left.end_line.cmp(&right.end_line))
    });
    items.dedup_by(|left, right| {
        left.path == right.path
            && left.start_line == right.start_line
            && left.end_line == right.end_line
    });
}

fn hash_items(items: &[CandidateItem]) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    for item in items {
        item.path.hash(&mut hasher);
        item.start_line.hash(&mut hasher);
        item.end_line.hash(&mut hasher);
        item.score.hash(&mut hasher);
        item.reason.hash(&mut hasher);
    }
    format!("{:016x}", hasher.finish())
}

/// Approximate live heap bytes retained by the declaration index, counting
/// string capacities. Attribution only; not a gate metric.
fn declarations_heap_bytes(declarations: &ProjectDeclarations) -> serde_json::Value {
    let type_bytes: usize = declarations
        .types
        .iter()
        .map(
            |TypeFact {
                 path,
                 namespace,
                 name,
                 kind,
                 ..
             }| {
                std::mem::size_of::<TypeFact>()
                    + path.capacity()
                    + namespace.capacity()
                    + name.capacity()
                    + kind.capacity()
            },
        )
        .sum();
    let field_bytes: usize = declarations
        .fields
        .iter()
        .map(
            |FieldFact {
                 namespace,
                 type_name,
                 name,
                 field_type,
             }| {
                std::mem::size_of::<FieldFact>()
                    + namespace.capacity()
                    + type_name.capacity()
                    + name.capacity()
                    + field_type.capacity()
            },
        )
        .sum();
    let db_set_bytes: usize = declarations
        .db_sets
        .iter()
        .map(
            |DbSetFact {
                 path,
                 context_type,
                 property,
                 element_type,
                 ..
             }| {
                std::mem::size_of::<DbSetFact>()
                    + path.capacity()
                    + context_type.capacity()
                    + property.capacity()
                    + element_type.capacity()
            },
        )
        .sum();
    serde_json::json!({
        "types": declarations.types.len(),
        "fields": declarations.fields.len(),
        "db_sets": declarations.db_sets.len(),
        "types_bytes": type_bytes,
        "fields_bytes": field_bytes,
        "db_sets_bytes": db_set_bytes,
        "total_bytes": type_bytes + field_bytes + db_set_bytes,
    })
}

struct Corpus {
    root: PathBuf,
    by_project: BTreeMap<String, Vec<(String, String)>>,
    manifest: serde_json::Value,
    b0: serde_json::Value,
    dev: serde_json::Value,
}

impl Corpus {
    fn load() -> Self {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let read_json = |path: &str| -> serde_json::Value {
            serde_json::from_str(&std::fs::read_to_string(repo.join(path)).expect("json"))
                .expect("valid json")
        };
        let manifest = read_json("benchmarks/rag-impact/corpus-manifest.json");
        let b0 = read_json("benchmarks/rag-impact/results/b0-official.json");
        let dev = read_json("benchmarks/rag-impact/cases/dev.json");
        let root = PathBuf::from(manifest["source_root"].as_str().expect("root"));
        let read = |path: &str| std::fs::read_to_string(root.join(path)).expect("source");
        let mut by_project: BTreeMap<String, Vec<(String, String)>> = BTreeMap::new();
        for file in manifest["files"].as_array().expect("files") {
            let Some(project) = file["project"].as_str() else {
                continue;
            };
            if file["kind"].as_str() != Some("cs_compile") {
                continue;
            }
            let path = file["path"].as_str().expect("path").to_owned();
            by_project
                .entry(project.to_owned())
                .or_default()
                .push((path.clone(), read(&path)));
        }
        Self {
            root,
            by_project,
            manifest,
            b0,
            dev,
        }
    }

    fn identity(&self, csproj: &str) -> ProjectIdentity {
        ProjectIdentity::from_csproj(&self.root.join(csproj))
    }

    fn catalog(&self) -> SqlCatalog {
        let read = |path: &str| std::fs::read_to_string(self.root.join(path)).expect("source");
        SqlCatalog::from_sources(&[
            (
                "DB/Store/sp_UpdateOrgTotalEmployee.sql".to_owned(),
                read("DB/Store/sp_UpdateOrgTotalEmployee.sql"),
                true,
                "selected_source_variant".to_owned(),
            ),
            (
                "DB/Store/sp_UpdateOrgTotalEmployee_old.sql".to_owned(),
                read("DB/Store/sp_UpdateOrgTotalEmployee_old.sql"),
                false,
                "excluded_variant".to_owned(),
            ),
            (
                "DB/Store/sp_UpdateOrgTotalEmployee_old1.sql".to_owned(),
                read("DB/Store/sp_UpdateOrgTotalEmployee_old1.sql"),
                false,
                "excluded_variant".to_owned(),
            ),
            (
                "DB/Views/EmployeeBaseViews.sql".to_owned(),
                read("DB/Views/EmployeeBaseViews.sql"),
                true,
                "selected_source_variant".to_owned(),
            ),
        ])
    }

    fn declarations(&self) -> ProjectDeclarations {
        let mut declarations = ProjectDeclarations::default();
        declarations.merge(build_declaration_index(
            &self.identity("DBContexts/HRIS/DBContext.Core/DBContext.Core.csproj"),
            self.by_project.get("P3").expect("P3"),
        ));
        declarations.merge(build_declaration_index(
            &self.identity("Services/HRIS/Service.Core/Service.Core.csproj"),
            self.by_project.get("P2").expect("P2"),
        ));
        declarations.merge(build_declaration_index(
            &self.identity("Webs/HRM/HRM.csproj"),
            self.by_project.get("P1").expect("P1"),
        ));
        declarations
    }

    /// E1 protocol: three full project parses merged on top of P1.
    fn full_facts(&self) -> ProjectFacts {
        let p3 = parse_project(
            &self.identity("DBContexts/HRIS/DBContext.Core/DBContext.Core.csproj"),
            self.by_project.get("P3").expect("P3"),
        );
        let p2 = parse_project_with_references(
            &self.identity("Services/HRIS/Service.Core/Service.Core.csproj"),
            self.by_project.get("P2").expect("P2"),
            std::slice::from_ref(&p3),
        );
        let p1 = parse_project_with_references(
            &self.identity("Webs/HRM/HRM.csproj"),
            self.by_project.get("P1").expect("P1"),
            &[p2.clone(), p3.clone()],
        );
        let mut merged = p1;
        for project in [&p2, &p3] {
            merged.files.extend(project.files.clone());
            merged.types.extend(project.types.clone());
            merged.methods.extend(project.methods.clone());
            merged.db_sets.extend(project.db_sets.clone());
            merged.fields.extend(project.fields.clone());
            merged.assignments.extend(project.assignments.clone());
            merged.invocations.extend(project.invocations.clone());
            merged.saves.extend(project.saves.clone());
        }
        merged
    }

    fn dev_cases(&self) -> Vec<(&serde_json::Value, Vec<Seed>, Vec<(String, usize, usize)>)> {
        let mut out = Vec::new();
        for case in self.dev["cases"].as_array().expect("cases") {
            let Some(id) = case["id"].as_str() else {
                continue;
            };
            if !id.starts_with('D')
                || ["D13", "D14", "D15", "D16"]
                    .iter()
                    .any(|prefix| id.starts_with(prefix))
            {
                continue;
            }
            let b0_case = self.b0["quality"]["dev_real_cases"]
                .as_array()
                .expect("b0")
                .iter()
                .find(|entry| entry["id"] == id)
                .expect("case");
            let mut seeds = BTreeSet::new();
            for call in b0_case["calls"].as_array().into_iter().flatten() {
                for unit in call["units"].as_array().into_iter().flatten() {
                    let path = unit["path"].as_str().unwrap_or_default();
                    let start = unit["start_line"].as_u64().unwrap_or(0) as usize;
                    let end = unit["end_line"].as_u64().unwrap_or(0) as usize;
                    if !path.is_empty() && start > 0 {
                        seeds.insert((path.to_owned(), start, end));
                    }
                }
            }
            let seeds: Vec<Seed> = seeds
                .into_iter()
                .map(|(path, start_line, end_line)| Seed {
                    path,
                    start_line,
                    end_line,
                })
                .collect();
            let mut gold = Vec::new();
            for reference in case["source_refs"].as_array().into_iter().flatten() {
                let path = reference["path"].as_str().unwrap_or_default().to_owned();
                for span in reference["spans"].as_array().into_iter().flatten() {
                    if let (Some(start), Some(end)) =
                        (span["start_line"].as_u64(), span["end_line"].as_u64())
                    {
                        gold.push((path.clone(), start as usize, end as usize));
                    }
                }
            }
            out.push((case, seeds, gold));
        }
        out
    }
}

/// Test 1 — reconcile the refs-off delta between E1 (full facts) and E1R
/// (anchor-only facts + compact declarations) under the same harness.
#[test]
#[ignore = "manual measurement: cargo test --lib h7_e1s_reconcile -- --ignored --exact"]
fn h7_e1s_reconcile() {
    let baseline = sample("process_start", std::time::Instant::now());
    let corpus = Corpus::load();
    let catalog = corpus.catalog();
    let declarations = corpus.declarations();
    let full = corpus.full_facts();
    let mut rows = Vec::new();
    for (case, seeds, gold) in corpus.dev_cases() {
        let id = case["id"].as_str().unwrap_or_default();
        let query = case["question"].as_str().unwrap_or_default();
        let anchor_paths: BTreeSet<String> = seeds.iter().map(|seed| seed.path.clone()).collect();
        let all_files: Vec<(String, String)> =
            corpus.by_project.values().flatten().cloned().collect();
        let identity = corpus.identity("Webs/HRM/HRM.csproj");
        let anchor_project =
            parse_anchor_facts(&identity, &all_files, &anchor_paths, &declarations);

        let mut off_full = expand_with_closure(&full, &catalog, &seeds, query, 500, false);
        sort_dedup(&mut off_full);
        let mut on_full = expand_with_closure(&full, &catalog, &seeds, query, 500, true);
        sort_dedup(&mut on_full);
        let mut off_compact = {
            let mut diag = ClosureDiagnostics::default();
            expand_with_declarations(
                &anchor_project,
                &declarations,
                &catalog,
                &seeds,
                query,
                500,
                false,
                &mut diag,
            )
        };
        sort_dedup(&mut off_compact);
        let mut on_compact = {
            let mut diag = ClosureDiagnostics::default();
            expand_with_declarations(
                &anchor_project,
                &declarations,
                &catalog,
                &seeds,
                query,
                500,
                true,
                &mut diag,
            )
        };
        sort_dedup(&mut on_compact);

        // Attribute every gold span covered by E1 off but lost by E1R off.
        let mut lost = Vec::new();
        for (path, start, end) in &gold {
            let in_full = off_full.iter().any(|item| {
                item.path == *path && ranges_overlap(item.start_line, item.end_line, *start, *end)
            });
            let in_compact = off_compact.iter().any(|item| {
                item.path == *path && ranges_overlap(item.start_line, item.end_line, *start, *end)
            });
            if in_full && !in_compact {
                let reason = off_full
                    .iter()
                    .find(|item| {
                        item.path == *path
                            && ranges_overlap(item.start_line, item.end_line, *start, *end)
                    })
                    .map(|item| item.reason.clone())
                    .unwrap_or_default();
                let full_rank = rank(&off_full, path, *start, *end);
                lost.push(serde_json::json!({
                    "path": path,
                    "start": start,
                    "end": end,
                    "full_rank": full_rank,
                    "full_reason": reason,
                    "anchor_file": anchor_paths.contains(path),
                }));
            }
        }
        // Gold spans lost by both off arms are not attribution-relevant here.
        let mut on_diff = Vec::new();
        for (path, start, end) in &gold {
            let full_cov = on_full.iter().any(|item| {
                item.path == *path && ranges_overlap(item.start_line, item.end_line, *start, *end)
            });
            let compact_cov = on_compact.iter().any(|item| {
                item.path == *path && ranges_overlap(item.start_line, item.end_line, *start, *end)
            });
            if full_cov != compact_cov {
                on_diff.push(serde_json::json!({
                    "path": path,
                    "start": start,
                    "end": end,
                    "on_full": full_cov,
                    "on_compact": compact_cov,
                }));
            }
        }
        rows.push(serde_json::json!({
            "id": id,
            "gold": gold.len(),
            "anchor_files": anchor_paths.len(),
            "off_full": coverage(&off_full, &gold),
            "off_compact": coverage(&off_compact, &gold),
            "on_full": coverage(&on_full, &gold),
            "on_compact": coverage(&on_compact, &gold),
            "candidates_off_full": off_full.len(),
            "candidates_off_compact": off_compact.len(),
            "candidates_on_full": on_full.len(),
            "candidates_on_compact": on_compact.len(),
            "ranks_off_full": gold.iter().map(|(p,s,e)| rank(&off_full,p,*s,*e)).collect::<Vec<_>>(),
            "ranks_off_compact": gold.iter().map(|(p,s,e)| rank(&off_compact,p,*s,*e)).collect::<Vec<_>>(),
            "lost_off_spans": lost,
            "on_arm_diff": on_diff,
            "hash_on_full": hash_items(&on_full),
            "hash_on_compact": hash_items(&on_compact),
        }));
    }
    let end = sample("end", std::time::Instant::now());
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "H7_E1S_RECONCILE",
            "baseline": baseline,
            "end": end,
            "rows": rows,
        }))
        .expect("json")
    );
}

/// Test 2 — fixed E1R gate harness (compact path) with phase samples.
/// Run as its own process; `rss_hwm_kib` is the gate value.
#[test]
#[ignore = "manual measurement: cargo test --lib h7_e1s_resource -- --ignored --exact"]
fn h7_e1s_resource() {
    let start = std::time::Instant::now();
    let mut phases = vec![sample("process_start", start)];
    let corpus = Corpus::load();
    phases.push(sample("corpus_loaded", start));
    let io_before = io_snapshot();
    let declarations = corpus.declarations();
    phases.push(sample("declarations_built", start));
    let phase_io = io_delta(&io_before, &io_snapshot());
    let catalog = corpus.catalog();
    phases.push(sample("catalog_built", start));
    let all_files: Vec<(String, String)> = corpus.by_project.values().flatten().cloned().collect();
    phases.push(sample("all_files_cloned", start));
    let identity = corpus.identity("Webs/HRM/HRM.csproj");

    let mut rows = Vec::new();
    let mut totals = BTreeMap::new();
    for (case, seeds, gold) in corpus.dev_cases() {
        let id = case["id"].as_str().unwrap_or_default();
        let query = case["question"].as_str().unwrap_or_default();
        let anchor_paths: BTreeSet<String> = seeds.iter().map(|seed| seed.path.clone()).collect();
        let anchor_before = sample("anchor_before", start);
        let project = parse_anchor_facts(&identity, &all_files, &anchor_paths, &declarations);
        let anchor_after = sample("anchor_after", start);
        let evaluate = |closure: bool| {
            let mut diagnostics = ClosureDiagnostics::default();
            let mut items = expand_with_declarations(
                &project,
                &declarations,
                &catalog,
                &seeds,
                query,
                500,
                closure,
                &mut diagnostics,
            );
            sort_dedup(&mut items);
            (items, diagnostics)
        };
        let io_case_before = io_snapshot();
        let off_start = std::time::Instant::now();
        let (off, _) = evaluate(false);
        let off_ms = off_start.elapsed().as_secs_f64() * 1000.0;
        let on_start = std::time::Instant::now();
        let (on, on_diag) = evaluate(true);
        let on_ms = on_start.elapsed().as_secs_f64() * 1000.0;
        let io_case = io_delta(&io_case_before, &io_snapshot());
        let after_case = sample("after_case", start);
        let row = serde_json::json!({
            "id": id,
            "gold": gold.len(),
            "coverage": coverage(&on, &gold),
            "refs_off": coverage(&off, &gold),
            "ranks_on": gold.iter().map(|(p,s,e)| rank(&on,p,*s,*e)).collect::<Vec<_>>(),
            "ranks_off": gold.iter().map(|(p,s,e)| rank(&off,p,*s,*e)).collect::<Vec<_>>(),
            "candidates": on.len(),
            "closure_items": on_diag.closure_items,
            "diagnostics": on_diag,
            "wall_off_ms": off_ms,
            "wall_on_ms": on_ms,
            "anchor_files": anchor_paths.len(),
            "anchor_pss_before_kib": anchor_before["pss_kib"],
            "anchor_pss_after_kib": anchor_after["pss_kib"],
            "rss_hwm_kib_after_case": after_case["rss_hwm_kib"],
            "io_case": io_case,
            "closure_hash": hash_items(&on),
        });
        for (budget, key) in [
            (10, "10"),
            (50, "50"),
            (100, "100"),
            (250, "250"),
            (500, "500"),
        ] {
            let value = covered(&on[..on.len().min(budget)], &gold);
            *totals.entry(key.to_owned()).or_insert(0u64) += value as u64;
        }
        rows.push(row);
    }
    let end = sample("end", start);
    let source_bytes: u64 = corpus.manifest["files"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|file| file["bytes"].as_u64())
        .sum();
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "H7_E1S_RESOURCE",
            "totals": totals,
            "phases": phases,
            "phase_io": phase_io,
            "source_bytes": source_bytes,
            "declarations": declarations_heap_bytes(&declarations),
            "rows": rows,
            "end": end,
            "rss_hwm_kib": status_kib("VmHWM:"),
            "pss_end_kib": pss_kib(),
        }))
        .expect("json")
    );
}

/// Test 3 — attribution only: phase residency, live declaration size, allocator
/// retention after drop + trim. Not a gate run.
#[test]
#[ignore = "manual attribution: cargo test --lib h7_e1s_attribution -- --ignored --exact"]
fn h7_e1s_attribution() {
    let start = std::time::Instant::now();
    let mut phases = vec![sample("process_start", start)];
    let corpus = Corpus::load();
    phases.push(sample("corpus_loaded", start));
    phases.push(serde_json::json!({
        "label": "corpus_held",
        "source_bytes": corpus.by_project.values().flatten().map(|(p,s)| p.len() + s.len()).sum::<usize>(),
        "files": corpus.by_project.values().flatten().count(),
    }));
    let declarations = corpus.declarations();
    phases.push(sample("declarations_built", start));
    phases.push(serde_json::json!({
        "label": "declarations_live",
        "declarations": declarations_heap_bytes(&declarations),
    }));
    let all_files: Vec<(String, String)> = corpus.by_project.values().flatten().cloned().collect();
    phases.push(sample("all_files_cloned", start));
    let identity = corpus.identity("Webs/HRM/HRM.csproj");
    let cases = corpus.dev_cases();
    let (_, seeds, _) = &cases[0];
    let anchor_paths: BTreeSet<String> = seeds.iter().map(|seed| seed.path.clone()).collect();
    let project = parse_anchor_facts(&identity, &all_files, &anchor_paths, &declarations);
    phases.push(sample("one_anchor_parsed", start));
    phases.push(serde_json::json!({
        "label": "anchor_project_live",
        "files": project.files.len(),
        "methods": project.methods.len(),
        "invocations": project.invocations.len(),
        "assignments": project.assignments.len(),
        "saves": project.saves.len(),
    }));
    drop(project);
    phases.push(sample("anchor_project_dropped", start));
    drop(all_files);
    phases.push(sample("all_files_dropped", start));
    drop(declarations);
    phases.push(sample("declarations_dropped", start));
    drop(corpus);
    phases.push(sample("corpus_dropped", start));
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "H7_E1S_ATTRIBUTION",
            "phases": phases,
            "rss_hwm_kib": status_kib("VmHWM:"),
        }))
        .expect("json")
    );
}

/// Test 4 — isolate anchor-parse cost: same anchor files parsed (a) with the
/// empty scope and (b) through `parse_anchor_facts` (project scope cloned into
/// each parsed file). Measurement only.
#[test]
#[ignore = "manual attribution: cargo test --lib h7_e1s_anchor_scaling -- --ignored --exact"]
fn h7_e1s_anchor_scaling() {
    use super::csharp_facts::parse_file;
    let start = std::time::Instant::now();
    let corpus = Corpus::load();
    let declarations = corpus.declarations();
    let identity = corpus.identity("Webs/HRM/HRM.csproj");
    let cases = corpus.dev_cases();
    let (_, seeds, _) = &cases[0];
    let mut anchor_paths: Vec<String> = seeds.iter().map(|seed| seed.path.clone()).collect();
    anchor_paths.sort();
    anchor_paths.dedup();
    let all_files: Vec<(String, String)> = corpus.by_project.values().flatten().cloned().collect();
    let by_path: BTreeMap<&str, &str> = all_files
        .iter()
        .map(|(path, source)| (path.as_str(), source.as_str()))
        .collect();
    let mut phases = vec![sample("declarations_ready", start)];
    for n in [1usize, 2, 4, 8, 16, 26] {
        let subset: Vec<&String> = anchor_paths.iter().take(n).collect();
        let mut facts = Vec::new();
        for path in &subset {
            let source = by_path.get(path.as_str()).expect("source");
            facts.push(parse_file(&identity, path, source));
        }
        let live: usize = facts
            .iter()
            .map(|f| f.methods.len() + f.invocations.len())
            .sum();
        drop(facts);
        phases.push(sample(&format!("empty_scope_{n}"), start));
        phases.last_mut().expect("sample");
        if let Some(last) = phases.last_mut() {
            last["methods_and_invocations"] = serde_json::json!(live);
        }
    }
    for n in [1usize, 2, 4, 8, 16, 26] {
        let subset: BTreeSet<String> = anchor_paths.iter().take(n).cloned().collect();
        let project = parse_anchor_facts(&identity, &all_files, &subset, &declarations);
        let live = project.files.len() + project.methods.len() + project.invocations.len();
        drop(project);
        phases.push(sample(&format!("project_scope_{n}"), start));
        if let Some(last) = phases.last_mut() {
            last["facts"] = serde_json::json!(live);
        }
    }
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "H7_E1S_ANCHOR_SCALING",
            "declarations": declarations_heap_bytes(&declarations),
            "phases": phases,
            "rss_hwm_kib": status_kib("VmHWM:"),
        }))
        .expect("json")
    );
}

/// Test 5 — isolate parser-construction churn: 820 C# sources parsed with a
/// fresh `Parser` per file versus one reused parser, in alternating passes.
/// Measurement only; no product code involved.
#[test]
#[ignore = "manual attribution: cargo test --lib h7_e1s_parser_churn -- --ignored --exact"]
fn h7_e1s_parser_churn() {
    use tree_sitter::Parser;
    let start = std::time::Instant::now();
    let corpus = Corpus::load();
    let sources: Vec<&str> = corpus
        .by_project
        .values()
        .flatten()
        .map(|(_, source)| source.as_str())
        .collect();
    let mut phases = vec![sample("sources_loaded", start)];
    let fresh_pass = |label: &str, phases: &mut Vec<serde_json::Value>| {
        for source in &sources {
            let mut parser = Parser::new();
            parser
                .set_language(&tree_sitter_c_sharp::LANGUAGE.into())
                .expect("language");
            let tree = parser.parse(source, None).expect("tree");
            drop(tree);
        }
        phases.push(sample(label, start));
    };
    let reused_pass = |label: &str, phases: &mut Vec<serde_json::Value>| {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_c_sharp::LANGUAGE.into())
            .expect("language");
        for source in &sources {
            let tree = parser.parse(source, None).expect("tree");
            drop(tree);
        }
        phases.push(sample(label, start));
    };
    fresh_pass("fresh_1", &mut phases);
    reused_pass("reused_1", &mut phases);
    fresh_pass("fresh_2", &mut phases);
    reused_pass("reused_2", &mut phases);
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "H7_E1S_PARSER_CHURN",
            "files": sources.len(),
            "phases": phases,
            "rss_hwm_kib": status_kib("VmHWM:"),
        }))
        .expect("json")
    );
}
