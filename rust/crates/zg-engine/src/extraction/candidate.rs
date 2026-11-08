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

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct CandidateItem {
    pub path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub score: usize,
    pub reason: String,
}

#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct ClosureDiagnostics {
    pub closure_items: usize,
    pub resolved: usize,
    pub unresolved: BTreeMap<String, usize>,
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
    expand_with_closure(project, catalog, seeds, query, budget, true)
}

#[must_use]
#[allow(clippy::too_many_lines)]
pub fn expand_with_closure(
    project: &ProjectFacts,
    catalog: &SqlCatalog,
    seeds: &[Seed],
    query: &str,
    budget: usize,
    closure: bool,
) -> Vec<CandidateItem> {
    let mut diagnostics = ClosureDiagnostics::default();
    expand_inner(
        project,
        catalog,
        seeds,
        query,
        budget,
        closure,
        &mut diagnostics,
    )
}

#[must_use]
#[allow(clippy::too_many_lines)]
pub fn expand_with_closure_diagnostics(
    project: &ProjectFacts,
    catalog: &SqlCatalog,
    seeds: &[Seed],
    query: &str,
    budget: usize,
    closure: bool,
) -> (Vec<CandidateItem>, ClosureDiagnostics) {
    let mut diagnostics = ClosureDiagnostics::default();
    let items = expand_inner(
        project,
        catalog,
        seeds,
        query,
        budget,
        closure,
        &mut diagnostics,
    );
    (items, diagnostics)
}

