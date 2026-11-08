//! Bounded SQL/EF fact extraction for the RAG relation adapter.
//!
//! This module parses selected `.sql` definitions and the SQL strings passed to
//! EF `ExecuteSqlCommand`/`SqlQuery` calls. It records source-visible table
//! reads/writes, selected-variant authority and dynamic/candidate boundaries.
//! It never claims live database authority or executes SQL.

#![allow(dead_code)]

use std::collections::BTreeMap;

use crate::extraction::csharp_facts::{InvocationFact, ProjectFacts};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SqlObjectKind {
    Procedure,
    View,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TableAccess {
    pub table: String,
    pub operation: String,
    pub line: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SqlDefinition {
    pub name: String,
    pub kind: SqlObjectKind,
    pub path: String,
    pub line_start: usize,
    pub line_end: usize,
    pub selected: bool,
    pub authority: String,
    pub writes: Vec<TableAccess>,
    pub reads: Vec<TableAccess>,
    pub columns: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct SqlCatalog {
    definitions: Vec<SqlDefinition>,
}

impl SqlCatalog {
    #[must_use]
    pub fn from_sources(sources: &[(String, String, bool, String)]) -> Self {
        let mut definitions = Vec::new();
        for (path, source, selected, authority) in sources {
            definitions.extend(parse_sql_file(path, source, *selected, authority));
        }
        Self { definitions }
    }

    #[must_use]
    pub fn definitions(&self) -> &[SqlDefinition] {
        &self.definitions
    }

    #[must_use]
    pub fn selected(&self, name: &str) -> Option<&SqlDefinition> {
        self.definitions
            .iter()
            .find(|definition| definition.name.eq_ignore_ascii_case(name) && definition.selected)
    }

    #[must_use]
    pub fn variants(&self, name: &str) -> Vec<&SqlDefinition> {
        self.definitions
            .iter()
            .filter(|definition| definition.name.eq_ignore_ascii_case(name))
            .collect()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BindingStatus {
    Resolved,
    Candidate,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SqlBinding {
    pub project: String,
    pub path: String,
    pub namespace: String,
    pub type_name: String,
    pub method: String,
    pub line: usize,
    pub called: String,
    pub object_name: Option<String>,
    pub target_path: Option<String>,
    pub writes: Vec<TableAccess>,
    pub reads: Vec<TableAccess>,
    pub dynamic: bool,
    pub status: BindingStatus,
    pub reason: Option<String>,
}

#[must_use]
pub fn bind_project(project: &ProjectFacts, catalog: &SqlCatalog) -> Vec<SqlBinding> {
    project
        .invocations
        .iter()
        .filter(|invocation| {
            matches!(
                invocation.called.split('<').next().unwrap_or_default(),
                "ExecuteSqlCommand" | "SqlQuery"
            )
        })
        .map(|invocation| bind_invocation(invocation, catalog))
        .collect()
}

fn bind_invocation(invocation: &InvocationFact, catalog: &SqlCatalog) -> SqlBinding {
    let (sql, dynamic) = extract_literals(&invocation.arguments);
    if sql.trim().is_empty() {
        return base_binding(
            invocation,
            None,
            Vec::new(),
            Vec::new(),
            true,
            BindingStatus::Candidate,
            Some("no SQL string literal".to_owned()),
        );
    }
    let normalized = sql.to_lowercase();
    if let Some(name) = procedure_name(&normalized) {
        let variants = catalog.variants(&name);
        let Some(selected) = catalog.selected(&name) else {
            let reason = if variants.is_empty() {
                format!("SQL definition not found for {name}")
            } else {
                format!("no selected variant for {name}")
            };
            return base_binding(
                invocation,
                Some(name),
                Vec::new(),
                Vec::new(),
                dynamic,
                BindingStatus::Candidate,
                Some(reason),
            );
        };
        return SqlBinding {
            project: invocation.project.clone(),
            path: invocation.path.clone(),
            namespace: invocation.namespace.clone(),
            type_name: invocation.type_name.clone(),
            method: invocation.method.clone(),
            line: invocation.line,
            called: invocation.called.clone(),
            object_name: Some(selected.name.clone()),
            target_path: Some(selected.path.clone()),
            writes: selected.writes.clone(),
            reads: selected.reads.clone(),
            dynamic,
            status: BindingStatus::Resolved,
            reason: Some(format!(
                "selected variant {} ({})",
                selected.name, selected.authority
            )),
        };
    }

    let (writes, reads, table_dynamic) = parse_table_access(&sql, invocation.line);
    let candidate = dynamic || table_dynamic || (writes.is_empty() && reads.is_empty());
    let reason = if table_dynamic {
        Some("dynamic table target".to_owned())
    } else if dynamic {
        Some("dynamic SQL values/predicate; table target is source-visible".to_owned())
    } else if writes.is_empty() && reads.is_empty() {
        Some("no source-visible table target".to_owned())
    } else {
        Some("literal SQL table target".to_owned())
    };
    base_binding(
        invocation,
        None,
        writes,
        reads,
        dynamic || table_dynamic,
        if candidate {
            BindingStatus::Candidate
        } else {
            BindingStatus::Resolved
        },
        reason,
    )
}

fn base_binding(
    invocation: &InvocationFact,
    object_name: Option<String>,
    writes: Vec<TableAccess>,
    reads: Vec<TableAccess>,
    dynamic: bool,
    status: BindingStatus,
    reason: Option<String>,
) -> SqlBinding {
    SqlBinding {
        project: invocation.project.clone(),
        path: invocation.path.clone(),
        namespace: invocation.namespace.clone(),
        type_name: invocation.type_name.clone(),
        method: invocation.method.clone(),
        line: invocation.line,
        called: invocation.called.clone(),
        object_name,
        target_path: None,
        writes,
        reads,
        dynamic,
        status,
        reason,
    }
}

#[must_use]
pub fn parse_sql_file(
    path: &str,
    source: &str,
    selected: bool,
    authority: &str,
) -> Vec<SqlDefinition> {
    let mut definitions = Vec::new();
    let mut current: Option<(String, SqlObjectKind, usize)> = None;
    for (index, line) in source.lines().enumerate() {
        let line_number = index + 1;
        if let Some((name, kind)) = create_object(line) {
            if let Some((old_name, old_kind, old_start)) = current.take() {
                definitions.push(build_definition(
                    &old_name,
                    old_kind,
                    path,
                    old_start,
                    line_number.saturating_sub(1),
                    selected,
                    authority,
                    source,
                ));
            }
            current = Some((name, kind, line_number));
        }
    }
    if let Some((name, kind, start)) = current {
        definitions.push(build_definition(
            &name,
            kind,
            path,
            start,
            source.lines().count(),
            selected,
            authority,
            source,
        ));
    }
    definitions
}

#[allow(clippy::too_many_arguments)]
fn build_definition(
    name: &str,
    kind: SqlObjectKind,
    path: &str,
    line_start: usize,
    line_end: usize,
    selected: bool,
    authority: &str,
    source: &str,
) -> SqlDefinition {
    let (writes, reads, _dynamic) = parse_table_access(source, 1);
    let columns = match kind {
        SqlObjectKind::View => view_columns(source),
        SqlObjectKind::Procedure => Vec::new(),
    };
    SqlDefinition {
        name: name.to_owned(),
        kind,
        path: path.to_owned(),
        line_start,
        line_end,
        selected,
        authority: authority.to_owned(),
        writes,
        reads,
        columns,
    }
}

fn create_object(line: &str) -> Option<(String, SqlObjectKind)> {
    let trimmed = line.trim().trim_start_matches('\u{feff}');
    let normalized = trimmed.to_lowercase();
    let (kind, prefix_len) = if normalized.starts_with("create procedure") {
        (SqlObjectKind::Procedure, "create procedure".len())
    } else if normalized.starts_with("alter procedure") {
        (SqlObjectKind::Procedure, "alter procedure".len())
    } else if normalized.starts_with("create view") {
        (SqlObjectKind::View, "create view".len())
    } else if normalized.starts_with("alter view") {
        (SqlObjectKind::View, "alter view".len())
    } else {
        return None;
    };
    let rest = &trimmed[prefix_len..];
    let name = rest
        .split_whitespace()
        .next()?
        .trim_matches(|c| c == '[' || c == ']');
    let name = name
        .rsplit('.')
        .next()
        .unwrap_or(name)
        .trim_matches(['[', ']']);
    (!name.is_empty()).then(|| (name.to_owned(), kind))
}

fn view_columns(source: &str) -> Vec<String> {
    let mut columns = Vec::new();
    let lower = source.to_lowercase();
    let Some(select_at) = lower.find("select") else {
        return columns;
    };
    let Some(from_at) = lower[select_at..].find("from ").map(|i| select_at + i) else {
        return columns;
    };
    for raw in source[select_at + "select".len()..from_at].split(',') {
        let part = raw.trim();
        if part.is_empty() || part == "*" {
            continue;
        }
        let name = part.split_whitespace().last().unwrap_or(part);
        let name = name
            .rsplit('.')
            .next()
            .unwrap_or(name)
            .trim_matches(['[', ']']);
        if !name.is_empty() && !name.eq_ignore_ascii_case("as") {
            columns.push(name.to_owned());
        }
    }
    columns
}

fn parse_table_access(sql: &str, line_offset: usize) -> (Vec<TableAccess>, Vec<TableAccess>, bool) {
    let mut writes = Vec::new();
    let mut reads = Vec::new();
    let mut dynamic_table = false;
    let lower = sql.to_lowercase();
    for (operation, keyword) in [
        ("update", "update "),
        ("insert", "insert into "),
        ("delete", "delete from "),
    ] {
        let mut rest = lower.as_str();
        while let Some(position) = rest.find(keyword) {
            let after = &rest[position + keyword.len()..];
            let token = after.split_whitespace().next().unwrap_or_default();
            if token.is_empty() {
                dynamic_table = true;
                break;
            }
            let table = clean_table(token);
            if is_sql_keyword(&table) {
                dynamic_table = true;
                break;
            }
            if !table.is_empty() {
                writes.push(TableAccess {
                    table,
                    operation: operation.to_owned(),
                    line: line_offset,
                });
            }
            rest = &after[token.len()..];
        }
    }
    for keyword in ["from ", "join "] {
        let mut rest = lower.as_str();
        while let Some(position) = rest.find(keyword) {
            let after = &rest[position + keyword.len()..];
            let token = after.split_whitespace().next().unwrap_or_default();
            if token.is_empty() {
                break;
            }
            let table = clean_table(token);
            if !table.is_empty() && !is_sql_keyword(&table) {
                reads.push(TableAccess {
                    table,
                    operation: "read".to_owned(),
                    line: line_offset,
                });
            }
            rest = &after[token.len()..];
        }
    }
    (writes, reads, dynamic_table)
}

fn is_sql_keyword(token: &str) -> bool {
    matches!(
        token.to_lowercase().as_str(),
        "set" | "where" | "select" | "from" | "join" | "values" | "into" | "as" | "on"
    )
}

fn clean_table(token: &str) -> String {
    let token = token
        .trim_matches(|c: char| c == '(' || c == ')' || c == ',' || c == ';' || c == '"')
        .trim();
    let last = token.rsplit('.').next().unwrap_or(token);
    last.trim_matches(['[', ']']).to_owned()
}

fn procedure_name(normalized: &str) -> Option<String> {
    let mut rest = normalized;
    while let Some(position) = rest.find("execute ") {
        let after = &rest[position + "execute ".len()..];
        let token = after.split_whitespace().next().unwrap_or_default();
        let name = clean_table(token);
        if !name.is_empty() && !name.contains('+') && !name.contains('"') {
            return Some(name);
        }
        rest = &after[token.len()..];
    }
    None
}

/// Extract literal SQL fragments and whether non-literal values are concatenated.
#[must_use]
pub fn extract_literals(text: &str) -> (String, bool) {
    let mut literal = String::new();
    let mut outside = String::new();
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch != '"' {
            outside.push(ch);
            continue;
        }
        let mut closed = false;
        while let Some(inner) = chars.next() {
            if inner == '"' {
                if chars.peek() == Some(&'"') {
                    chars.next();
                    literal.push('"');
                } else {
                    closed = true;
                    break;
                }
            } else {
                literal.push(inner);
            }
        }
        if !closed {
            outside.push('"');
        }
    }
    let dynamic = outside.chars().any(|ch| ch.is_alphanumeric() || ch == '_');
    (literal, dynamic)
}

#[must_use]
pub fn table_writes(bindings: &[SqlBinding]) -> BTreeMap<String, Vec<String>> {
    let mut result: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for binding in bindings {
        for write in &binding.writes {
            result
                .entry(write.table.clone())
                .or_default()
                .push(binding.method.clone());
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extraction::csharp_facts::{ProjectIdentity, parse_project_with_references};

    fn fixture(path: &str) -> String {
        let root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../benchmarks/rag-impact/cases/synthetic");
        std::fs::read_to_string(root.join(path)).expect("fixture")
    }

    #[test]
    fn selected_proc_and_view_are_parsed_without_excluded_variant_authority() {
        let catalog = SqlCatalog::from_sources(&[
            (
                "Store.sql".to_owned(),
                fixture("R3/Store.sql"),
                true,
                "selected_source_variant".to_owned(),
            ),
            (
                "Store_old.sql".to_owned(),
                fixture("R3/Store_old.sql"),
                false,
                "excluded_variant".to_owned(),
            ),
            (
                "Views.sql".to_owned(),
                fixture("R3/Views.sql"),
                true,
                "selected_source_variant".to_owned(),
            ),
        ]);
        let selected = catalog
            .selected("sp_UpdateOrgTotalEmployee")
            .expect("selected proc");
        assert_eq!(selected.path, "Store.sql");
        assert!(
            selected
                .writes
                .iter()
                .any(|write| write.table.eq_ignore_ascii_case("Organizations"))
        );
        assert!(
            selected
                .reads
                .iter()
                .any(|read| read.table.eq_ignore_ascii_case("Employees"))
        );
        let old = catalog
            .variants("sp_UpdateOrgTotalEmployee_old")
            .into_iter()
            .find(|definition| definition.path == "Store_old.sql")
            .expect("old variant");
        assert!(!old.selected);
        let view = catalog
            .selected("EmployeeBaseViews")
            .expect("selected view");
        assert!(view.columns.iter().any(|column| column == "FullName"));
        assert!(view.writes.is_empty());
        assert!(
            view.reads
                .iter()
                .any(|read| read.table.eq_ignore_ascii_case("Employees"))
        );
    }

    #[test]
    fn csharp_sql_calls_bind_to_selected_catalog_and_keep_dynamic_targets_candidate() {
        let catalog = SqlCatalog::from_sources(&[
            (
                "Store.sql".to_owned(),
                fixture("R3/Store.sql"),
                true,
                "selected_source_variant".to_owned(),
            ),
            (
                "Store_old.sql".to_owned(),
                fixture("R3/Store_old.sql"),
                false,
                "excluded_variant".to_owned(),
            ),
            (
                "Views.sql".to_owned(),
                fixture("R3/Views.sql"),
                true,
                "selected_source_variant".to_owned(),
            ),
        ]);
        let files = vec![("Commands.cs".to_owned(), fixture("R3/Commands.cs"))];
        let project = parse_project_with_references(
            &ProjectIdentity {
                name: "R3".to_owned(),
                assembly: "R3".to_owned(),
                root_namespace: Some("Synthetic.R3".to_owned()),
            },
            &files,
            &[],
        );
        let bindings = bind_project(&project, &catalog);
        let proc = bindings
            .iter()
            .find(|binding| binding.method == "RestoreEmployeeById")
            .expect("proc binding");
        assert_eq!(proc.status, BindingStatus::Resolved);
        assert_eq!(proc.target_path.as_deref(), Some("Store.sql"));
        assert!(
            proc.writes
                .iter()
                .any(|write| write.table.eq_ignore_ascii_case("Organizations"))
        );

        let raw = bindings
            .iter()
            .find(|binding| binding.method == "DeleteEmployeeByIds")
            .expect("raw update");
        assert!(
            raw.writes
                .iter()
                .any(|write| write.table.eq_ignore_ascii_case("Employees"))
        );
        assert!(raw.dynamic);

        let view = bindings
            .iter()
            .find(|binding| binding.method == "ReadView")
            .expect("view read");
        assert!(
            view.reads
                .iter()
                .any(|read| read.table.eq_ignore_ascii_case("EmployeeBaseViews"))
        );
        assert!(view.writes.is_empty());

        let dynamic = bindings
            .iter()
            .find(|binding| binding.method == "DynamicTable")
            .expect("dynamic table");
        assert_eq!(dynamic.status, BindingStatus::Candidate);
        assert!(dynamic.dynamic);
    }

    #[test]
    #[ignore = "requires the external HRM2.0 pilot checkout for the real-case R3 gate"]
    #[allow(clippy::too_many_lines)]
    fn real_hrm2_sql_gate() {
        let manifest_path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../benchmarks/rag-impact/corpus-manifest.json");
        let manifest: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&manifest_path).expect("manifest"))
                .expect("valid manifest");
        let root = std::path::PathBuf::from(manifest["source_root"].as_str().expect("root"));
        let read = |path: &str| std::fs::read_to_string(root.join(path)).expect("source");
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
        let selected = catalog
            .selected("sp_UpdateOrgTotalEmployee")
            .expect("selected proc");
        assert!(
            selected
                .writes
                .iter()
                .any(|write| write.table.eq_ignore_ascii_case("Organizations"))
        );
        let view = catalog
            .selected("EmployeeBaseViews")
            .expect("selected view");
        assert!(view.columns.iter().any(|column| column == "FullName"));
        assert!(view.writes.is_empty());

        let p3_identity = crate::extraction::csharp_facts::ProjectIdentity::from_csproj(
            &root.join("DBContexts/HRIS/DBContext.Core/DBContext.Core.csproj"),
        );
        let p3 = crate::extraction::csharp_facts::parse_project(
            &p3_identity,
            &[(
                "DBContexts/HRIS/DBContext.Core/Entities/CoreDBContext.cs".to_owned(),
                read("DBContexts/HRIS/DBContext.Core/Entities/CoreDBContext.cs"),
            )],
        );
        let p2_identity = crate::extraction::csharp_facts::ProjectIdentity::from_csproj(
            &root.join("Services/HRIS/Service.Core/Service.Core.csproj"),
        );
        let p2 = parse_project_with_references(
            &p2_identity,
            &[
                (
                    "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs".to_owned(),
                    read("Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs"),
                ),
                (
                    "Services/HRIS/Service.Core/Executes/Base/CoreService.cs".to_owned(),
                    read("Services/HRIS/Service.Core/Executes/Base/CoreService.cs"),
                ),
                (
                    "Services/HRIS/Service.Core/Executes/General/Organizations/OrganizationCommand.cs".to_owned(),
                    read("Services/HRIS/Service.Core/Executes/General/Organizations/OrganizationCommand.cs"),
                ),
            ],
            std::slice::from_ref(&p3),
        );
        let bindings = bind_project(&p2, &catalog);
        let proc = bindings
            .iter()
            .find(|binding| binding.method == "UpdateOrgTotalEmployee")
            .expect("proc binding");
        assert_eq!(proc.status, BindingStatus::Resolved);
        assert_eq!(
            proc.target_path.as_deref(),
            Some("DB/Store/sp_UpdateOrgTotalEmployee.sql")
        );
        assert!(
            proc.writes
                .iter()
                .any(|write| write.table.eq_ignore_ascii_case("Organizations"))
        );
        assert!(
            bindings
                .iter()
                .filter(|binding| binding.method == "DeleteEmployeeByIds")
                .any(|binding| {
                    binding.dynamic
                        && binding
                            .writes
                            .iter()
                            .any(|write| write.table.eq_ignore_ascii_case("Employees"))
                })
        );
        assert!(
            catalog
                .variants("sp_UpdateOrgTotalEmployee_old")
                .iter()
                .all(|definition| !definition.selected)
        );
    }

    #[test]
    fn literal_extraction_marks_concatenated_values_dynamic() {
        assert_eq!(
            extract_literals("(\"execute sp_X\")"),
            ("execute sp_X".to_owned(), false)
        );
        let (literal, dynamic) =
            extract_literals("(\"update Employees set Status = -1\" + ids + \")\")");
        assert_eq!(literal, "update Employees set Status = -1)");
        assert!(dynamic);
    }
}
