// R2 side parser is not yet wired into the R3 relation/evidence service.
// Remove this allow when the consumer lands.
#![allow(dead_code)]

//! Bounded C# fact extraction for the RAG relation adapter.
//!
//! This is a side parser: it does not change ordinary `.cs` indexing or chunking.
//! It extracts project-scoped type/method identity, assignments, invocations,
//! branch guards and `SaveChanges` boundaries. Unresolved or dynamic targets are
//! kept as candidates with a reason; they are never promoted to resolved edges.

use std::collections::{BTreeSet, HashMap};
use std::path::Path;

use tree_sitter::{Node, Parser};

/// Minimal project identity needed to keep same-FQN types in different
/// assemblies separate.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ProjectIdentity {
    pub name: String,
    pub assembly: String,
    pub root_namespace: Option<String>,
}

impl ProjectIdentity {
    #[must_use]
    pub fn from_csproj(path: &Path) -> Self {
        let text = std::fs::read_to_string(path).unwrap_or_default();
        let assembly = xml_element(&text, "AssemblyName")
            .or_else(|| {
                path.file_stem()
                    .and_then(|stem| stem.to_str())
                    .map(str::to_owned)
            })
            .unwrap_or_else(|| "unknown".to_owned());
        let root_namespace = xml_element(&text, "RootNamespace");
        let name = assembly.clone();
        Self {
            name,
            assembly,
            root_namespace,
        }
    }
}

