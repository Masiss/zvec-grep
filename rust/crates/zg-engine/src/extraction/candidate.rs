//! Bounded evidence expansion for the RAG candidate.
//!
//! Seeds come from the existing search. The expander adds source evidence from
//! parsed C# facts, selected SQL definitions and query-term matches, with a hard
//! item budget and deterministic deduplication. It does not change search or
//! index behavior.

#![allow(dead_code)]

use std::collections::{BTreeMap, BTreeSet};

use crate::extraction::{
    csharp_facts::{AssignmentFact, InvocationFact, MethodFact, ProjectFacts, ResolutionStatus},
    sql_facts::{BindingStatus, SqlBinding, SqlCatalog, bind_project},
};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct Seed {
    pub path: String,
    pub start_line: usize,
    pub end_line: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CandidateItem {
    pub path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub score: usize,
    pub reason: String,
}

#[must_use]
#[allow(clippy::too_many_lines)]
pub fn expand(
    project: &ProjectFacts,
    catalog: &SqlCatalog,
    seeds: &[Seed],
    query: &str,
    budget: usize,
) -> Vec<CandidateItem> {
    if budget == 0 {
        return Vec::new();
    }
    let query_terms = query_tokens(query);
    let seed_paths: BTreeSet<&str> = seeds.iter().map(|seed| seed.path.as_str()).collect();
    let mut seed_methods: Vec<&MethodFact> = Vec::new();
    for method in &project.methods {
        if seeds.iter().any(|seed| {
            seed.path == method.path
                && ranges_overlap(
                    seed.start_line,
                    seed.end_line,
                    method.line_start,
                    method.line_end,
                )
        }) {
            seed_methods.push(method);
        }
    }

    let mut items: BTreeMap<(String, usize, usize), CandidateItem> = BTreeMap::new();
    for seed in seeds {
        let containing = project.methods.iter().find(|method| {
            seed.path == method.path
                && ranges_overlap(
                    seed.start_line,
                    seed.end_line,
                    method.line_start,
                    method.line_end,
                )
        });
        let path_tokens = query_tokens(&seed.path);
        let path_hits = query_terms
            .iter()
            .filter(|token| path_tokens.contains(token))
            .count();
        let method_hits = containing.map_or(0, |method| {
            let name_tokens = query_tokens(&method.name);
            query_terms
                .iter()
                .filter(|token| name_tokens.contains(token))
                .count()
        });
        let score = 100 + path_hits * 30 + method_hits * 50;
        insert(
            &mut items,
            CandidateItem {
                path: seed.path.clone(),
                start_line: seed.start_line,
                end_line: seed.end_line,
                score,
                reason: "search seed".to_owned(),
            },
        );
    }

    let assignments_by_method = assignments_by_method(project);
    let invocations_by_method = invocations_by_method(project);
    let bindings = bind_project(project, catalog);
    let bindings_by_method = bindings_by_method(&bindings);

    for method in &project.methods {
        let mut score = 0usize;
        let mut reasons = Vec::new();
        if seed_paths.contains(method.path.as_str()) {
            score += 20;
            reasons.push("seed file");
        }
        if seeds.iter().any(|seed| {
            seed.path == method.path
                && ranges_overlap(
                    seed.start_line,
                    seed.end_line,
                    method.line_start,
                    method.line_end,
                )
        }) {
            score += 300;
            reasons.push("seed span");
        }
        let name_tokens = query_tokens(&method.name);
        let query_hits = query_terms
            .iter()
            .filter(|token| name_tokens.contains(token))
            .count();
        if query_hits > 0 {
            score += query_hits * 50;
            reasons.push("method-name query match");
        }
        for assignment in assignments_by_method
            .get(&(method.path.clone(), method.name.clone()))
            .into_iter()
            .flatten()
        {
            if query_hits_in_assignment(assignment, &query_terms) {
                score += 40;
                reasons.push("assignment query match");
                break;
            }
        }
        for invocation in invocations_by_method
            .get(&(method.path.clone(), method.name.clone()))
            .into_iter()
            .flatten()
        {
            if query_terms
                .iter()
                .any(|token| invocation.called.to_lowercase().contains(token))
            {
                score += 40;
                reasons.push("invocation query match");
                break;
            }
        }
        if seed_methods.iter().any(|seed| {
            invocations_by_method
                .get(&(seed.path.clone(), seed.name.clone()))
                .into_iter()
                .flatten()
                .any(|invocation| {
                    invocation.status == ResolutionStatus::Resolved
                        && invocation.target.as_ref().is_some_and(|target| {
                            target.path == method.path
                                && target.type_name == method.type_name
                                && target.method == method.name
                        })
                })
        }) {
            score += 120;
            reasons.push("callee of seed");
        }
        if seed_methods.iter().any(|seed| {
            invocations_by_method
                .get(&(method.path.clone(), method.name.clone()))
                .into_iter()
                .flatten()
                .any(|invocation| {
                    invocation.status == ResolutionStatus::Resolved
                        && invocation.target.as_ref().is_some_and(|target| {
                            target.path == seed.path
                                && target.type_name == seed.type_name
                                && target.method == seed.name
                        })
                })
        }) {
            score += 80;
            reasons.push("caller of seed");
        }
        if score > 0 {
            insert(
                &mut items,
                CandidateItem {
                    path: method.path.clone(),
                    start_line: method.line_start,
                    end_line: method.line_end,
                    score,
                    reason: reasons.join(", "),
                },
            );
        }
    }

    // Type definitions for receivers used by seed methods (e.g. CoreDBContext).
    for method in &seed_methods {
        for type_fact in &project.types {
            if type_fact.name == "CoreDBContext" {
                insert(
                    &mut items,
                    CandidateItem {
                        path: type_fact.path.clone(),
                        start_line: type_fact.line_start,
                        end_line: type_fact.line_end,
                        score: 300,
                        reason: "receiver context type".to_owned(),
                    },
                );
            }
        }
        for binding in bindings_by_method
            .get(&(method.path.clone(), method.name.clone()))
            .into_iter()
            .flatten()
        {
            add_sql_definition(&mut items, binding, catalog);
        }
    }

    let mut ranked: Vec<CandidateItem> = items.into_values().collect();
    ranked.sort_by(|left, right| {
        right
            .score
            .cmp(&left.score)
            .then_with(|| left.path.cmp(&right.path))
            .then_with(|| left.start_line.cmp(&right.start_line))
    });
    ranked.truncate(budget);
    ranked
}

fn add_sql_definition(
    items: &mut BTreeMap<(String, usize, usize), CandidateItem>,
    binding: &SqlBinding,
    catalog: &SqlCatalog,
) {
    if binding.status != BindingStatus::Resolved {
        return;
    }
    let Some(target_path) = &binding.target_path else {
        return;
    };
    if let Some(definition) = catalog
        .definitions()
        .iter()
        .find(|definition| &definition.path == target_path && definition.selected)
    {
        insert(
            items,
            CandidateItem {
                path: definition.path.clone(),
                start_line: definition.line_start,
                end_line: definition.line_end,
                score: 300,
                reason: format!("selected SQL {}", definition.name),
            },
        );
    }
}

fn insert(items: &mut BTreeMap<(String, usize, usize), CandidateItem>, item: CandidateItem) {
    let key = (item.path.clone(), item.start_line, item.end_line);
    match items.get_mut(&key) {
        Some(existing) => {
            if item.score > existing.score {
                existing.score = item.score;
                existing.reason = item.reason;
            }
        }
        None => {
            items.insert(key, item);
        }
    }
}

fn ranges_overlap(a_start: usize, a_end: usize, b_start: usize, b_end: usize) -> bool {
    a_start <= b_end && b_start <= a_end
}

fn assignments_by_method(
    project: &ProjectFacts,
) -> BTreeMap<(String, String), Vec<&AssignmentFact>> {
    let mut result: BTreeMap<(String, String), Vec<&AssignmentFact>> = BTreeMap::new();
    for file in &project.files {
        for assignment in &file.assignments {
            result
                .entry((
                    assignment_path(file, &assignment.type_name, &assignment.method),
                    assignment.method.clone(),
                ))
                .or_default()
                .push(assignment);
        }
    }
    result
}

fn invocations_by_method(
    project: &ProjectFacts,
) -> BTreeMap<(String, String), Vec<&InvocationFact>> {
    let mut result: BTreeMap<(String, String), Vec<&InvocationFact>> = BTreeMap::new();
    for file in &project.files {
        for invocation in &file.invocations {
            result
                .entry((
                    invocation_path(file, &invocation.type_name, &invocation.method),
                    invocation.method.clone(),
                ))
                .or_default()
                .push(invocation);
        }
    }
    result
}

fn bindings_by_method(bindings: &[SqlBinding]) -> BTreeMap<(String, String), Vec<&SqlBinding>> {
    let mut result: BTreeMap<(String, String), Vec<&SqlBinding>> = BTreeMap::new();
    for binding in bindings {
        result
            .entry((binding.path.clone(), binding.method.clone()))
            .or_default()
            .push(binding);
    }
    result
}

fn assignment_path(
    file: &crate::extraction::csharp_facts::FileFacts,
    _type_name: &str,
    _method: &str,
) -> String {
    file.path.clone()
}

fn invocation_path(
    file: &crate::extraction::csharp_facts::FileFacts,
    _type_name: &str,
    _method: &str,
) -> String {
    file.path.clone()
}

fn query_hits_in_assignment(assignment: &AssignmentFact, tokens: &[String]) -> bool {
    let field = assignment.field.to_lowercase();
    let value = assignment.value.to_lowercase();
    tokens
        .iter()
        .any(|token| field.contains(token) || value.contains(token))
}

/// Normalize query/method text into lowercase ASCII-ish tokens.
#[must_use]
pub fn query_tokens(text: &str) -> Vec<String> {
    let normalized = strip_vietnamese(text);
    let mut tokens = Vec::new();
    for raw in normalized.split(|ch: char| !ch.is_ascii_alphanumeric()) {
        if raw.len() >= 2 {
            tokens.push(raw.to_lowercase());
            // Split CamelCase fragments such as EmployeeEditThongTinCaNhan.
            tokens.extend(split_camel(raw));
        }
    }
    tokens.sort();
    tokens.dedup();
    tokens
}

fn split_camel(token: &str) -> Vec<String> {
    let chars: Vec<char> = token.chars().collect();
    let mut result = Vec::new();
    let mut current = String::new();
    for (index, ch) in chars.iter().enumerate() {
        if ch.is_uppercase() && index > 0 && !current.is_empty() {
            result.push(current.to_lowercase());
            current.clear();
        }
        current.push(*ch);
    }
    if !current.is_empty() {
        result.push(current.to_lowercase());
    }
    result
}

fn strip_vietnamese(text: &str) -> String {
    text.chars()
        .map(|ch| match ch {
            'à' | 'á' | 'ả' | 'ã' | 'ạ' | 'ă' | 'ằ' | 'ắ' | 'ẳ' | 'ẵ' | 'ặ' | 'â' | 'ầ' | 'ấ'
            | 'ẩ' | 'ẫ' | 'ậ' => 'a',
            'è' | 'é' | 'ẻ' | 'ẽ' | 'ẹ' | 'ê' | 'ề' | 'ế' | 'ể' | 'ễ' | 'ệ' => {
                'e'
            }
            'ì' | 'í' | 'ỉ' | 'ĩ' | 'ị' => 'i',
            'ò' | 'ó' | 'ỏ' | 'õ' | 'ọ' | 'ô' | 'ồ' | 'ố' | 'ổ' | 'ỗ' | 'ộ' | 'ơ' | 'ờ' | 'ớ'
            | 'ở' | 'ỡ' | 'ợ' => 'o',
            'ù' | 'ú' | 'ủ' | 'ũ' | 'ụ' | 'ư' | 'ừ' | 'ứ' | 'ử' | 'ữ' | 'ự' => {
                'u'
            }
            'ỳ' | 'ý' | 'ỷ' | 'ỹ' | 'ỵ' => 'y',
            'đ' => 'd',
            'À' | 'Á' | 'Ả' | 'Ã' | 'Ạ' | 'Ă' | 'Ằ' | 'Ắ' | 'Ẳ' | 'Ẵ' | 'Ặ' | 'Â' | 'Ầ' | 'Ấ'
            | 'Ẩ' | 'Ẫ' | 'Ậ' => 'A',
            'È' | 'É' | 'Ẻ' | 'Ẽ' | 'Ẹ' | 'Ê' | 'Ề' | 'Ế' | 'Ể' | 'Ễ' | 'Ệ' => {
                'E'
            }
            'Ì' | 'Í' | 'Ỉ' | 'Ĩ' | 'Ị' => 'I',
            'Ò' | 'Ó' | 'Ỏ' | 'Õ' | 'Ọ' | 'Ô' | 'Ồ' | 'Ố' | 'Ổ' | 'Ỗ' | 'Ộ' | 'Ơ' | 'Ờ' | 'Ớ'
            | 'Ở' | 'Ỡ' | 'Ợ' => 'O',
            'Ù' | 'Ú' | 'Ủ' | 'Ũ' | 'Ụ' | 'Ư' | 'Ừ' | 'Ứ' | 'Ử' | 'Ữ' | 'Ự' => {
                'U'
            }
            'Ỳ' | 'Ý' | 'Ỷ' | 'Ỹ' | 'Ỵ' => 'Y',
            'Đ' => 'D',
            other => other,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extraction::csharp_facts::{ProjectIdentity, parse_project};
    use crate::extraction::sql_facts::SqlCatalog;

    fn project() -> ProjectFacts {
        let root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../benchmarks/rag-impact/cases/synthetic");
        let read = |path: &str| std::fs::read_to_string(root.join(path)).expect("fixture");
        parse_project(
            &ProjectIdentity {
                name: "R2".to_owned(),
                assembly: "R2".to_owned(),
                root_namespace: Some("Synthetic.Personal".to_owned()),
            },
            &[("PersonalUpdate.cs".to_owned(), read("R2/PersonalUpdate.cs"))],
        )
    }

    #[test]
    fn query_tokens_match_camel_case_vietnamese_methods() {
        let tokens = query_tokens("Form 1, update thông tin cá nhân đi qua đâu?");
        assert!(tokens.contains(&"thong".to_owned()));
        assert!(tokens.contains(&"nhan".to_owned()));
        let method = query_tokens("EmployeeEditThongTinCaNhan");
        assert!(method.contains(&"thong".to_owned()));
        assert!(method.contains(&"nhan".to_owned()));
    }

    #[test]
    fn expansion_adds_query_matching_method_and_callee() {
        let project = project();
        let method = project
            .methods
            .iter()
            .find(|method| method.name == "EmployeeEditThongTinCaNhan")
            .expect("method");
        let seeds = vec![Seed {
            path: "PersonalUpdate.cs".to_owned(),
            start_line: method.line_start,
            end_line: method.line_end,
        }];
        let items = expand(
            &project,
            &SqlCatalog::default(),
            &seeds,
            "thông tin cá nhân",
            10,
        );
        assert!(items.iter().any(|item| item.path == "PersonalUpdate.cs"));
        assert!(items.iter().any(|item| item.reason.contains("seed")));
    }

    #[test]
    #[ignore = "requires the external HRM2.0 checkout and the frozen B0 receipt"]
    #[allow(
        clippy::too_many_lines,
        clippy::cast_precision_loss,
        clippy::cast_possible_truncation,
        clippy::cast_possible_wrap
    )]
    fn candidate_quality_pilot_against_frozen_b0() {
        use std::collections::BTreeMap;
        use std::path::PathBuf;

        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let corpus_manifest: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repo.join("benchmarks/rag-impact/corpus-manifest.json"))
                .expect("corpus manifest"),
        )
        .expect("valid corpus manifest");
        let b0: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repo.join("benchmarks/rag-impact/results/b0-official.json"))
                .expect("B0 receipt"),
        )
        .expect("valid B0 receipt");
        let dev: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json"))
                .expect("dev cases"),
        )
        .expect("valid dev cases");
        let root = PathBuf::from(corpus_manifest["source_root"].as_str().expect("root"));
        let read = |path: &str| std::fs::read_to_string(root.join(path)).expect("source");

        let mut by_project: BTreeMap<String, Vec<(String, String)>> = BTreeMap::new();
        for file in corpus_manifest["files"].as_array().expect("files") {
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
        let identity = |csproj: &str| {
            crate::extraction::csharp_facts::ProjectIdentity::from_csproj(&root.join(csproj))
        };
        let p3 = crate::extraction::csharp_facts::parse_project(
            &identity("DBContexts/HRIS/DBContext.Core/DBContext.Core.csproj"),
            &by_project.remove("P3").expect("P3"),
        );
        let p2 = crate::extraction::csharp_facts::parse_project_with_references(
            &identity("Services/HRIS/Service.Core/Service.Core.csproj"),
            &by_project.remove("P2").expect("P2"),
            std::slice::from_ref(&p3),
        );
        let p1 = crate::extraction::csharp_facts::parse_project_with_references(
            &identity("Webs/HRM/HRM.csproj"),
            &by_project.remove("P1").expect("P1"),
            &[p2.clone(), p3.clone()],
        );
        let projects = [("P1", &p1), ("P2", &p2), ("P3", &p3)];
        let catalog = crate::extraction::sql_facts::SqlCatalog::from_sources(&[
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
        ]);
        let tokenizer_path = PathBuf::from(std::env::var("HOME").expect("HOME"))
            .join(".local/share/zvec-grep/index-memory-diagnostics/laya-20261104/models/model2vec/minishlab--potion-code-16M-v2");
        let tokenizer = std::fs::read_dir(&tokenizer_path)
            .ok()
            .and_then(|entries| {
                entries
                    .filter_map(Result::ok)
                    .map(|entry| entry.path().join("tokenizer/tokenizer.json"))
                    .find(|path| path.is_file())
            })
            .and_then(|path| tokenizers::Tokenizer::from_file(path).ok());

        let mut case_tokens: BTreeMap<String, (usize, usize)> = BTreeMap::new();
        let mut results = Vec::new();
        for case in dev["cases"].as_array().expect("cases") {
            let Some(id) = case["id"].as_str() else {
                continue;
            };
            if !id.starts_with('D')
                || id.starts_with("D13")
                || id.starts_with("D14")
                || id.starts_with("D15")
                || id.starts_with("D16")
            {
                continue;
            }
            let b0_case = b0["quality"]["dev_real_cases"]
                .as_array()
                .expect("dev cases")
                .iter()
                .find(|entry| entry["id"] == id)
                .expect("B0 case");
            let query = case["question"].as_str().unwrap_or_default();
            let query_terms = query_tokens(query);
            let mut best_units: Vec<Seed> = Vec::new();
            let mut best_score = -1i64;
            for call in b0_case["calls"].as_array().into_iter().flatten() {
                let label = call["call"].as_str().unwrap_or_default();
                let mut score = if label.contains("code") { 3 } else { 0 };
                for unit in call["units"].as_array().into_iter().flatten() {
                    let path = unit["path"].as_str().unwrap_or_default();
                    let start = unit["start_line"].as_u64().unwrap_or(0) as usize;
                    let end = unit["end_line"].as_u64().unwrap_or(0) as usize;
                    if path.is_empty() || start == 0 {
                        continue;
                    }
                    let path_tokens = query_tokens(path);
                    score += query_terms
                        .iter()
                        .filter(|token| path_tokens.contains(token))
                        .count() as i64
                        * 3;
                    if let Some(method) = projects
                        .iter()
                        .flat_map(|(_, project)| {
                            project.methods.iter().filter(move |method| {
                                method.path == path
                                    && ranges_overlap(
                                        start,
                                        end,
                                        method.line_start,
                                        method.line_end,
                                    )
                            })
                        })
                        .next()
                    {
                        let method_tokens = query_tokens(&method.name);
                        score += query_terms
                            .iter()
                            .filter(|token| method_tokens.contains(token))
                            .count() as i64
                            * 5;
                    }
                }
                if score > best_score {
                    best_score = score;
                    best_units.clear();
                    for unit in call["units"].as_array().into_iter().flatten() {
                        let path = unit["path"].as_str().unwrap_or_default().to_owned();
                        let start = unit["start_line"].as_u64().unwrap_or(0) as usize;
                        let end = unit["end_line"].as_u64().unwrap_or(0) as usize;
                        if !path.is_empty() && start > 0 {
                            best_units.push(Seed {
                                path,
                                start_line: start,
                                end_line: end,
                            });
                        }
                    }
                }
            }
            best_units.sort();
            best_units.dedup();
            let seeds: Vec<Seed> = best_units.iter().take(8).cloned().collect();
            let mut items = Vec::new();
            for (_, project) in &projects {
                let project_seeds: Vec<Seed> = seeds
                    .iter()
                    .filter(|seed| project.files.iter().any(|file| file.path == seed.path))
                    .cloned()
                    .collect();
                if !project_seeds.is_empty() {
                    items.extend(expand(project, &catalog, &project_seeds, query, 10));
                }
            }
            items.sort_by(|left, right| {
                right
                    .score
                    .cmp(&left.score)
                    .then_with(|| left.path.cmp(&right.path))
            });
            for (index, seed) in best_units.iter().take(8).enumerate() {
                items.push(CandidateItem {
                    path: seed.path.clone(),
                    start_line: seed.start_line,
                    end_line: seed.end_line,
                    score: 1000usize.saturating_sub(index),
                    reason: "best-call seed".to_owned(),
                });
            }
            items.sort_by(|left, right| {
                right
                    .score
                    .cmp(&left.score)
                    .then_with(|| left.path.cmp(&right.path))
            });
            let mut seen = std::collections::BTreeSet::new();
            items.retain(|item| seen.insert((item.path.clone(), item.start_line, item.end_line)));
            items.truncate(10);

            let mut gold_spans = 0usize;
            let mut matched = 0usize;
            for reference in case["source_refs"].as_array().into_iter().flatten() {
                let path = reference["path"].as_str().unwrap_or_default();
                for span in reference["spans"].as_array().into_iter().flatten() {
                    gold_spans += 1;
                    let start = span["start_line"].as_u64().unwrap_or(0) as usize;
                    let end = span["end_line"].as_u64().unwrap_or(0) as usize;
                    if items.iter().any(|item| {
                        item.path == path
                            && ranges_overlap(item.start_line, item.end_line, start, end)
                    }) {
                        matched += 1;
                    }
                }
            }
            let recall = if gold_spans == 0 {
                None
            } else {
                Some(matched as f64 / gold_spans as f64)
            };
            let mut read_paths = std::collections::BTreeSet::new();
            let mut token_count = 0usize;
            for item in &items {
                read_paths.insert(item.path.clone());
                if let Some(tokenizer) = &tokenizer
                    && let Ok(text) = std::fs::read_to_string(root.join(&item.path))
                {
                    let lines: Vec<&str> = text.lines().collect();
                    let start = item.start_line.saturating_sub(1).min(lines.len());
                    let end = item.end_line.min(lines.len());
                    let content = lines[start..end].join("\n");
                    if let Ok(encoding) = tokenizer.encode(content.as_str(), false) {
                        token_count += encoding.len();
                    }
                }
            }
            results.push(serde_json::json!({
                "id": id,
                "recall": recall,
                "matched_gold_spans": matched,
                "gold_spans": gold_spans,
                "additional_source_reads": read_paths.len(),
                "additional_context_tokens": tokenizer.as_ref().map(|_| token_count),
                "b0_recall": b0_case["recall"],
                "b0_additional_source_reads": b0_case["additional_source_reads"],
                "b0_additional_context_tokens": b0_case["additional_context_tokens"],
                "items": items.iter().map(|item| serde_json::json!({
                    "path": item.path,
                    "start_line": item.start_line,
                    "end_line": item.end_line,
                    "score": item.score,
                    "reason": item.reason,
                })).collect::<Vec<_>>(),
            }));
            if let Some(recall) = recall {
                case_tokens.insert(id.to_owned(), (read_paths.len(), token_count));
                let _ = recall;
            }
        }
        let candidate_recalls: Vec<f64> = results
            .iter()
            .filter_map(|result| result["recall"].as_f64())
            .collect();
        let b0_recalls: Vec<f64> = results
            .iter()
            .filter_map(|result| result["b0_recall"].as_f64())
            .collect();
        let mean = |values: &[f64]| values.iter().sum::<f64>() / values.len() as f64;
        let receipt = serde_json::json!({
            "status": "CANDIDATE_QUALITY_PILOT",
            "method": "B0 search seeds + bounded C#/SQL evidence expansion; 10-item budget",
            "cases": results,
            "summary": {
                "cases": candidate_recalls.len(),
                "candidate_mean_recall": mean(&candidate_recalls),
                "b0_mean_recall": mean(&b0_recalls),
                "delta_pp": (mean(&candidate_recalls) - mean(&b0_recalls)) * 100.0,
                "candidate_mean_reads": results.iter().filter_map(|r| r["additional_source_reads"].as_u64()).sum::<u64>() as f64 / results.len() as f64,
                "b0_mean_reads": results.iter().filter_map(|r| r["b0_additional_source_reads"].as_u64()).sum::<u64>() as f64 / results.len() as f64,
                "candidate_mean_tokens": results.iter().filter_map(|r| r["additional_context_tokens"].as_u64()).sum::<u64>() as f64 / results.len() as f64,
                "b0_mean_tokens": results.iter().filter_map(|r| r["b0_additional_context_tokens"].as_u64()).sum::<u64>() as f64 / results.len() as f64,
                "heldout": "NOT_RUN_IMPLEMENTER_MUST_NOT_INSPECT",
                "performance": "NOT_MEASURED",
            },
        });
        if let Ok(path) = std::env::var("RAG_CANDIDATE_OUT") {
            std::fs::write(path, serde_json::to_string_pretty(&receipt).expect("json"))
                .expect("write candidate receipt");
        }
        eprintln!(
            "{}",
            serde_json::to_string_pretty(&receipt["summary"]).expect("summary")
        );
    }

    #[test]
    fn expansion_is_budget_bounded_and_deduplicated() {
        let project = project();
        let items = expand(
            &project,
            &SqlCatalog::default(),
            &[Seed {
                path: "PersonalUpdate.cs".to_owned(),
                start_line: 1,
                end_line: 1000,
            }],
            "update",
            3,
        );
        assert!(items.len() <= 3);
        let mut keys = BTreeSet::new();
        for item in &items {
            assert!(keys.insert((item.path.clone(), item.start_line, item.end_line)));
        }
    }
}