#[allow(clippy::too_many_lines)]
fn expand_inner(
    project: &ProjectFacts,
    catalog: &SqlCatalog,
    seeds: &[Seed],
    query: &str,
    budget: usize,
    closure: bool,
    diagnostics: &mut ClosureDiagnostics,
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

    if closure {
        // Typed closure from observed seed evidence. No identifier special cases:
        // receiver/declaration types are resolved from project facts.
        let mut receiver_types: BTreeMap<String, String> = BTreeMap::new();
        let mut observed_db_set_properties: BTreeSet<String> = BTreeSet::new();
        for method in &seed_methods {
            let anchor = format!("{}:{}-{}", method.path, method.line_start, method.line_end);
            let key = (method.path.clone(), method.name.clone());
            for assignment in assignments_by_method.get(&key).into_iter().flatten() {
                if let Some((_, property)) = assignment.value.split_once('.') {
                    observed_db_set_properties.insert(
                        property
                            .split(['.', '(', ' '])
                            .next()
                            .unwrap_or_default()
                            .to_owned(),
                    );
                }
                if let Some(receiver_type) = &assignment.receiver_type {
                    receiver_types
                        .entry(receiver_type.clone())
                        .or_insert_with(|| {
                            format!(
                                "anchor={anchor}|expr={}.{}",
                                assignment.receiver, assignment.field
                            )
                        });
                }
            }
            for invocation in invocations_by_method.get(&key).into_iter().flatten() {
                if invocation.receiver_type.is_none() {
                    *diagnostics
                        .unresolved
                        .entry("invocation_receiver_type_missing".to_owned())
                        .or_insert(0) += 1;
                }
                if let Some(receiver_type) = &invocation.receiver_type {
                    receiver_types
                        .entry(receiver_type.clone())
                        .or_insert_with(|| {
                            format!(
                                "anchor={anchor}|expr={}.{}()",
                                invocation.receiver.as_deref().unwrap_or_default(),
                                invocation.called
                            )
                        });
                }
            }
            for save in project
                .saves
                .iter()
                .filter(|save| save.method == method.name && save.type_name == method.type_name)
            {
                if let Some(context_type) = &save.context_type {
                    receiver_types
                        .entry(context_type.clone())
                        .or_insert_with(|| {
                            format!("anchor={anchor}|expr={}.SaveChanges()", save.receiver)
                        });
                }
            }
            for binding in bindings_by_method
                .get(&(method.path.clone(), method.name.clone()))
                .into_iter()
                .flatten()
            {
                if binding.status == BindingStatus::Resolved {
                    diagnostics.resolved += 1;
                } else {
                    *diagnostics
                        .unresolved
                        .entry("sql_binding_not_resolved".to_owned())
                        .or_insert(0) += 1;
                }
                add_sql_definition(&mut items, binding, catalog, &anchor);
            }
        }

        // DbSet members expose their element type and declaration span; both are
        // bounded, observed project facts.
        for db_set in &project.db_sets {
            if receiver_types.contains_key(&db_set.context_type)
                && observed_db_set_properties.contains(&db_set.property)
            {
                diagnostics.resolved += 1;
                let expression = format!(
                    "anchor=project-scope|expr={}.{}",
                    db_set.context_type, db_set.property
                );
                receiver_types
                    .entry(db_set.element_type.clone())
                    .or_insert_with(|| expression.clone());
                insert(
                    &mut items,
                    CandidateItem {
                        path: db_set.path.clone(),
                        start_line: db_set.line_start,
                        end_line: db_set.line_end,
                        score: 300,
                        reason: closure_reason(
                            "resolved",
                            &expression,
                            &format!("{}:{}:{}", db_set.path, db_set.line_start, db_set.line_end),
                            &format!("{}:{}:{}", db_set.path, db_set.line_start, db_set.line_end),
                            "dbset-element",
                        ),
                    },
                );
            }
        }

        for (type_name, expression) in &receiver_types {
            let mut found = false;
            for type_fact in &project.types {
                if &type_fact.name != type_name {
                    continue;
                }
                found = true;
                let target = format!(
                    "{}:{}-{}",
                    type_fact.path, type_fact.line_start, type_fact.line_end
                );
                insert(
                    &mut items,
                    CandidateItem {
                        path: type_fact.path.clone(),
                        start_line: type_fact.line_start,
                        end_line: type_fact.line_end,
                        score: 300,
                        reason: closure_reason(
                            "resolved",
                            expression,
                            &target,
                            &target,
                            "receiver-type",
                        ),
                    },
                );
            }
            if found {
                diagnostics.resolved += 1;
            } else {
                *diagnostics
                    .unresolved
                    .entry("type_decl_missing".to_owned())
                    .or_insert(0) += 1;
            }
        }
    }

    diagnostics.closure_items = items
        .values()
        .filter(|item| item.reason.starts_with("resolved|"))
        .count();
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
    anchor: &str,
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
                reason: closure_reason(
                    "resolved",
                    &format!(
                        "anchor={anchor}|expr={}({})",
                        binding.called,
                        binding.object_name.as_deref().unwrap_or_default()
                    ),
                    &format!("{}:{}", definition.path, definition.line_start),
                    &format!(
                        "{}:{}-{}",
                        definition.path, definition.line_start, definition.line_end
                    ),
                    "sql-selected",
                ),
            },
        );
    }
}