fn xml_element(text: &str, tag: &str) -> Option<String> {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    let start = text.find(&open)? + open.len();
    let end = text[start..].find(&close)? + start;
    let value = text[start..end].trim();
    (!value.is_empty()).then(|| value.to_owned())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypeFact {
    pub path: String,
    pub namespace: String,
    pub name: String,
    pub kind: String,
    pub partial: bool,
    pub line_start: usize,
    pub line_end: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParameterFact {
    pub name: String,
    pub type_name: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MethodFact {
    pub project: String,
    pub path: String,
    pub namespace: String,
    pub type_name: String,
    pub name: String,
    pub signature: String,
    pub line_start: usize,
    pub line_end: usize,
    pub parameters: Vec<ParameterFact>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DbSetFact {
    pub path: String,
    pub context_type: String,
    pub property: String,
    pub element_type: String,
    pub line_start: usize,
    pub line_end: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AssignmentFact {
    pub namespace: String,
    pub type_name: String,
    pub method: String,
    pub receiver: String,
    pub receiver_type: Option<String>,
    pub field: String,
    pub value: String,
    pub guard: Option<String>,
    pub line: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SaveFact {
    pub namespace: String,
    pub type_name: String,
    pub method: String,
    pub receiver: String,
    pub context_type: Option<String>,
    pub guard: Option<String>,
    pub line: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MethodTarget {
    pub project: String,
    pub namespace: String,
    pub type_name: String,
    pub method: String,
    pub path: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResolutionStatus {
    Resolved,
    Candidate,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InvocationFact {
    pub project: String,
    pub path: String,
    pub namespace: String,
    pub type_name: String,
    pub method: String,
    pub receiver: Option<String>,
    pub receiver_type: Option<String>,
    pub called: String,
    pub arguments: String,
    pub guard: Option<String>,
    pub line: usize,
    pub status: ResolutionStatus,
    pub target: Option<MethodTarget>,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FieldFact {
    pub namespace: String,
    pub type_name: String,
    pub name: String,
    pub field_type: String,
}

#[derive(Debug, Clone, Default)]
pub struct ProjectScope {
    pub fields_by_type: HashMap<(String, String), HashMap<String, String>>,
    pub db_sets: Vec<DbSetFact>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileFacts {
    pub project: String,
    pub path: String,
    pub namespace: Option<String>,
    pub types: Vec<TypeFact>,
    pub methods: Vec<MethodFact>,
    pub db_sets: Vec<DbSetFact>,
    pub fields: Vec<FieldFact>,
    pub assignments: Vec<AssignmentFact>,
    pub invocations: Vec<InvocationFact>,
    pub saves: Vec<SaveFact>,
    pub parse_error: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectFacts {
    pub project: ProjectIdentity,
    pub files: Vec<FileFacts>,
    pub types: Vec<TypeFact>,
    pub methods: Vec<MethodFact>,
    pub db_sets: Vec<DbSetFact>,
    pub fields: Vec<FieldFact>,
    pub assignments: Vec<AssignmentFact>,
    pub invocations: Vec<InvocationFact>,
    pub saves: Vec<SaveFact>,
}

#[derive(Debug, Clone, Default)]
pub struct ProjectDeclarations {
    pub types: Vec<TypeFact>,
    pub fields: Vec<FieldFact>,
    pub db_sets: Vec<DbSetFact>,
}

impl ProjectDeclarations {
    pub fn merge(&mut self, other: Self) {
        for ty in other.types {
            if !self.types.contains(&ty) {
                self.types.push(ty);
            }
        }
        for field in other.fields {
            if !self.fields.contains(&field) {
                self.fields.push(field);
            }
        }
        for db_set in other.db_sets {
            if !self.db_sets.iter().any(|existing| {
                existing.path == db_set.path
                    && existing.context_type == db_set.context_type
                    && existing.property == db_set.property
            }) {
                self.db_sets.push(db_set);
            }
        }
    }
}

#[must_use]
pub fn build_declaration_index(
    project: &ProjectIdentity,
    files: &[(String, String)],
) -> ProjectDeclarations {
    let mut declarations = ProjectDeclarations::default();
    for (path, source) in files {
        let facts = parse_file(project, path, source);
        declarations.types.extend(facts.types);
        declarations.fields.extend(facts.fields);
        declarations.db_sets.extend(facts.db_sets);
    }
    declarations.types.sort_by(|left, right| {
        left.path
            .cmp(&right.path)
            .then_with(|| left.line_start.cmp(&right.line_start))
            .then_with(|| left.name.cmp(&right.name))
    });
    declarations.types.dedup();
    declarations.fields.sort_by(|left, right| {
        left.namespace
            .cmp(&right.namespace)
            .then_with(|| left.type_name.cmp(&right.type_name))
            .then_with(|| left.name.cmp(&right.name))
    });
    declarations.fields.dedup();
    declarations.db_sets.sort_by(|left, right| {
        left.path
            .cmp(&right.path)
            .then_with(|| left.context_type.cmp(&right.context_type))
            .then_with(|| left.property.cmp(&right.property))
    });
    declarations.db_sets.dedup_by(|left, right| {
        left.path == right.path
            && left.context_type == right.context_type
            && left.property == right.property
    });
    declarations
}

/// Parse full facts only for anchor files, using compact declarations for
/// project-scoped receiver/declaration resolution. All declarations are kept;
/// methods/assignments/invocations/saves are anchor-only.
#[must_use]
pub fn parse_anchor_facts(
    project: &ProjectIdentity,
    files: &[(String, String)],
    anchor_paths: &std::collections::BTreeSet<String>,
    declarations: &ProjectDeclarations,
) -> ProjectFacts {
    let mut fields_by_type: HashMap<(String, String), HashMap<String, String>> = HashMap::new();
    for field in &declarations.fields {
        fields_by_type
            .entry((field.namespace.clone(), field.type_name.clone()))
            .or_default()
            .insert(field.name.clone(), field.field_type.clone());
    }
    let scope = ProjectScope {
        fields_by_type,
        db_sets: declarations.db_sets.clone(),
    };
    let mut parsed = Vec::new();
    for (path, source) in files {
        if !anchor_paths.contains(path) {
            continue;
        }
        let mut facts = parse_file_with_scope(project, path, source, &scope);
        facts.db_sets.clear();
        parsed.push(facts);
    }
    let mut project_facts = ProjectFacts {
        project: project.clone(),
        files: parsed,
        types: Vec::new(),
        methods: Vec::new(),
        db_sets: Vec::new(),
        fields: Vec::new(),
        assignments: Vec::new(),
        invocations: Vec::new(),
        saves: Vec::new(),
    };
    // Resolve while file-local methods/invocations are still present; then keep
    // only file-local assignments in FileFacts and the resolved top-level graph.
    resolve_project(&mut project_facts);
    let mut files = Vec::new();
    for mut file in project_facts.files {
        project_facts.methods.append(&mut file.methods);
        project_facts.saves.append(&mut file.saves);
        file.saves.clear();
        file.methods.clear();
        file.invocations.clear();
        files.push(file);
    }
    project_facts.files = files;
    project_facts
}

impl ProjectFacts {
    pub fn resolved_invocations(&self) -> impl Iterator<Item = &InvocationFact> {
        self.invocations
            .iter()
            .filter(|invocation| invocation.status == ResolutionStatus::Resolved)
    }

    pub fn candidate_invocations(&self) -> impl Iterator<Item = &InvocationFact> {
        self.invocations
            .iter()
            .filter(|invocation| invocation.status == ResolutionStatus::Candidate)
    }
}

#[derive(Debug, Default)]
struct Context {
    namespace: Vec<String>,
    type_name: Option<String>,
    type_partial: bool,
    method: Option<String>,
    guards: Vec<String>,
    locals: HashMap<String, String>,
    params: HashMap<String, String>,
    fields: HashMap<String, String>,
    shared_fields: HashMap<(String, String), HashMap<String, String>>,
}

impl Context {
    fn namespace(&self) -> String {
        self.namespace.join(".")
    }

    fn type_name(&self) -> String {
        self.type_name.clone().unwrap_or_default()
    }

    fn method_name(&self) -> String {
        self.method.clone().unwrap_or_default()
    }

    fn guard(&self) -> Option<String> {
        (!self.guards.is_empty()).then(|| self.guards.join(" && "))
    }
}

#[must_use]
pub fn parse_file(project: &ProjectIdentity, path: &str, source: &str) -> FileFacts {
    parse_file_with_scope(project, path, source, &ProjectScope::default())
}

fn parse_file_with_scope(
    project: &ProjectIdentity,
    path: &str,
    source: &str,
    scope: &ProjectScope,
) -> FileFacts {
    let mut parser = Parser::new();
    let mut facts = FileFacts {
        project: project.name.clone(),
        path: path.to_owned(),
        namespace: None,
        types: Vec::new(),
        methods: Vec::new(),
        db_sets: scope.db_sets.clone(),
        fields: Vec::new(),
        assignments: Vec::new(),
        invocations: Vec::new(),
        saves: Vec::new(),
        parse_error: false,
    };
    if parser
        .set_language(&tree_sitter_c_sharp::LANGUAGE.into())
        .is_err()
    {
        facts.parse_error = true;
        return facts;
    }
    let Some(tree) = parser.parse(source, None) else {
        facts.parse_error = true;
        return facts;
    };
    facts.parse_error = tree.root_node().has_error();
    let mut context = Context {
        shared_fields: scope.fields_by_type.clone(),
        ..Context::default()
    };
    walk(tree.root_node(), source, &mut context, project, &mut facts);
    facts.namespace = (!facts.types.is_empty()).then(|| facts.types[0].namespace.clone());
    facts
}

#[must_use]
pub fn parse_project(project: &ProjectIdentity, files: &[(String, String)]) -> ProjectFacts {
    parse_project_with_references(project, files, &[])
}

#[must_use]
pub fn parse_project_with_references(
    project: &ProjectIdentity,
    files: &[(String, String)],
    references: &[ProjectFacts],
) -> ProjectFacts {
    // First pass: collect project-wide field and DbSet types. This lets a
    // partial class in one file resolve `Context` declared in another file.
    let first_pass: Vec<FileFacts> = files
        .iter()
        .map(|(path, source)| parse_file(project, path, source))
        .collect();
    let mut fields_by_type: HashMap<(String, String), HashMap<String, String>> = HashMap::new();
    let mut db_sets = Vec::new();
    for reference in references {
        for field in &reference.fields {
            fields_by_type
                .entry((field.namespace.clone(), field.type_name.clone()))
                .or_default()
                .insert(field.name.clone(), field.field_type.clone());
        }
        for db_set in &reference.db_sets {
            if !db_sets.iter().any(|existing: &DbSetFact| {
                existing.context_type == db_set.context_type && existing.property == db_set.property
            }) {
                db_sets.push(db_set.clone());
            }
        }
    }
    for file in &first_pass {
        for field in &file.fields {
            let entry = fields_by_type
                .entry((field.namespace.clone(), field.type_name.clone()))
                .or_default();
            entry.insert(field.name.clone(), field.field_type.clone());
        }
        for db_set in &file.db_sets {
            if !db_sets.iter().any(|existing: &DbSetFact| {
                existing.context_type == db_set.context_type && existing.property == db_set.property
            }) {
                db_sets.push(db_set.clone());
            }
        }
    }
    let scope = ProjectScope {
        fields_by_type,
        db_sets,
    };
    let parsed: Vec<FileFacts> = files
        .iter()
        .map(|(path, source)| parse_file_with_scope(project, path, source, &scope))
        .collect();
    let mut project_facts = ProjectFacts {
        project: project.clone(),
        types: Vec::new(),
        methods: Vec::new(),
        db_sets: Vec::new(),
        fields: Vec::new(),
        assignments: Vec::new(),
        invocations: Vec::new(),
        saves: Vec::new(),
        files: Vec::new(),
    };
    for file in &parsed {
        project_facts.types.extend(file.types.clone());
        project_facts.methods.extend(file.methods.clone());
        project_facts.db_sets.extend(file.db_sets.clone());
        project_facts.fields.extend(file.fields.clone());
        project_facts.assignments.extend(file.assignments.clone());
        project_facts.invocations.extend(file.invocations.clone());
        project_facts.saves.extend(file.saves.clone());
    }
    project_facts.files = parsed;
    resolve_project(&mut project_facts);
    project_facts
}

fn resolve_project(project: &mut ProjectFacts) {
    let mut methods_by_type: HashMap<(String, String), Vec<(String, MethodFact)>> = HashMap::new();
    let mut methods_by_name: HashMap<String, Vec<(String, MethodFact)>> = HashMap::new();
    for file in &project.files {
        for method in &file.methods {
            let key = (method.namespace.clone(), method.type_name.clone());
            methods_by_type
                .entry(key)
                .or_default()
                .push((file.path.clone(), method.clone()));
            methods_by_name
                .entry(method.name.clone())
                .or_default()
                .push((file.path.clone(), method.clone()));
        }
    }

    for file in &mut project.files {
        for invocation in &mut file.invocations {
            let mut resolution = resolve_invocation(invocation, &methods_by_type, &methods_by_name);
            invocation.status = resolution.0;
            invocation.target = resolution.1.take();
            invocation.reason = resolution.2.take();
        }
    }
    project.invocations = project
        .files
        .iter()
        .flat_map(|file| file.invocations.clone())
        .collect();
}

type Resolution = (ResolutionStatus, Option<MethodTarget>, Option<String>);

#[allow(clippy::too_many_lines)]
fn resolve_invocation(
    invocation: &InvocationFact,
    methods_by_type: &HashMap<(String, String), Vec<(String, MethodFact)>>,
    methods_by_name: &HashMap<String, Vec<(String, MethodFact)>>,
) -> Resolution {
    if is_dynamic_or_external(invocation) {
        return (
            ResolutionStatus::Candidate,
            None,
            Some(dynamic_reason(invocation)),
        );
    }

    if let Some(receiver_type) = &invocation.receiver_type {
        let key = (invocation.namespace.clone(), receiver_type.clone());
        if let Some(methods) = methods_by_type.get(&key) {
            let matches: Vec<&(String, MethodFact)> = methods
                .iter()
                .filter(|(_, method)| method.name == invocation.called)
                .collect();
            match matches.as_slice() {
                [] => {}
                [only] => {
                    return (
                        ResolutionStatus::Resolved,
                        Some(MethodTarget {
                            project: invocation.project.clone(),
                            namespace: only.1.namespace.clone(),
                            type_name: only.1.type_name.clone(),
                            method: only.1.name.clone(),
                            path: only.0.clone(),
                        }),
                        None,
                    );
                }
                _ => {
                    return (
                        ResolutionStatus::Candidate,
                        None,
                        Some("ambiguous receiver/overload".to_owned()),
                    );
                }
            }
        }
        // A receiver type may be declared in another namespace of the same project.
        let project_matches: Vec<&(String, MethodFact)> = methods_by_name
            .get(&invocation.called)
            .into_iter()
            .flatten()
            .filter(|(_, method)| method.type_name == *receiver_type)
            .collect();
        match project_matches.as_slice() {
            [only] => {
                return (
                    ResolutionStatus::Resolved,
                    Some(MethodTarget {
                        project: invocation.project.clone(),
                        namespace: only.1.namespace.clone(),
                        type_name: only.1.type_name.clone(),
                        method: only.1.name.clone(),
                        path: only.0.clone(),
                    }),
                    None,
                );
            }
            [] => {
                return (
                    ResolutionStatus::Candidate,
                    None,
                    Some("receiver type/method not found in project".to_owned()),
                );
            }
            _ => {
                return (
                    ResolutionStatus::Candidate,
                    None,
                    Some("ambiguous receiver/overload".to_owned()),
                );
            }
        }
    }

    // Unqualified call: current type first, then a unique project-wide method.
    let current: Vec<&(String, MethodFact)> = methods_by_name
        .get(&invocation.called)
        .into_iter()
        .flatten()
        .filter(|(_, method)| {
            method.namespace == invocation.namespace && method.type_name == invocation.type_name
        })
        .collect();
    if let [only] = current.as_slice() {
        return (
            ResolutionStatus::Resolved,
            Some(MethodTarget {
                project: invocation.project.clone(),
                namespace: only.1.namespace.clone(),
                type_name: only.1.type_name.clone(),
                method: only.1.name.clone(),
                path: only.0.clone(),
            }),
            None,
        );
    }
    let project_matches: Vec<&(String, MethodFact)> = methods_by_name
        .get(&invocation.called)
        .into_iter()
        .flatten()
        .collect();
    if let [only] = project_matches.as_slice() {
        return (
            ResolutionStatus::Resolved,
            Some(MethodTarget {
                project: invocation.project.clone(),
                namespace: only.1.namespace.clone(),
                type_name: only.1.type_name.clone(),
                method: only.1.name.clone(),
                path: only.0.clone(),
            }),
            None,
        );
    }
    (
        ResolutionStatus::Candidate,
        None,
        Some("unqualified call is not unique in project".to_owned()),
    )
}

fn is_dynamic_or_external(invocation: &InvocationFact) -> bool {
    matches!(
        invocation.called.as_str(),
        "Invoke"
            | "GetMethod"
            | "GetType"
            | "ExecuteSqlCommand"
            | "SqlQuery"
            | "SaveChanges"
            | "FirstOrDefault"
            | "SingleOrDefault"
            | "Where"
            | "Delete"
            | "Write"
            | "Send"
    ) || invocation
        .receiver_type
        .as_deref()
        .is_some_and(|ty| matches!(ty, "MethodInfo" | "Type" | "DatabaseFacade"))
}

fn dynamic_reason(invocation: &InvocationFact) -> String {
    match invocation.called.as_str() {
        "Invoke" => "reflection/dynamic dispatch target".to_owned(),
        "GetMethod" | "GetType" => "reflection lookup, not a resolved call".to_owned(),
        "ExecuteSqlCommand" | "SqlQuery" => "dynamic/external SQL target".to_owned(),
        "SaveChanges" => "external EF SaveChanges boundary".to_owned(),
        "FirstOrDefault" | "SingleOrDefault" | "Where" => {
            "extension/LINQ method, receiver is the data source".to_owned()
        }
        _ => "external or dynamic target".to_owned(),
    }
}

#[allow(clippy::too_many_lines)]
fn walk(
    node: Node<'_>,
    source: &str,
    context: &mut Context,
    project: &ProjectIdentity,
    out: &mut FileFacts,
) {
    match node.kind() {
        "namespace_declaration" | "file_scoped_namespace_declaration" => {
            let old = context.namespace.clone();
            if let Some(name) = node.child_by_field_name("name") {
                let parts = text(name, source)
                    .split('.')
                    .map(str::trim)
                    .filter(|part| !part.is_empty())
                    .map(str::to_owned)
                    .collect::<Vec<_>>();
                context.namespace.extend(parts);
            }
            if let Some(body) = node.child_by_field_name("body") {
                walk(body, source, context, project, out);
            } else {
                walk_children(node, source, context, project, out);
            }
            context.namespace = old;
        }
        "class_declaration"
        | "struct_declaration"
        | "interface_declaration"
        | "record_declaration" => {
            let Some(name_node) = node.child_by_field_name("name") else {
                walk_children(node, source, context, project, out);
                return;
            };
            let name = text(name_node, source);
            let partial = has_modifier(node, source, "partial");
            out.types.push(TypeFact {
                path: out.path.clone(),
                namespace: context.namespace(),
                name: name.clone(),
                kind: node.kind().trim_end_matches("_declaration").to_owned(),
                partial,
                line_start: line(node),
                line_end: end_line(node),
            });
            let old_type = context.type_name.replace(name.clone());
            let old_partial = std::mem::replace(&mut context.type_partial, partial);
            let old_fields = std::mem::take(&mut context.fields);
            if let Some(shared) = context
                .shared_fields
                .get(&(context.namespace(), name))
                .cloned()
            {
                context.fields.extend(shared);
            }
            if let Some(body) = node.child_by_field_name("body") {
                walk(body, source, context, project, out);
            }
            context.type_name = old_type;
            context.type_partial = old_partial;
            context.fields = old_fields;
        }
        "property_declaration" => {
            record_property(node, source, context, out);
            walk_children(node, source, context, project, out);
        }
        "field_declaration" => {
            record_field(node, source, context, out);
            walk_children(node, source, context, project, out);
        }
        "method_declaration" | "constructor_declaration" => {
            walk_method(node, source, context, project, out);
        }
        "variable_declaration" => {
            record_variables(node, source, context, out);
            walk_children(node, source, context, project, out);
        }
        "assignment_expression" => {
            record_assignment(node, source, context, out);
            walk_children(node, source, context, project, out);
        }
        "invocation_expression" => {
            record_invocation(node, source, context, out);
            walk_children(node, source, context, project, out);
        }
        "if_statement" => {
            let condition = node
                .child_by_field_name("condition")
                .map(|n| text(n, source))
                .unwrap_or_default();
            if let Some(condition_node) = node.child_by_field_name("condition") {
                walk(condition_node, source, context, project, out);
            }
            context.guards.push(format!("if ({condition})"));
            if let Some(consequence) = node.child_by_field_name("consequence") {
                walk(consequence, source, context, project, out);
            }
            context.guards.pop();
            if let Some(alternative) = node.child_by_field_name("alternative") {
                context.guards.push(format!("else ({condition})"));
                walk(alternative, source, context, project, out);
                context.guards.pop();
            }
        }
        "switch_section" => {
            let label = text(node, source)
                .split('{')
                .next()
                .unwrap_or_default()
                .trim()
                .to_owned();
            context.guards.push(format!("switch section: {label}"));
            walk_children(node, source, context, project, out);
            context.guards.pop();
        }
        _ => walk_children(node, source, context, project, out),
    }
}

fn walk_children(
    node: Node<'_>,
    source: &str,
    context: &mut Context,
    project: &ProjectIdentity,
    out: &mut FileFacts,
) {
    let mut cursor = node.walk();
    for child in node.named_children(&mut cursor) {
        walk(child, source, context, project, out);
    }
}

fn walk_method(
    node: Node<'_>,
    source: &str,
    context: &mut Context,
    project: &ProjectIdentity,
    out: &mut FileFacts,
) {
    let Some(name_node) = node.child_by_field_name("name") else {
        walk_children(node, source, context, project, out);
        return;
    };
    let name = text(name_node, source);
    let mut parameters = Vec::new();
    if let Some(parameter_list) = node.child_by_field_name("parameters") {
        let mut cursor = parameter_list.walk();
        for parameter in parameter_list.named_children(&mut cursor) {
            if parameter.kind() != "parameter" {
                continue;
            }
            let p_name = parameter
                .child_by_field_name("name")
                .map(|n| text(n, source))
                .unwrap_or_default();
            let p_type = parameter
                .child_by_field_name("type")
                .map(|n| text(n, source))
                .unwrap_or_default();
            if !p_name.is_empty() {
                parameters.push(ParameterFact {
                    name: p_name,
                    type_name: p_type,
                });
            }
        }
    }
    let signature = node.child_by_field_name("returns").map_or_else(
        || text(node, source),
        |returns| format!("{} {}", text(returns, source), name),
    );
    out.methods.push(MethodFact {
        project: project.name.clone(),
        path: out.path.clone(),
        namespace: context.namespace(),
        type_name: context.type_name(),
        name: name.clone(),
        signature,
        line_start: line(node),
        line_end: end_line(node),
        parameters: parameters.clone(),
    });
    let old_method = context.method.replace(name);
    let old_params = std::mem::take(&mut context.params);
    let old_locals = std::mem::take(&mut context.locals);
    for parameter in parameters {
        context.params.insert(parameter.name, parameter.type_name);
    }
    if let Some(body) = node.child_by_field_name("body") {
        walk(body, source, context, project, out);
    }
    context.method = old_method;
    context.params = old_params;
    context.locals = old_locals;
}

fn record_property(node: Node<'_>, source: &str, context: &mut Context, out: &mut FileFacts) {
    let name = node
        .child_by_field_name("name")
        .map(|n| text(n, source))
        .unwrap_or_default();
    let type_text = node
        .child_by_field_name("type")
        .map(|n| text(n, source))
        .unwrap_or_default();
    if name.is_empty() {
        return;
    }
    if let Some(element) = db_set_element(&type_text) {
        out.db_sets.push(DbSetFact {
            path: out.path.clone(),
            context_type: context.type_name(),
            property: name.clone(),
            element_type: element,
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
        });
    }
    if !type_text.is_empty() {
        context.fields.insert(name.clone(), type_text.clone());
        out.fields.push(FieldFact {
            namespace: context.namespace(),
            type_name: context.type_name(),
            name,
            field_type: type_text,
        });
    }
}

fn record_field(node: Node<'_>, source: &str, context: &mut Context, out: &mut FileFacts) {
    let mut cursor = node.walk();
    for child in node.named_children(&mut cursor) {
        if child.kind() != "variable_declaration" {
            continue;
        }
        let type_text = child
            .child_by_field_name("type")
            .map(|n| text(n, source))
            .unwrap_or_default();
        let mut declarators = child.walk();
        for declarator in child.named_children(&mut declarators) {
            if declarator.kind() != "variable_declarator" {
                continue;
            }
            if let Some(name) = declarator.child_by_field_name("name") {
                let name = text(name, source);
                context.fields.insert(name.clone(), type_text.clone());
                out.fields.push(FieldFact {
                    namespace: context.namespace(),
                    type_name: context.type_name(),
                    name,
                    field_type: type_text.clone(),
                });
            }
        }
    }
}

fn record_variables(node: Node<'_>, source: &str, context: &mut Context, out: &mut FileFacts) {
    if context.method.is_none() {
        return;
    }
    let declared = node
        .child_by_field_name("type")
        .map(|n| text(n, source))
        .unwrap_or_default();
    let mut cursor = node.walk();
    for declarator in node.named_children(&mut cursor) {
        if declarator.kind() != "variable_declarator" {
            continue;
        }
        let Some(name) = declarator.child_by_field_name("name") else {
            continue;
        };
        let name = text(name, source);
        let value = declarator.named_child(1);
        let inferred_type = value.and_then(|v| infer_type(v, source, context, out));
        let inferred_text = value.and_then(|v| infer_from_text(&text(v, source), context, out));
        let inferred = if declared == "var" {
            inferred_type.or(inferred_text)
        } else {
            Some(declared.clone())
        };
        if let Some(inferred) = inferred {
            context.locals.insert(name, inferred);
        }
    }
}

fn record_assignment(node: Node<'_>, source: &str, context: &mut Context, out: &mut FileFacts) {
    if context.method.is_none() {
        return;
    }
    let Some(left) = node.child_by_field_name("left") else {
        return;
    };
    if left.kind() != "member_access_expression" {
        return;
    }
    let Some(receiver) = left.child_by_field_name("expression") else {
        return;
    };
    let Some(field) = left.child_by_field_name("name") else {
        return;
    };
    let receiver_text = text(receiver, source);
    let receiver_type = infer_type(receiver, source, context, out)
        .or_else(|| context.fields.get(&receiver_text).cloned())
        .or_else(|| context.locals.get(&receiver_text).cloned());
    out.assignments.push(AssignmentFact {
        namespace: context.namespace(),
        type_name: context.type_name(),
        method: context.method_name(),
        receiver: receiver_text,
        receiver_type,
        field: text(field, source),
        value: node
            .child_by_field_name("right")
            .map(|n| text(n, source))
            .unwrap_or_default(),
        guard: context.guard(),
        line: line(node),
    });
}

fn record_invocation(node: Node<'_>, source: &str, context: &mut Context, out: &mut FileFacts) {
    if context.method.is_none() {
        return;
    }
    let Some(function) = node.child_by_field_name("function") else {
        return;
    };
    let (receiver_node, called) = if function.kind() == "member_access_expression" {
        let receiver = function.child_by_field_name("expression");
        let called = function
            .child_by_field_name("name")
            .map(|n| text(n, source))
            .unwrap_or_default();
        (receiver, called)
    } else {
        (None, text(function, source))
    };
    if called.is_empty() {
        return;
    }
    let receiver_text = receiver_node.map(|n| text(n, source));
    let receiver_type = receiver_node
        .and_then(|n| infer_type(n, source, context, out))
        .or_else(|| {
            receiver_text
                .as_ref()
                .and_then(|r| context.locals.get(r).cloned())
        })
        .or_else(|| {
            receiver_text
                .as_ref()
                .and_then(|r| context.fields.get(r).cloned())
        });
    let arguments = node
        .child_by_field_name("arguments")
        .map(|n| text(n, source))
        .unwrap_or_default();
    if called == "SaveChanges" {
        out.saves.push(SaveFact {
            namespace: context.namespace(),
            type_name: context.type_name(),
            method: context.method_name(),
            receiver: receiver_text.clone().unwrap_or_default(),
            context_type: receiver_type.clone(),
            guard: context.guard(),
            line: line(node),
        });
    }
    out.invocations.push(InvocationFact {
        project: out.project.clone(),
        path: out.path.clone(),
        namespace: context.namespace(),
        type_name: context.type_name(),
        method: context.method_name(),
        receiver: receiver_text,
        receiver_type,
        called,
        arguments,
        guard: context.guard(),
        line: line(node),
        status: ResolutionStatus::Candidate,
        target: None,
        reason: None,
    });
}

fn infer_type(node: Node<'_>, source: &str, context: &Context, out: &FileFacts) -> Option<String> {
    match node.kind() {
        "identifier" => {
            let name = text(node, source);
            context
                .locals
                .get(&name)
                .or_else(|| context.params.get(&name))
                .or_else(|| context.fields.get(&name))
                .cloned()
                .or_else(|| {
                    name.chars()
                        .next()
                        .is_some_and(char::is_uppercase)
                        .then_some(name)
                })
        }
        "object_creation_expression" => node
            .child_by_field_name("type")
            .map(|n| base_type_name(&text(n, source))),
        "member_access_expression" => {
            let receiver = node.child_by_field_name("expression")?;
            let name = node.child_by_field_name("name").map(|n| text(n, source))?;
            let receiver_type = infer_type(receiver, source, context, out)?;
            if let Some(element) = dbset_element_for(&receiver_type, &name, out) {
                return Some(element);
            }
            if name == "Database"
                && out
                    .db_sets
                    .iter()
                    .any(|db_set| db_set.context_type == receiver_type)
            {
                return Some("DatabaseFacade".to_owned());
            }
            if name == "FirstOrDefault" || name == "SingleOrDefault" {
                return infer_type(receiver, source, context, out);
            }
            None
        }
        "invocation_expression" => {
            let function = node.child_by_field_name("function")?;
            if function.kind() == "member_access_expression" {
                let receiver = function.child_by_field_name("expression")?;
                let name = function
                    .child_by_field_name("name")
                    .map(|n| text(n, source))
                    .unwrap_or_default();
                if name == "GetType" {
                    return Some("Type".to_owned());
                }
                if name == "GetMethod" {
                    return Some("MethodInfo".to_owned());
                }
                if name == "FirstOrDefault" || name == "SingleOrDefault" {
                    return infer_type(receiver, source, context, out);
                }
                let receiver_type = infer_type(receiver, source, context, out)?;
                if let Some(element) = dbset_element_for(&receiver_type, &name, out) {
                    return Some(element);
                }
            }
            None
        }
        "generic_name" => node.child_by_field_name("name").map(|n| text(n, source)),
        "predefined_type" | "qualified_name" => Some(base_type_name(&text(node, source))),
        _ => None,
    }
}

fn infer_from_text(text_value: &str, context: &Context, out: &FileFacts) -> Option<String> {
    let compact = text_value.trim();
    if let Some(rest) = compact.strip_prefix("new ") {
        let ty = rest
            .split(['(', '<', '[', ' '])
            .next()
            .unwrap_or_default()
            .trim();
        if !ty.is_empty() {
            return Some(base_type_name(ty));
        }
    }
    if compact.contains("Type.GetType") {
        return Some("Type".to_owned());
    }
    if compact.contains(".GetMethod") {
        return Some("MethodInfo".to_owned());
    }
    let mut access = compact.split('.');
    if let (Some(receiver), Some(property)) = (access.next(), access.next())
        && let Some(receiver_type) = context
            .locals
            .get(receiver)
            .or_else(|| context.params.get(receiver))
            .or_else(|| context.fields.get(receiver))
        && let Some(element) = dbset_element_for(receiver_type, property, out)
    {
        return Some(element);
    }
    let name = compact.split(['(', '.', ' ']).next().unwrap_or_default();
    context
        .locals
        .get(name)
        .or_else(|| context.params.get(name))
        .or_else(|| context.fields.get(name))
        .cloned()
}

fn dbset_element_for(receiver_type: &str, property: &str, out: &FileFacts) -> Option<String> {
    out.db_sets
        .iter()
        .find(|db_set| db_set.context_type == receiver_type && db_set.property == property)
        .map(|db_set| db_set.element_type.clone())
}

fn db_set_element(type_text: &str) -> Option<String> {
    let trimmed = type_text.trim();
    for prefix in ["DbSet<", "IDbSet<"] {
        if let Some(rest) = trimmed.strip_prefix(prefix) {
            return rest
                .strip_suffix('>')
                .map(|element| base_type_name(element.trim()));
        }
    }
    None
}

fn base_type_name(type_text: &str) -> String {
    type_text
        .trim()
        .trim_end_matches('?')
        .split(['<', '.'])
        .next_back()
        .unwrap_or(type_text)
        .trim()
        .to_owned()
}

fn has_modifier(node: Node<'_>, source: &str, modifier: &str) -> bool {
    let mut cursor = node.walk();
    node.named_children(&mut cursor)
        .any(|child| child.kind() == "modifier" && text(child, source) == modifier)
}

fn text(node: Node<'_>, source: &str) -> String {
    node.utf8_text(source.as_bytes())
        .unwrap_or_default()
        .trim()
        .to_owned()
}

fn line(node: Node<'_>) -> usize {
    node.start_position().row + 1
}

fn end_line(node: Node<'_>) -> usize {
    node.end_position().row + 1
}

/// A bounded callgraph expansion helper: returns the next target paths while
/// deduplicating cycles. This is intentionally separate from relation storage.
#[must_use]
pub fn expand_targets(
    start: &MethodTarget,
    project: &ProjectFacts,
    max_hops: usize,
) -> (Vec<MethodTarget>, Vec<String>) {
    let mut seen: BTreeSet<(String, String, String, String)> = BTreeSet::new();
    let mut frontier = vec![start.clone()];
    let mut output = Vec::new();
    let mut gaps = Vec::new();
    let mut hops = 0;
    while !frontier.is_empty() {
        if hops >= max_hops {
            gaps.push(format!("hop cap reached at {max_hops}"));
            break;
        }
        hops += 1;
        let mut next = Vec::new();
        for target in frontier {
            let key = (
                target.project.clone(),
                target.namespace.clone(),
                target.type_name.clone(),
                target.method.clone(),
            );
            if !seen.insert(key) {
                gaps.push(format!(
                    "cycle/dedup at {}.{}",
                    target.type_name, target.method
                ));
                continue;
            }
            output.push(target.clone());
            for invocation in &project.invocations {
                if invocation.status == ResolutionStatus::Resolved
                    && invocation.namespace == target.namespace
                    && invocation.type_name == target.type_name
                    && let Some(next_target) = &invocation.target
                {
                    next.push(next_target.clone());
                }
            }
        }
        frontier = next;
    }
    (output, gaps)
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;

    use super::*;

    fn fixture(path: &str) -> String {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../benchmarks/rag-impact/cases/synthetic");
        fs::read_to_string(root.join(path)).expect("fixture source")
    }

    fn project(name: &str) -> ProjectIdentity {
        ProjectIdentity {
            name: name.to_owned(),
            assembly: name.to_owned(),
            root_namespace: Some("Shop.Orders".to_owned()),
        }
    }

    fn find_invocation<'a>(facts: &'a ProjectFacts, called: &str) -> Vec<&'a InvocationFact> {
        facts
            .invocations
            .iter()
            .filter(|invocation| invocation.called == called)
            .collect()
    }

    #[test]
    fn same_fqn_in_two_projects_resolves_inside_each_project() {
        let files_a = vec![
            (
                "ProjectA/Orders/Caller.cs".to_owned(),
                fixture("D13/ProjectA/Orders/Caller.cs"),
            ),
            (
                "ProjectA/Orders/OrderService.cs".to_owned(),
                fixture("D13/ProjectA/Orders/OrderService.cs"),
            ),
        ];
        let files_b = vec![
            (
                "ProjectB/Orders/Caller.cs".to_owned(),
                fixture("D13/ProjectB/Orders/Caller.cs"),
            ),
            (
                "ProjectB/Orders/OrderService.cs".to_owned(),
                fixture("D13/ProjectB/Orders/OrderService.cs"),
            ),
        ];
        let a = parse_project(&project("ProjectA"), &files_a);
        let b = parse_project(&project("ProjectB"), &files_b);
        let update_a = find_invocation(&a, "Update");
        let update_b = find_invocation(&b, "Update");
        assert_eq!(update_a.len(), 1);
        assert_eq!(update_b.len(), 1);
        assert_eq!(update_a[0].status, ResolutionStatus::Resolved);
        assert_eq!(update_b[0].status, ResolutionStatus::Resolved);
        assert_eq!(
            update_a[0].target.as_ref().expect("A target").path,
            "ProjectA/Orders/OrderService.cs"
        );
        assert_eq!(
            update_b[0].target.as_ref().expect("B target").path,
            "ProjectB/Orders/OrderService.cs"
        );
        assert_ne!(update_a[0].target, update_b[0].target);
    }

    #[test]
    fn reflection_and_dynamic_sql_remain_candidates() {
        let files = vec![(
            "DynamicTargets.cs".to_owned(),
            fixture("D14/DynamicTargets.cs"),
        )];
        let facts = parse_project(&project("D14"), &files);
        for called in ["Invoke", "ExecuteSqlCommand", "GetMethod", "GetType"] {
            assert!(
                !find_invocation(&facts, called).is_empty(),
                "missing {called}"
            );
        }
        assert!(facts.candidate_invocations().count() >= 4);
        assert!(facts.resolved_invocations().next().is_none());
    }

    #[test]
    fn project_identity_reads_csproj_assembly_and_root_namespace() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../benchmarks/rag-impact/cases/synthetic/D13/ProjectA/ProjectA.csproj");
        let identity = ProjectIdentity::from_csproj(&path);
        assert_eq!(identity.name, "ProjectA");
        assert_eq!(identity.assembly, "ProjectA");
        assert_eq!(identity.root_namespace.as_deref(), Some("Shop.Orders"));
    }

    #[test]
    fn cycle_edges_resolve_and_expansion_is_bounded() {
        let files = vec![
            ("CycleA.cs".to_owned(), fixture("D16/CycleA.cs")),
            ("CycleB.cs".to_owned(), fixture("D16/CycleB.cs")),
            ("LongChain.cs".to_owned(), fixture("D16/LongChain.cs")),
        ];
        let facts = parse_project(&project("D16"), &files);
        let a = find_invocation(&facts, "A");
        let b = find_invocation(&facts, "B");
        assert!(a.iter().any(|i| i.status == ResolutionStatus::Resolved));
        assert!(b.iter().any(|i| i.status == ResolutionStatus::Resolved));
        let start = a
            .iter()
            .find_map(|i| i.target.clone())
            .expect("resolved A target");
        let (expanded, gaps) = expand_targets(&start, &facts, 6);
        assert!(expanded.len() <= 6);
        assert!(!gaps.is_empty());
    }

    #[test]
    fn personal_update_keeps_saves_guards_and_receiver_types() {
        let files = vec![(
            "PersonalUpdate.cs".to_owned(),
            fixture("R2/PersonalUpdate.cs"),
        )];
        let facts = parse_project(&project("R2"), &files);
        let command = facts
            .methods
            .iter()
            .find(|method| method.name == "EmployeeEditThongTinCaNhan")
            .expect("method");
        assert_eq!(command.parameters[0].type_name, "EmployeeEditModel");
        let assignments: Vec<&AssignmentFact> = facts
            .assignments
            .iter()
            .filter(|assignment| assignment.method == "EmployeeEditThongTinCaNhan")
            .collect();
        assert!(assignments.iter().any(|a| {
            a.receiver == "u"
                && a.field == "FullName"
                && a.receiver_type.as_deref() == Some("Employee")
        }));
        assert!(assignments.iter().any(|a| {
            a.receiver == "d"
                && a.field == "MaChamCong"
                && a.receiver_type.as_deref() == Some("EmployeeDetail")
        }));
        let saves: Vec<&SaveFact> = facts
            .saves
            .iter()
            .filter(|save| save.method == "EmployeeEditThongTinCaNhan")
            .collect();
        assert_eq!(saves.len(), 2);
        assert!(
            saves
                .iter()
                .all(|save| save.context_type.as_deref() == Some("CoreDBContext"))
        );
        let update = find_invocation(&facts, "UpdateOrgTotalEmployee");
        assert_eq!(update.len(), 1);
        assert_eq!(update[0].status, ResolutionStatus::Resolved);
        assert!(
            update[0]
                .guard
                .as_deref()
                .is_some_and(|guard| guard.contains("changeType"))
        );
    }

    #[test]
    fn cross_file_partial_context_resolves_dbset_element() {
        let context_source = r"
namespace Synthetic.Cross {
    public class Employee { public string FullName { get; set; } }
    public class CoreDBContext { public DbSet<Employee> Employees { get; set; } }
}
";
        let command_source = r#"
namespace Synthetic.Cross {
    public partial class CoreService {
        public CoreDBContext Context { get; set; }
        public void Update() {
            var u = Context.Employees.FirstOrDefault(x => x.FullName == "x");
            u.FullName = "y";
        }
    }
}
"#;
        let files = vec![
            ("CoreDBContext.cs".to_owned(), context_source.to_owned()),
            ("CoreService.cs".to_owned(), command_source.to_owned()),
        ];
        let facts = parse_project(&project("Cross"), &files);
        let assignment = facts
            .assignments
            .iter()
            .find(|assignment| assignment.field == "FullName")
            .expect("assignment");
        assert_eq!(assignment.receiver_type.as_deref(), Some("Employee"));
    }

    #[test]
    #[ignore = "requires the external HRM2.0 pilot checkout for the real-case R2 gate"]
    #[allow(clippy::too_many_lines)]
    fn real_hrm2_development_cases() {
        use std::collections::HashMap;

        let manifest_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../benchmarks/rag-impact/corpus-manifest.json");
        let manifest: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&manifest_path).expect("corpus manifest"))
                .expect("valid corpus manifest");
        let source_root = PathBuf::from(
            manifest["source_root"]
                .as_str()
                .expect("source_root in corpus manifest"),
        );
        let mut by_project: HashMap<String, Vec<(String, String)>> = HashMap::new();
        for file in manifest["files"].as_array().expect("files") {
            let Some(project) = file["project"].as_str() else {
                continue;
            };
            if file["kind"].as_str() != Some("cs_compile") {
                continue;
            }
            let relative = file["path"].as_str().expect("path");
            let source = fs::read_to_string(source_root.join(relative)).expect("source file");
            by_project
                .entry(project.to_owned())
                .or_default()
                .push((relative.to_owned(), source));
        }

        let project_files = [
            ("P1", "Webs/HRM/HRM.csproj"),
            ("P2", "Services/HRIS/Service.Core/Service.Core.csproj"),
            ("P3", "DBContexts/HRIS/DBContext.Core/DBContext.Core.csproj"),
        ];
        let mut projects = HashMap::new();
        let mut identities = HashMap::new();
        for (id, csproj) in project_files {
            identities.insert(id, ProjectIdentity::from_csproj(&source_root.join(csproj)));
        }
        let p3 = parse_project(
            identities.get("P3").expect("P3"),
            &by_project.remove("P3").expect("P3 files"),
        );
        let p2 = parse_project_with_references(
            identities.get("P2").expect("P2"),
            &by_project.remove("P2").expect("P2 files"),
            std::slice::from_ref(&p3),
        );
        let p1 = parse_project_with_references(
            identities.get("P1").expect("P1"),
            &by_project.remove("P1").expect("P1 files"),
            &[p2.clone(), p3.clone()],
        );
        projects.insert("P1", p1);
        projects.insert("P2", p2);
        projects.insert("P3", p3);
        let all = ["P1", "P2", "P3"]
            .iter()
            .map(|id| projects.get(id).expect("project"))
            .collect::<Vec<_>>();

        let core_ns = "Service.Core.Executes.Base";
        let find_method = |type_name: &str, method: &str| {
            all.iter()
                .flat_map(|project| project.methods.iter())
                .find(|fact| {
                    fact.namespace == core_ns && fact.type_name == type_name && fact.name == method
                })
                .unwrap_or_else(|| panic!("missing {type_name}.{method}"))
        };
        let assignments = |type_name: &str, method: &str| {
            all.iter()
                .flat_map(|project| project.assignments.iter())
                .filter(move |fact| {
                    fact.namespace == core_ns
                        && fact.type_name == type_name
                        && fact.method == method
                })
                .collect::<Vec<_>>()
        };
        let saves = |type_name: &str, method: &str| {
            all.iter()
                .flat_map(|project| project.saves.iter())
                .filter(move |fact| {
                    fact.namespace == core_ns
                        && fact.type_name == type_name
                        && fact.method == method
                })
                .collect::<Vec<_>>()
        };
        let invocations = |type_name: &str, called: &str| {
            all.iter()
                .flat_map(|project| project.invocations.iter())
                .filter(move |fact| fact.type_name == type_name && fact.called == called)
                .collect::<Vec<_>>()
        };
        let core_invocations = |type_name: &str, called: &str| {
            all.iter()
                .flat_map(|project| project.invocations.iter())
                .filter(move |fact| {
                    fact.namespace == core_ns
                        && fact.type_name == type_name
                        && fact.called == called
                })
                .collect::<Vec<_>>()
        };

        // D01/D06: controller dispatch and guarded org-total call exist.
        let dispatch = invocations("EmployeeController", "EmployeeEditThongTinCaNhan");
        assert!(!dispatch.is_empty(), "D01 dispatch missing");
        let org_total = core_invocations("CoreService", "UpdateOrgTotalEmployee");
        assert!(
            org_total.iter().any(|fact| fact
                .guard
                .as_deref()
                .is_some_and(|guard| guard.contains("changeType"))),
            "D06 changeType guard missing"
        );

        // D02/D03/D04: personal update fields and two SaveChanges; EmployeeUpdateInfo has no Keyword write.
        let update_method = find_method("CoreService", "EmployeeEditThongTinCaNhan");
        assert_eq!(update_method.parameters[0].type_name, "EmployeeEditModel");
        let update_assignments = assignments("CoreService", "EmployeeEditThongTinCaNhan");
        for field in ["FullName", "Email", "Keyword"] {
            assert!(
                update_assignments.iter().any(|fact| {
                    fact.receiver == "u"
                        && fact.field == field
                        && fact.receiver_type.as_deref() == Some("Employee")
                }),
                "D02/D03 missing u.{field}"
            );
        }
        assert!(
            update_assignments
                .iter()
                .any(|fact| { fact.field == "Keyword" && fact.value.contains("FullName") }),
            "D03 Keyword expression must reference FullName"
        );
        let update_saves = saves("CoreService", "EmployeeEditThongTinCaNhan");
        assert_eq!(update_saves.len(), 2, "D02 expected two SaveChanges");
        let mut save_lines = update_saves
            .iter()
            .map(|save| save.line)
            .collect::<Vec<_>>();
        save_lines.sort_unstable();
        let detail_assignment = update_assignments
            .iter()
            .find(|fact| fact.receiver == "d" && fact.field == "MaChamCong")
            .map(|fact| fact.line)
            .expect("D05 detail assignment");
        assert!(
            save_lines[0] < detail_assignment,
            "D05 first SaveChanges must precede the detail assignment"
        );
        assert!(
            update_saves
                .iter()
                .all(|save| save.context_type.as_deref() == Some("CoreDBContext")),
            "D10 context inference failed"
        );
        let info_assignments = assignments("CoreService", "EmployeeUpdateInfo");
        assert!(
            !info_assignments.iter().any(|fact| fact.field == "Keyword"),
            "D04 EmployeeUpdateInfo must not write Keyword"
        );

        // D08: raw SQL delete is an external/dynamic call, not a resolved call.
        assert!(
            core_invocations("CoreService", "ExecuteSqlCommand")
                .iter()
                .all(|fact| fact.status == ResolutionStatus::Candidate),
            "D08 raw SQL must remain candidate"
        );

        // D11: only the Avatar switch branch is assigned.
        let attr_assignments = assignments("CoreService", "UpdateEmployeeAttr");
        assert!(
            attr_assignments.iter().any(|fact| {
                fact.field == "Avatar"
                    && fact
                        .guard
                        .as_deref()
                        .is_some_and(|guard| guard.contains("Avatar"))
            }),
            "D11 Avatar guard missing"
        );
        assert!(
            attr_assignments
                .iter()
                .all(|fact| matches!(fact.field.as_str(), "Avatar")),
            "D11 unexpected field assignment"
        );
    }

    #[test]
    fn cross_project_dbset_reference_resolves_element_type() {
        let context_source = r"
namespace Synthetic.Cross {
    public class Employee { public string FullName { get; set; } }
    public class CoreDBContext { public DbSet<Employee> Employees { get; set; } }
}
";
        let service_source = r#"
namespace Synthetic.Cross {
    public partial class CoreService {
        public CoreDBContext Context { get; set; }
        public void Update() {
            var u = Context.Employees.FirstOrDefault(x => x.FullName == "x");
            u.FullName = "y";
        }
    }
}
"#;
        let context_facts = parse_project(
            &ProjectIdentity {
                name: "ContextProject".to_owned(),
                assembly: "ContextProject".to_owned(),
                root_namespace: Some("Synthetic.Cross".to_owned()),
            },
            &[("CoreDBContext.cs".to_owned(), context_source.to_owned())],
        );
        let service_facts = parse_project_with_references(
            &ProjectIdentity {
                name: "ServiceProject".to_owned(),
                assembly: "ServiceProject".to_owned(),
                root_namespace: Some("Synthetic.Cross".to_owned()),
            },
            &[("CoreService.cs".to_owned(), service_source.to_owned())],
            std::slice::from_ref(&context_facts),
        );
        let assignment = service_facts
            .assignments
            .iter()
            .find(|assignment| assignment.field == "FullName")
            .expect("assignment");
        assert_eq!(assignment.receiver_type.as_deref(), Some("Employee"));
    }

    #[test]
    fn broken_source_flags_parse_error_without_panicking() {
        let facts = parse_file(
            &project("Broken"),
            "Broken.cs",
            "namespace X { public class Broken { public void M( } }",
        );
        assert!(facts.parse_error);
    }
}