fn closure_reason(
    status: &str,
    expression: &str,
    declaration: &str,
    target: &str,
    rule: &str,
) -> String {
    format!("{status}|{expression}|declaration={declaration}|target={target}|rule={rule}")
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
    fn r4a_r4b_followup_experiments() {
        use std::collections::{BTreeMap, BTreeSet};
        use std::path::PathBuf;

        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let b0: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repo.join("benchmarks/rag-impact/results/b0-official.json"))
                .expect("B0 receipt"),
        )
        .expect("valid B0");
        let dev: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json"))
                .expect("dev cases"),
        )
        .expect("valid dev");
        let corpus: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repo.join("benchmarks/rag-impact/corpus-manifest.json"))
                .expect("corpus"),
        )
        .expect("valid corpus");
        let root = PathBuf::from(corpus["source_root"].as_str().expect("root"));
        let read = |path: &str| std::fs::read_to_string(root.join(path)).expect("source");

        let mut by_project: BTreeMap<String, Vec<(String, String)>> = BTreeMap::new();
        for file in corpus["files"].as_array().expect("files") {
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

        let token_count = |items: &[CandidateItem]| -> usize {
            let Some(tokenizer) = &tokenizer else {
                return 0;
            };
            let mut total = 0;
            for item in items {
                if let Ok(text) = std::fs::read_to_string(root.join(&item.path)) {
                    let lines: Vec<&str> = text.lines().collect();
                    let start = item.start_line.saturating_sub(1).min(lines.len());
                    let end = item.end_line.min(lines.len());
                    if let Ok(encoding) =
                        tokenizer.encode(lines[start..end].join("\n").as_str(), false)
                    {
                        total += encoding.len();
                    }
                }
            }
            total
        };
        let file_count = |items: &[CandidateItem]| -> usize {
            items
                .iter()
                .map(|item| item.path.as_str())
                .collect::<BTreeSet<_>>()
                .len()
        };
        let recall = |items: &[CandidateItem], gold: &[(String, usize, usize)]| -> f64 {
            if gold.is_empty() {
                return 0.0;
            }
            let matched = gold
                .iter()
                .filter(|(path, start, end)| {
                    items.iter().any(|item| {
                        item.path == *path
                            && ranges_overlap(item.start_line, item.end_line, *start, *end)
                    })
                })
                .count();
            matched as f64 / gold.len() as f64
        };

        let mut rows = Vec::new();
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
            let mut calls: Vec<(String, Vec<Seed>)> = Vec::new();
            for call in b0_case["calls"].as_array().into_iter().flatten() {
                let label = call["call"].as_str().unwrap_or_default().to_owned();
                let mut seeds = Vec::new();
                for unit in call["units"].as_array().into_iter().flatten() {
                    let path = unit["path"].as_str().unwrap_or_default().to_owned();
                    let start = unit["start_line"].as_u64().unwrap_or(0) as usize;
                    let end = unit["end_line"].as_u64().unwrap_or(0) as usize;
                    if !path.is_empty() && start > 0 {
                        seeds.push(Seed {
                            path,
                            start_line: start,
                            end_line: end,
                        });
                    }
                }
                calls.push((label, seeds));
            }

            // B0 reference pack: the best call's top-10 units.
            let best_call = b0_case["best_call"].as_str().unwrap_or_default();
            let mut b0_pack: Vec<CandidateItem> = Vec::new();
            for (label, seeds) in &calls {
                if label == best_call {
                    for (index, seed) in seeds.iter().take(10).enumerate() {
                        b0_pack.push(CandidateItem {
                            path: seed.path.clone(),
                            start_line: seed.start_line,
                            end_line: seed.end_line,
                            score: 1000usize.saturating_sub(index),
                            reason: "b0 best-call unit".to_owned(),
                        });
                    }
                }
            }
            let gold: Vec<(String, usize, usize)> = case["source_refs"]
                .as_array()
                .into_iter()
                .flatten()
                .flat_map(|reference| {
                    let path = reference["path"].as_str().unwrap_or_default().to_owned();
                    reference["spans"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .map(move |span| {
                            (
                                path.clone(),
                                span["start_line"].as_u64().unwrap_or(0) as usize,
                                span["end_line"].as_u64().unwrap_or(0) as usize,
                            )
                        })
                })
                .collect();

            // R4-A: score units per call, retain call diversity, then expand.
            let mut selected: Vec<(usize, Seed, String)> = Vec::new();
            let mut per_call: BTreeMap<String, Vec<(usize, Seed)>> = BTreeMap::new();
            for (label, seeds) in &calls {
                for seed in seeds {
                    let method = projects
                        .iter()
                        .flat_map(|(_, project)| {
                            project.methods.iter().filter(|method| {
                                method.path == seed.path
                                    && ranges_overlap(
                                        seed.start_line,
                                        seed.end_line,
                                        method.line_start,
                                        method.line_end,
                                    )
                            })
                        })
                        .next();
                    let method_hits = method.map_or(0, |method| {
                        let tokens = query_tokens(&method.name);
                        query_terms
                            .iter()
                            .filter(|token| tokens.contains(token))
                            .count()
                    });
                    let path_hits = query_tokens(&seed.path)
                        .iter()
                        .filter(|token| query_terms.contains(token))
                        .count();
                    let score = method.map_or(0, |_| 5)
                        + method_hits * 3
                        + path_hits
                        + usize::from(seed.end_line > seed.start_line);
                    per_call
                        .entry(label.clone())
                        .or_default()
                        .push((score, seed.clone()));
                }
            }
            for (label, mut units) in per_call {
                units.sort_by_key(|(score, _)| std::cmp::Reverse(*score));
                for (score, seed) in units.into_iter().take(2) {
                    selected.push((score, seed, label.clone()));
                }
            }
            selected.sort_by_key(|(score, _, _)| std::cmp::Reverse(*score));
            let mut chosen: Vec<Seed> = Vec::new();
            let mut call_counts: BTreeMap<String, usize> = BTreeMap::new();
            let mut seen = BTreeSet::new();
            for (_, seed, label) in selected {
                if chosen.len() >= 5 {
                    break;
                }
                if call_counts.get(&label).copied().unwrap_or(0) >= 2 {
                    continue;
                }
                if seen.insert((seed.path.clone(), seed.start_line, seed.end_line)) {
                    call_counts.entry(label).or_insert(0);
                    chosen.push(seed);
                }
            }
            let mut expanded: Vec<CandidateItem> = Vec::new();
            for (_, project) in &projects {
                let project_seeds: Vec<Seed> = chosen
                    .iter()
                    .filter(|seed| project.files.iter().any(|file| file.path == seed.path))
                    .cloned()
                    .collect();
                if !project_seeds.is_empty() {
                    expanded.extend(expand(project, &catalog, &project_seeds, query, 50));
                }
            }
            expanded.sort_by_key(|item| std::cmp::Reverse(item.score));
            let mut expansion: Vec<CandidateItem> = Vec::new();
            let mut expansion_seen = BTreeSet::new();
            for item in expanded {
                if chosen.iter().any(|seed| {
                    seed.path == item.path
                        && seed.start_line == item.start_line
                        && seed.end_line == item.end_line
                }) {
                    continue;
                }
                if expansion_seen.insert((item.path.clone(), item.start_line, item.end_line)) {
                    expansion.push(item);
                }
            }
            let mut pack_a: Vec<CandidateItem> = Vec::new();
            for (index, seed) in chosen.iter().enumerate() {
                pack_a.push(CandidateItem {
                    path: seed.path.clone(),
                    start_line: seed.start_line,
                    end_line: seed.end_line,
                    score: 1000usize.saturating_sub(index),
                    reason: "r4a multi-call seed".to_owned(),
                });
            }
            for item in expansion.into_iter().take(5) {
                pack_a.push(item);
            }
            pack_a.sort_by_key(|item| std::cmp::Reverse(item.score));
            pack_a.truncate(10);

            // R4-B: file-cluster packing, max 3 files, merge adjacent spans.
            let mut by_file: BTreeMap<String, Vec<&CandidateItem>> = BTreeMap::new();
            for item in &pack_a {
                by_file.entry(item.path.clone()).or_default().push(item);
            }
            let mut files: Vec<(usize, String)> = by_file
                .iter()
                .map(|(path, items)| {
                    (
                        items.iter().map(|item| item.score).max().unwrap_or(0),
                        path.clone(),
                    )
                })
                .collect();
            files.sort_by_key(|(score, _)| std::cmp::Reverse(*score));
            let selected_files: BTreeSet<String> =
                files.into_iter().take(3).map(|(_, path)| path).collect();
            let mut pack_b: Vec<CandidateItem> = Vec::new();
            for (path, mut items) in by_file {
                if !selected_files.contains(&path) {
                    continue;
                }
                items.sort_by_key(|item| item.start_line);
                let mut merged: Vec<CandidateItem> = Vec::new();
                for item in items {
                    if let Some(last) = merged.last_mut()
                        && item.start_line <= last.end_line.saturating_add(2)
                    {
                        last.end_line = last.end_line.max(item.end_line);
                        last.score = last.score.max(item.score);
                        last.reason = "r4b file cluster".to_owned();
                        continue;
                    }
                    merged.push((*item).clone());
                }
                pack_b.extend(merged);
            }
            pack_b.sort_by_key(|item| std::cmp::Reverse(item.score));
            pack_b.truncate(10);

            rows.push(serde_json::json!({
                "id": id,
                "b0_pack_recall": recall(&b0_pack, &gold),
                "r4a_recall": recall(&pack_a, &gold),
                "r4b_recall": recall(&pack_b, &gold),
                "b0_files": file_count(&b0_pack),
                "r4a_files": file_count(&pack_a),
                "r4b_files": file_count(&pack_b),
                "b0_tokens": token_count(&b0_pack),
                "r4a_tokens": token_count(&pack_a),
                "r4b_tokens": token_count(&pack_b),
                "r4a_items": pack_a,
                "r4b_items": pack_b,
            }));
        }
        let mean = |key: &str, rows: &[serde_json::Value]| -> f64 {
            rows.iter().filter_map(|row| row[key].as_f64()).sum::<f64>() / rows.len() as f64
        };
        let summary = serde_json::json!({
            "status": "R4A_R4B_FOLLOWUP",
            "cases": rows.len(),
            "b0_recall": mean("b0_pack_recall", &rows),
            "r4a_recall": mean("r4a_recall", &rows),
            "r4b_recall": mean("r4b_recall", &rows),
            "b0_files": mean("b0_files", &rows),
            "r4a_files": mean("r4a_files", &rows),
            "r4b_files": mean("r4b_files", &rows),
            "b0_tokens": mean("b0_tokens", &rows),
            "r4a_tokens": mean("r4a_tokens", &rows),
            "r4b_tokens": mean("r4b_tokens", &rows),
            "heldout": "NOT_RUN",
            "performance": "NOT_MEASURED",
        });
        let out_dir =
            PathBuf::from(std::env::var("RAG_R4_OUT_DIR").unwrap_or_else(|_| "/tmp".to_owned()));
        std::fs::write(
            out_dir.join("candidate-r4a.json"),
            serde_json::to_string_pretty(&serde_json::json!({
                "schema_version": 1,
                "status": "R4A_MULTI_CALL_SEED_METHOD_RANKING",
                "method": "multi-call seeds + method-level ranking; 5 fixed + 5 expansion slots",
                "summary": summary,
                "cases": rows,
            }))
            .expect("json"),
        )
        .expect("write r4a");
        eprintln!(
            "{}",
            serde_json::to_string_pretty(&summary).expect("summary")
        );
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

#[cfg(test)]
mod h7_e1_tests {
    use super::*;
    use crate::extraction::csharp_facts::{ProjectIdentity, parse_project};
    use crate::extraction::sql_facts::SqlCatalog;
    use std::collections::{BTreeMap, BTreeSet};
    use std::hash::{Hash, Hasher};
    use std::path::PathBuf;

    fn projects(
        root: &std::path::Path,
        corpus: &serde_json::Value,
    ) -> (Vec<ProjectFacts>, SqlCatalog) {
        let read = |path: &str| std::fs::read_to_string(root.join(path)).expect("source");
        let mut by_project: BTreeMap<String, Vec<(String, String)>> = BTreeMap::new();
        for file in corpus["files"].as_array().expect("files") {
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
        let identity = |csproj: &str| ProjectIdentity::from_csproj(&root.join(csproj));
        let p3 = parse_project(
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
        let catalog = SqlCatalog::from_sources(&[
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
        (vec![p1, p2, p3], catalog)
    }

    fn status_kib(key: &str) -> usize {
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

    fn pss_kib() -> usize {
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
                        Some((key.to_owned(), value.trim().parse().ok()?))
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    fn io_delta(
        before: &BTreeMap<String, u64>,
        after: &BTreeMap<String, u64>,
    ) -> BTreeMap<String, u64> {
        after
            .iter()
            .map(|(key, value)| {
                (
                    key.clone(),
                    value.saturating_sub(*before.get(key).unwrap_or(&0)),
                )
            })
            .collect()
    }

    fn gold(case: &serde_json::Value) -> Vec<(String, usize, usize)> {
        let mut out = Vec::new();
        for reference in case["source_refs"].as_array().into_iter().flatten() {
            let path = reference["path"].as_str().unwrap_or_default().to_owned();
            for span in reference["spans"].as_array().into_iter().flatten() {
                if let (Some(start), Some(end)) =
                    (span["start_line"].as_u64(), span["end_line"].as_u64())
                {
                    out.push((path.clone(), start as usize, end as usize));
                }
            }
        }
        out
    }

    fn covered(items: &[CandidateItem], gold: &[(String, usize, usize)]) -> usize {
        gold.iter()
            .filter(|(path, start, end)| {
                items.iter().any(|item| {
                    item.path == *path
                        && ranges_overlap(item.start_line, item.end_line, *start, *end)
                })
            })
            .count()
    }

    #[test]
    #[ignore = "requires the external HRM2.0 checkout and frozen B0/dev receipts"]
    #[allow(clippy::too_many_lines)]
    fn h7_e1_typed_reference_closure() {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let read_json = |path: &str| -> serde_json::Value {
            serde_json::from_str(&std::fs::read_to_string(repo.join(path)).expect("json"))
                .expect("valid json")
        };
        let corpus = read_json("benchmarks/rag-impact/corpus-manifest.json");
        let b0 = read_json("benchmarks/rag-impact/results/b0-official.json");
        let dev = read_json("benchmarks/rag-impact/cases/dev.json");
        let root = PathBuf::from(corpus["source_root"].as_str().expect("root"));
        let phase_start = std::time::Instant::now();
        let io_phase_before = io_snapshot();
        let build_start = std::time::Instant::now();
        let (projects, catalog) = projects(&root, &corpus);
        let parse_build_ms = build_start.elapsed().as_secs_f64() * 1000.0;
        let pss_before_merge = pss_kib();
        let merge_start = std::time::Instant::now();
        let mut merged = projects[0].clone();
        for project in &projects[1..] {
            merged.files.extend(project.files.clone());
            merged.types.extend(project.types.clone());
            merged.methods.extend(project.methods.clone());
            merged.db_sets.extend(project.db_sets.clone());
            merged.fields.extend(project.fields.clone());
            merged.assignments.extend(project.assignments.clone());
            merged.invocations.extend(project.invocations.clone());
            merged.saves.extend(project.saves.clone());
        }
        let merge_ms = merge_start.elapsed().as_secs_f64() * 1000.0;
        let pss_after_merge = pss_kib();
        let io_phase = io_delta(&io_phase_before, &io_snapshot());
        let source_bytes: u64 = corpus["files"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|file| file["bytes"].as_u64())
            .sum();
        let mut rows = Vec::new();
        let mut totals = BTreeMap::new();
        let rss_after_build = status_kib("VmRSS:");
        let pss_after_build = pss_kib();
        for case in dev["cases"].as_array().expect("cases") {
            let Some(id) = case["id"].as_str() else {
                continue;
            };
            if !id.starts_with('D')
                || ["D13", "D14", "D15", "D16"]
                    .iter()
                    .any(|p| id.starts_with(p))
            {
                continue;
            }
            let b0_case = b0["quality"]["dev_real_cases"]
                .as_array()
                .expect("b0 cases")
                .iter()
                .find(|entry| entry["id"] == id)
                .expect("b0 case");
            let query = case["question"].as_str().unwrap_or_default();
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
            let evaluate = |closure: bool| {
                let (mut items, diagnostics) =
                    expand_with_closure_diagnostics(&merged, &catalog, &seeds, query, 500, closure);
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
                (items, diagnostics)
            };
            let io_before = io_snapshot();
            let off_start = std::time::Instant::now();
            let (off, off_diag) = evaluate(false);
            let off_ms = off_start.elapsed().as_secs_f64() * 1000.0;
            let on_start = std::time::Instant::now();
            let (on, on_diag) = evaluate(true);
            let on_ms = on_start.elapsed().as_secs_f64() * 1000.0;
            let io_after = io_snapshot();
            let rss_after_case = status_kib("VmRSS:");
            let pss_after_case = pss_kib();
            let gold = gold(case);
            let at = |items: &[CandidateItem], budget: usize| {
                covered(&items[..items.len().min(budget)], &gold)
            };
            let closure_items: Vec<&CandidateItem> = on
                .iter()
                .filter(|item| item.reason.starts_with("resolved|"))
                .collect();
            let rules: BTreeMap<String, usize> =
                closure_items.iter().fold(BTreeMap::new(), |mut map, item| {
                    if let Some(rule) = item.reason.split("rule=").nth(1) {
                        *map.entry(rule.to_owned()).or_insert(0) += 1;
                    }
                    map
                });
            let mut hasher = std::collections::hash_map::DefaultHasher::new();
            for item in &on {
                item.path.hash(&mut hasher);
                item.start_line.hash(&mut hasher);
                item.end_line.hash(&mut hasher);
                item.score.hash(&mut hasher);
                item.reason.hash(&mut hasher);
            }
            let rank = |items: &[CandidateItem], path: &str, start: usize, end: usize| {
                items.iter().position(|item| {
                    item.path == path && ranges_overlap(item.start_line, item.end_line, start, end)
                })
            };
            let ranks_on: Vec<Option<usize>> = gold
                .iter()
                .map(|(path, start, end)| rank(&on, path, *start, *end))
                .collect();
            let ranks_off: Vec<Option<usize>> = gold
                .iter()
                .map(|(path, start, end)| rank(&off, path, *start, *end))
                .collect();
            let row = serde_json::json!({
                "id": id,
                "gold": gold.len(),
                "coverage": {"10": at(&on, 10), "50": at(&on, 50), "100": at(&on, 100), "250": at(&on, 250), "500": at(&on, 500)},
                "refs_off": {"10": at(&off, 10), "50": at(&off, 50), "100": at(&off, 100), "250": at(&off, 250), "500": at(&off, 500)},
                "candidates": on.len(),
                "closure_items": closure_items.len(),
                "closure_rules": rules,
                "closure_hash": format!("{:016x}", hasher.finish()),
                "ranks_on": ranks_on,
                "ranks_off": ranks_off,
                "diagnostics_on": on_diag,
                "diagnostics_off": off_diag,
                "wall_on_ms": on_ms,
                "wall_off_ms": off_ms,
                "rss_kib_after_case": rss_after_case,
                "pss_kib_after_case": pss_after_case,
                "io_case": io_delta(&io_before, &io_after),
            });
            for (budget, value) in [
                ("10", at(&on, 10)),
                ("50", at(&on, 50)),
                ("100", at(&on, 100)),
                ("250", at(&on, 250)),
                ("500", at(&on, 500)),
            ] {
                *totals.entry(budget).or_insert(0) += value;
            }
            rows.push(row);
        }
        let phase = serde_json::json!({
            "parse_build_ms": parse_build_ms,
            "project_scope_merge_ms": merge_ms,
            "pss_kib_before_merge": pss_before_merge,
            "pss_kib_after_merge": pss_after_merge,
            "total_phase_ms": phase_start.elapsed().as_secs_f64() * 1000.0,
            "rss_kib_after_build": rss_after_build,
            "pss_kib_after_build": pss_after_build,
            "rss_kib_end": status_kib("VmRSS:"),
            "rss_hwm_kib_end": status_kib("VmHWM:"),
            "pss_kib_end": pss_kib(),
            "io_build": io_phase,
            "source_bytes_from_manifest": source_bytes,
        });
        println!("{}", serde_json::to_string_pretty(&serde_json::json!({"status":"H7_E1_TYPED_REFERENCE_CLOSURE_FORENSICS","cases":rows,"totals":totals,"phase":phase})).expect("json"));
    }
}

#[cfg(test)]
mod h7_e1_external {
    use super::*;
    use crate::extraction::csharp_facts::parse_project;
    use crate::extraction::sql_facts::SqlCatalog;
    use std::collections::BTreeMap;
    use std::path::PathBuf;

    fn projects(root: &std::path::Path, corpus: &serde_json::Value) -> (ProjectFacts, SqlCatalog) {
        let read = |path: &str| std::fs::read_to_string(root.join(path)).expect("source");
        let mut by_project: BTreeMap<String, Vec<(String, String)>> = BTreeMap::new();
        for file in corpus["files"].as_array().expect("files") {
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
        let p3 = parse_project(
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
        let catalog = SqlCatalog::from_sources(&[
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
        (merged, catalog)
    }

    fn covered(items: &[CandidateItem], gold: &[(String, usize, usize)]) -> usize {
        gold.iter()
            .filter(|(path, start, end)| {
                items.iter().any(|item| {
                    item.path == *path
                        && ranges_overlap(item.start_line, item.end_line, *start, *end)
                })
            })
            .count()
    }

    #[test]
    #[ignore = "set H7_CASES and H7_ANCHORS to frozen JSON files"]
    fn h7_e1_external() {
        let Ok(cases_path) = std::env::var("H7_CASES") else {
            return;
        };
        let Ok(anchors_path) = std::env::var("H7_ANCHORS") else {
            return;
        };
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let corpus: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repo.join("benchmarks/rag-impact/corpus-manifest.json"))
                .expect("corpus"),
        )
        .expect("json");
        let cases: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(cases_path).expect("cases"))
                .expect("json");
        let anchors: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(anchors_path).expect("anchors"))
                .expect("json");
        let root = PathBuf::from(corpus["source_root"].as_str().expect("root"));
        let (merged, catalog) = projects(&root, &corpus);
        let by_id: BTreeMap<String, &serde_json::Value> = anchors["cases"]
            .as_array()
            .expect("anchor cases")
            .iter()
            .filter_map(|case| Some((case["id"].as_str()?.to_owned(), case)))
            .collect();
        let mut rows = Vec::new();
        let mut totals = BTreeMap::new();
        for case in cases["cases"].as_array().expect("cases") {
            let Some(id) = case["id"].as_str() else {
                continue;
            };
            let Some(anchor) = by_id.get(id) else {
                continue;
            };
            let seeds: Vec<Seed> = anchor["seeds"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|unit| {
                    Some(Seed {
                        path: unit["path"].as_str()?.to_owned(),
                        start_line: unit["start_line"].as_u64()? as usize,
                        end_line: unit["end_line"].as_u64()? as usize,
                    })
                })
                .collect();
            let query = case["question"].as_str().unwrap_or_default();
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
            let evaluate = |closure: bool| {
                let mut items = expand_with_closure(&merged, &catalog, &seeds, query, 500, closure);
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
                items
            };
            let off = evaluate(false);
            let on = evaluate(true);
            let at = |items: &[CandidateItem], budget: usize| {
                covered(&items[..items.len().min(budget)], &gold)
            };
            let row = serde_json::json!({"id":id,"gold":gold.len(),"coverage":{"10":at(&on,10),"50":at(&on,50),"100":at(&on,100),"250":at(&on,250),"500":at(&on,500)},"refs_off":{"10":at(&off,10),"50":at(&off,50),"100":at(&off,100),"250":at(&off,250),"500":at(&off,500)},"candidates":on.len(),"closure_items":on.iter().filter(|item| item.reason.starts_with("resolved|")).count()});
            for (budget, value) in [
                ("10", at(&on, 10)),
                ("50", at(&on, 50)),
                ("100", at(&on, 100)),
                ("250", at(&on, 250)),
                ("500", at(&on, 500)),
            ] {
                *totals.entry(budget).or_insert(0) += value;
            }
            rows.push(row);
        }
        println!(
            "{}",
            serde_json::to_string_pretty(
                &serde_json::json!({"status":"H7_E1_EXTERNAL","cases":rows,"totals":totals})
            )
            .expect("json")
        );
    }
}
