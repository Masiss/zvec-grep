use super::storage::SearchStorage;

use std::{collections::HashSet, env, fs, path::Path};

use crate::{
    EngineError,
    api::context::{
        ContextOptions, ContextResult,
        options::{ContextRoute, ContextRouteMode},
        result::{
            ContentRange, ContextContentRole, ContextCoverage, ContextDiagnostics,
            ContextGroupResult, ContextItem, ContextItemKind, ContextItemStatus,
            ContextQueryGroupMatch, ContextQueryGroupRole, ContextSelectionReason, ContextSource,
            ContextWorkspaceIndex, EmptyReason, IndexDiagnostics, IndexQueryGroupDiagnostics,
            IndexRouteDiagnostics, MatchedBy,
        },
    },
    api::evidence::{EvidenceBudget, EvidenceRequest, assemble as assemble_evidence},
    domain::{Content, FileRecord, Range, Workspace},
    utils::sha256_hex,
};

use super::pipeline::{
    SearchEmbeddingRuntime, SearchHit, SearchPlan, SearchPlanResult, search_workspace_index,
};

const DEFAULT_CONTEXT_LIMIT: usize = 10;
const DEFAULT_CONTEXT_TOTAL_LIMIT: usize = 30;
const DEFAULT_CONTEXT_PRIORITY_LIMIT: usize = 6;
const CONTEXT_GROUP_RRF_K: f64 = 60.0;

#[derive(Clone, Debug)]
pub(crate) struct NormalizedContextRequest {
    pub display_query: String,
    pub routes: Vec<ContextRoute>,
    pub groups: Vec<NormalizedContextGroup>,
}

#[derive(Clone, Debug)]
pub(crate) struct NormalizedContextGroup {
    pub id: String,
    pub query: String,
    pub role: ContextQueryGroupRole,
    pub routes: Vec<ContextRoute>,
}

pub(crate) fn normalize_context_request(
    options: &ContextOptions,
) -> Result<NormalizedContextRequest, EngineError> {
    let primary_queries = options
        .query
        .iter()
        .chain(&options.queries)
        .filter_map(|query| {
            let query = query.trim();
            (!query.is_empty()).then(|| query.to_owned())
        })
        .collect::<Vec<_>>();
    let routes = options
        .routes
        .iter()
        .enumerate()
        .map(|(index, route)| {
            let query = route.query.trim();
            if query.is_empty() {
                return Err(EngineError::invalid_argument(format!(
                    "context route {index} requires a non-empty query"
                )));
            }
            Ok(ContextRoute {
                mode: route.mode,
                query: query.to_owned(),
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    if primary_queries.is_empty() && routes.is_empty() {
        return Err(EngineError::invalid_argument(
            "context requires a non-empty query or route",
        ));
    }

    let mut groups = primary_queries
        .iter()
        .enumerate()
        .map(|(index, query)| NormalizedContextGroup {
            id: format!("Q{}", index + 1),
            query: query.clone(),
            role: ContextQueryGroupRole::Primary,
            routes: vec![
                ContextRoute {
                    mode: ContextRouteMode::Fts,
                    query: query.clone(),
                },
                ContextRoute {
                    mode: ContextRouteMode::Vector,
                    query: query.clone(),
                },
            ],
        })
        .collect::<Vec<_>>();
    groups.extend(
        routes
            .iter()
            .enumerate()
            .map(|(index, route)| NormalizedContextGroup {
                id: format!("Q{}", primary_queries.len() + index + 1),
                query: route.query.clone(),
                role: ContextQueryGroupRole::Supplemental,
                routes: vec![route.clone()],
            }),
    );
    let all_routes = groups
        .iter()
        .flat_map(|group| group.routes.iter().cloned())
        .collect::<Vec<_>>();
    let display_query = if primary_queries.is_empty() {
        routes
            .iter()
            .map(|route| route.query.as_str())
            .collect::<Vec<_>>()
            .join(" | ")
    } else {
        primary_queries.join(" | ")
    };

    Ok(NormalizedContextRequest {
        display_query,
        routes: all_routes,
        groups,
    })
}

pub(crate) async fn context_from_index(
    root: &Path,
    workspace: &Workspace,
    storage: &dyn SearchStorage,
    embedding_models: &[&dyn SearchEmbeddingRuntime],
    options: &ContextOptions,
    request: &NormalizedContextRequest,
) -> Result<ContextResult, EngineError> {
    let groups = if options.fuse {
        vec![NormalizedContextGroup {
            id: "Q1".to_owned(),
            query: request.display_query.clone(),
            role: if request
                .groups
                .iter()
                .any(|group| group.role == ContextQueryGroupRole::Primary)
            {
                ContextQueryGroupRole::Primary
            } else {
                ContextQueryGroupRole::Supplemental
            },
            routes: request.routes.clone(),
        }]
    } else {
        request.groups.clone()
    };
    let limit = context_group_limit(options.limit, groups.len());
    let mut searches = Vec::with_capacity(groups.len());
    for group in &groups {
        searches.push(
            search_workspace_index(
                workspace.source_root(),
                SearchPlan {
                    routes: group.routes.clone(),
                    limit: Some(limit),
                    trace: options.trace,
                    prefer_symbol: options.prefer_symbol,
                    filter: options.filter.clone(),
                },
                storage,
                embedding_models,
            )
            .await?,
        );
    }

    build_context_result(root, workspace, request, &groups, searches)
}

fn build_context_result(
    root: &Path,
    workspace: &Workspace,
    request: &NormalizedContextRequest,
    groups: &[NormalizedContextGroup],
    searches: Vec<SearchPlanResult>,
) -> Result<ContextResult, EngineError> {
    let group_items = searches
        .iter()
        .zip(groups)
        .map(|(search, group)| search_plan_to_context_items(search, workspace.source_root(), group))
        .collect::<Result<Vec<_>, _>>()?;
    super::pipeline::dump_stage(
        "context_group_items",
        group_items
            .iter()
            .flatten()
            .map(|item| {
                serde_json::json!({
                    "entity_id": item.entity_id,
                    "fragment_id": item.fragment_id,
                    "path": item.relative_path,
                    "file_sha256": item.file_sha256,
                    "range": format!("{:?}", item.range),
                    "content_range": format!("{:?}", item.content_range),
                    "rank": item.rank,
                    "score": item.score,
                    "query_groups": item.query_groups,
                })
            })
            .collect(),
    );
    let coverage_groups = groups
        .iter()
        .filter(|group| group.role == ContextQueryGroupRole::Primary)
        .map(|group| group.id.clone())
        .collect::<Vec<_>>();
    let flat_items = || group_items.iter().flatten().cloned().collect::<Vec<_>>();
    let items = if env::var_os("ZVEC_RAG_QUERY_COVERAGE_SELECT").is_some() {
        let (item_budget, byte_budget) = fragment_budgets();
        let byte_cap = env::var("ZVEC_RAG_SELECT_BYTE_CAP")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(byte_budget)
            .min(byte_budget);
        coverage_budget_select(flat_items(), &request.display_query, item_budget, byte_cap)
    } else if env::var_os("ZVEC_RAG_FRAGMENT_RELEVANCE").is_some()
        || env::var_os("ZVEC_RAG_FRAGMENT_SELECT_V2").is_some()
    {
        fragment_budget_select(flat_items(), &request.display_query)
    } else if env::var_os("ZVEC_RAG_BUDGET_SELECT").is_some() {
        rag_budget_select(group_items.iter().flatten().cloned().collect())
    } else {
        select_and_rank_context_items(
            group_items.iter().flatten().cloned().collect(),
            &coverage_groups,
        )
    };
    if let Ok(pack_path) = env::var("ZVEC_RAG_EVIDENCE_PACK_FILE") {
        write_verified_pack(root, &items, &pack_path);
    }
    super::pipeline::dump_stage(
        "context_selected",
        items
            .iter()
            .map(|item| {
                serde_json::json!({
                    "entity_id": item.entity_id,
                    "fragment_id": item.fragment_id,
                    "path": item.relative_path,
                    "file_sha256": item.file_sha256,
                    "range": format!("{:?}", item.range),
                    "content_range": format!("{:?}", item.content_range),
                    "rank": item.rank,
                    "score": item.score,
                    "selection_reason": item.selection_reason,
                    "coverage_group": item.coverage_group,
                    "query_groups": item.query_groups,
                    "trace": item.trace,
                })
            })
            .collect(),
    );
    let timings = searches
        .iter()
        .flat_map(|search| search.timings.iter().cloned())
        .collect();
    let hits_returned = items.len();

    Ok(ContextResult {
        freshness: None,
        background_refresh: None,
        indexing: None,
        query: request.display_query.clone(),
        root: root.to_path_buf(),
        source: ContextSource::Index,
        coverage: ContextCoverage::RankedSample,
        workspace_index: Some(ContextWorkspaceIndex {
            root: workspace.source_root().to_path_buf(),
            path: workspace.layout().storage_dir().to_path_buf(),
        }),
        items,
        group_results: groups
            .iter()
            .zip(group_items)
            .map(|(group, items)| ContextGroupResult {
                id: group.id.clone(),
                query: group.query.clone(),
                role: group.role,
                items,
            })
            .collect(),
        diagnostics: ContextDiagnostics {
            empty_reason: (hits_returned == 0).then_some(EmptyReason::NoMatches),
            index: Some(IndexDiagnostics {
                hits_returned,
                query_groups: groups
                    .iter()
                    .map(|group| IndexQueryGroupDiagnostics {
                        id: group.id.clone(),
                        query: group.query.clone(),
                        role: group.role,
                    })
                    .collect(),
                routes: searches
                    .into_iter()
                    .flat_map(|search| search.routes)
                    .map(|route| IndexRouteDiagnostics {
                        id: route.id,
                        mode: route.mode,
                        query: route.query,
                    })
                    .collect(),
            }),
            rg: None,
            structure: None,
            timings,
        },
    })
}

fn context_group_limit(limit: Option<usize>, group_count: usize) -> usize {
    limit.unwrap_or_else(|| {
        let group_count = group_count.max(1);
        if group_count <= 3 {
            DEFAULT_CONTEXT_LIMIT
        } else {
            DEFAULT_CONTEXT_TOTAL_LIMIT.div_ceil(group_count).max(1)
        }
    })
}

fn search_plan_to_context_items(
    result: &SearchPlanResult,
    workspace_root: &Path,
    group: &NormalizedContextGroup,
) -> Result<Vec<ContextItem>, EngineError> {
    result
        .hits
        .iter()
        .map(|hit| {
            let target = context_item_target(hit)?;
            Ok(ContextItem {
                kind: ContextItemKind::IndexedEntity,
                rank: hit.rank,
                absolute_path: workspace_root.join(hit.file.relative_path()),
                relative_path: hit.file.relative_path().to_path_buf(),
                range: hit.entity.source_range().into(),
                excerpt_range: target.excerpt_range,
                content_range: target.content_range,
                match_ranges: Vec::new(),
                content: target.content,
                outline: crate::extraction::code_outline(
                    hit.file.relative_path().as_ref(),
                    hit.entity.content(),
                    hit.entity.metadata(),
                ),
                content_role: Some(target.content_role),
                status: file_freshness_status(workspace_root, &hit.file),
                score: Some(hit.score),
                matched_by: hit.matched_by,
                metadata: hit.entity.metadata().cloned(),
                entity_id: Some(hit.entity.id().as_str().to_owned()),
                fragment_id: hit
                    .evidence
                    .first()
                    .map(|evidence| evidence.fragment.id.as_str().to_owned()),
                file_sha256: hit.file.snapshot().content_hash.clone(),
                container: None,
                trace: hit.trace.clone(),
                query_groups: vec![ContextQueryGroupMatch {
                    id: group.id.clone(),
                    query: group.query.clone(),
                    role: group.role,
                    rank: hit.rank,
                    matched_by: hit.matched_by,
                }],
                selection_reason: None,
                coverage_group: None,
            })
        })
        .collect()
}

fn same_source(left: &ContextItem, right: &ContextItem) -> bool {
    left.relative_path == right.relative_path && left.file_sha256 == right.file_sha256
}

fn source_overlap_ratio(item: &ContextItem, other: &ContextItem) -> f64 {
    if !same_source(item, other) {
        return 0.0;
    }
    range_overlap_ratio(&item.content_range, &other.content_range)
}

/// Removes only source lines already delivered by `selected`, keeping every
/// non-overlapping remainder of the same fragment. Each remainder keeps its own
/// verified half-open source coordinates. Returns an empty vector when the
/// fragment is fully duplicated.
fn clip_fragment_overlap(item: ContextItem, selected: &[ContextItem]) -> Vec<ContextItem> {
    let (Some(start), Some(end)) = (
        item.content_range.start_line(),
        item.content_range.last_line(),
    ) else {
        return vec![item];
    };
    let mut blocked = selected
        .iter()
        .filter(|other| same_source(&item, other))
        .filter_map(|other| {
            let from = other.content_range.start_line()?;
            let to = other.content_range.last_line()?;
            Some((from, to))
        })
        .collect::<Vec<_>>();
    if blocked.is_empty() {
        return vec![item];
    }
    blocked.sort_unstable();
    let mut keep = Vec::new();
    let mut cursor = start;
    for (from, to) in blocked {
        if to < cursor {
            continue;
        }
        if from > end {
            break;
        }
        if from > cursor {
            keep.push((cursor, from.saturating_sub(1)));
        }
        cursor = cursor.max(to.saturating_add(1));
        if cursor > end {
            break;
        }
    }
    if cursor <= end {
        keep.push((cursor, end));
    }
    let (old_start_byte, old_start_column, old_end_column) = match item.content_range {
        ContentRange::Text {
            start_byte_offset,
            start_byte_column,
            end_byte_column,
            ..
        } => (start_byte_offset, start_byte_column, end_byte_column),
        _ => return vec![item],
    };
    let lines = crate::utils::line_byte_offsets(&item.content);
    let mut clipped = Vec::new();
    for (keep_start, keep_end) in keep {
        let local_start = keep_start.saturating_sub(start);
        let local_end_exclusive = keep_end
            .saturating_sub(start)
            .saturating_add(1)
            .min(lines.len().saturating_sub(1));
        let Some(&from) = lines.get(local_start) else {
            continue;
        };
        let to = lines
            .get(local_end_exclusive)
            .copied()
            .unwrap_or(item.content.len());
        if from >= to {
            continue;
        }
        let mut piece = item.clone();
        piece.content = item.content[from..to].to_owned();
        let at_line_boundary = local_end_exclusive < lines.len();
        piece.content_range = ContentRange::Text {
            start_line: keep_start,
            end_line: if at_line_boundary {
                keep_end.saturating_add(1)
            } else {
                keep_end
            },
            start_byte_offset: old_start_byte.saturating_add(from),
            end_byte_offset: old_start_byte.saturating_add(to),
            start_byte_column: if local_start == 0 {
                old_start_column
            } else {
                0
            },
            end_byte_column: if at_line_boundary { 0 } else { old_end_column },
        };
        piece.excerpt_range = Some(piece.content_range.clone());
        clipped.push(piece);
    }
    clipped
}

fn fragment_budgets() -> (usize, usize) {
    let item_budget = env::var("ZVEC_RAG_ITEM_BUDGET")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(64);
    let byte_budget = env::var("ZVEC_RAG_BYTE_BUDGET")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(24 * 1024);
    (item_budget, byte_budget)
}

fn fragment_budget_select(items: Vec<ContextItem>, query: &str) -> Vec<ContextItem> {
    let (item_budget, byte_budget) = fragment_budgets();
    let terms = {
        let mut terms = query
            .split(|c: char| !c.is_alphanumeric() && c != '_')
            .filter(|word| word.len() >= 3)
            .map(|word| word.to_lowercase())
            .collect::<Vec<_>>();
        terms.sort_unstable();
        terms.dedup();
        terms.truncate(24);
        terms
    };
    let rank_only = env::var_os("ZVEC_RAG_FRAGMENT_SELECT_RANK_ONLY").is_some();
    let mut candidates = Vec::new();
    let mut seen = HashSet::new();
    for item in items {
        if !seen.insert(context_item_dedupe_key(&item)) {
            continue;
        }
        if rank_only {
            candidates.push((item, 0.0));
            continue;
        }
        let lower = item.content.to_lowercase();
        let coverage = if terms.is_empty() {
            0.0
        } else {
            terms
                .iter()
                .filter(|term| lower.contains(term.as_str()))
                .count() as f64
                / terms.len() as f64
        };
        candidates.push((item, coverage));
    }
    let mut selected: Vec<ContextItem> = Vec::new();
    let mut used = 0usize;
    while selected.len() < item_budget && !candidates.is_empty() {
        let utility = |index: usize| -> f64 {
            let (item, coverage) = &candidates[index];
            if rank_only {
                return -(item.rank as f64);
            }
            let overlap = selected
                .iter()
                .map(|other| source_overlap_ratio(item, other))
                .fold(0.0_f64, f64::max);
            item.score.unwrap_or(0.0).max(0.0) + 0.25 * coverage
                - 0.35 * overlap
                - 0.05 * (item.content.len() as f64 / byte_budget.max(1) as f64)
        };
        let best = (0..candidates.len())
            .max_by(|&a, &b| {
                utility(a)
                    .total_cmp(&utility(b))
                    .then_with(|| candidates[a].0.rank.cmp(&candidates[b].0.rank))
            })
            .unwrap_or(0);
        let (item, _) = candidates.swap_remove(best);
        for mut item in clip_fragment_overlap(item, &selected) {
            let cost = item.content.len();
            if used.saturating_add(cost) > byte_budget {
                continue;
            }
            used += cost;
            item.rank = selected.len() + 1;
            item.selection_reason = Some(ContextSelectionReason::GlobalFill);
            selected.push(item);
        }
    }
    selected
}

/// Select full fragments by query-term coverage, with rank as a tie-break.
/// This is an env-gated candidate: it never changes normal or baseline flows.
fn coverage_budget_select(
    items: Vec<ContextItem>,
    query: &str,
    item_budget: usize,
    byte_budget: usize,
) -> Vec<ContextItem> {
    let terms = {
        let mut terms = query
            .split(|c: char| !c.is_alphanumeric() && c != '_')
            .filter(|word| word.len() >= 3)
            .map(|word| word.to_lowercase())
            .collect::<Vec<_>>();
        terms.sort_unstable();
        terms.dedup();
        terms.truncate(24);
        terms
    };
    let mut candidates = Vec::new();
    let mut seen = HashSet::new();
    for item in items {
        if !seen.insert(context_item_dedupe_key(&item)) {
            continue;
        }
        let lower = item.content.to_lowercase();
        let coverage = if terms.is_empty() {
            0.0
        } else {
            terms
                .iter()
                .filter(|term| lower.contains(term.as_str()))
                .count() as f64
                / terms.len() as f64
        };
        candidates.push((item, coverage));
    }
    candidates.sort_by(|left, right| {
        right
            .1
            .total_cmp(&left.1)
            .then_with(|| left.0.rank.cmp(&right.0.rank))
            .then_with(|| context_item_dedupe_key(&left.0).cmp(&context_item_dedupe_key(&right.0)))
    });
    let mut selected: Vec<ContextItem> = Vec::new();
    let mut used = 0usize;
    for (mut item, _) in candidates {
        if selected.len() >= item_budget {
            break;
        }
        if selected
            .iter()
            .any(|other| source_overlap_ratio(&item, other) >= 0.9)
        {
            continue;
        }
        let cost = item.content.len();
        if used.saturating_add(cost) > byte_budget {
            continue;
        }
        used += cost;
        item.rank = selected.len() + 1;
        item.selection_reason = Some(ContextSelectionReason::GlobalFill);
        selected.push(item);
    }
    selected
}

fn rag_budget_select(items: Vec<ContextItem>) -> Vec<ContextItem> {
    let item_budget = env::var("ZVEC_RAG_ITEM_BUDGET")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(10);
    let byte_budget = env::var("ZVEC_RAG_BYTE_BUDGET")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(24 * 1024);
    let mut ranked = items;
    ranked.sort_by(compare_global_rank);
    let mut selected: Vec<ContextItem> = Vec::new();
    let mut used = 0usize;
    for mut item in ranked {
        if selected.len() >= item_budget {
            break;
        }
        let cost = item.content.len();
        if used.saturating_add(cost) > byte_budget {
            continue;
        }
        let overlap = selected
            .iter()
            .map(|other| range_overlap_ratio(&item.range, &other.range))
            .fold(0.0_f64, f64::max);
        if overlap > 0.5 {
            continue;
        }
        used += cost;
        item.rank = selected.len() + 1;
        item.selection_reason = Some(ContextSelectionReason::GlobalFill);
        selected.push(item);
    }
    selected
}

fn range_overlap_ratio(left: &ContentRange, right: &ContentRange) -> f64 {
    let (Some(ls), Some(le), Some(rs), Some(re)) = (
        left.start_line(),
        left.last_line(),
        right.start_line(),
        right.last_line(),
    ) else {
        return 0.0;
    };
    if ls > re || rs > le {
        return 0.0;
    }
    let overlap = le.min(re).saturating_sub(ls.max(rs)) + 1;
    let union = le.saturating_sub(ls.min(rs)) + 1;
    if union == 0 {
        0.0
    } else {
        overlap as f64 / union as f64
    }
}

fn write_verified_pack(root: &Path, items: &[ContextItem], path: &str) {
    let requests = items
        .iter()
        .filter_map(|item| {
            let (start, end) = match item.content_range {
                ContentRange::Text {
                    start_byte_offset,
                    end_byte_offset,
                    ..
                } => (start_byte_offset, end_byte_offset),
                ContentRange::Byte {
                    start_offset,
                    end_offset,
                } => (
                    usize::try_from(start_offset).ok()?,
                    usize::try_from(end_offset).ok()?,
                ),
                ContentRange::File => return None,
            };
            Some(EvidenceRequest {
                project: "rag".to_owned(),
                relative_path: item.relative_path.clone(),
                expected_sha256: item.file_sha256.clone().unwrap_or_default(),
                start_byte: start,
                end_byte: end,
                expected_start_line: item.content_range.start_line(),
                expected_end_line: item.content_range.end_line(),
            })
        })
        .collect::<Vec<_>>();
    let no_prune = env::var_os("ZVEC_RAG_FRAGMENT_RELEVANCE").is_some()
        || env::var_os("ZVEC_RAG_FRAGMENT_SELECT_V2").is_some();
    let budget = EvidenceBudget {
        max_bytes: if no_prune {
            usize::MAX
        } else {
            env::var("ZVEC_RAG_BYTE_BUDGET")
                .ok()
                .and_then(|value| value.parse::<usize>().ok())
                .unwrap_or(24 * 1024)
        },
        max_items: if no_prune {
            items.len().max(1)
        } else {
            env::var("ZVEC_RAG_ITEM_BUDGET")
                .ok()
                .and_then(|value| value.parse::<usize>().ok())
                .unwrap_or(10)
        },
    };
    if let Ok(pack) = assemble_evidence(root, &requests, budget) {
        if let Ok(text) = serde_json::to_string_pretty(&pack) {
            let _ = fs::write(path, text);
        }
    }
}

fn select_and_rank_context_items(
    items: Vec<ContextItem>,
    coverage_group_ids: &[String],
) -> Vec<ContextItem> {
    let mut deduped: Vec<ContextItem> = Vec::new();
    for item in items {
        let key = context_item_dedupe_key(&item);
        if let Some(existing) = deduped
            .iter_mut()
            .find(|candidate| context_item_dedupe_key(candidate) == key)
        {
            merge_query_group_matches(&mut existing.query_groups, &item.query_groups);
            existing.matched_by = merged_matched_by(&existing.query_groups);
        } else {
            deduped.push(item);
        }
    }
    let mut globally_ranked = deduped.clone();
    globally_ranked.sort_by(compare_global_rank);
    let mut selected = HashSet::<String>::new();
    let mut prioritized = Vec::new();

    for group_id in coverage_group_ids {
        if prioritized.len() >= DEFAULT_CONTEXT_PRIORITY_LIMIT {
            break;
        }
        let mut candidates = deduped
            .iter()
            .filter(|item| !selected.contains(&context_item_dedupe_key(item)))
            .filter(|item| query_group_match(item, group_id).is_some())
            .cloned()
            .collect::<Vec<_>>();
        candidates.sort_by(|left, right| compare_group_rank(left, right, group_id));
        if let Some(mut candidate) = candidates.into_iter().next() {
            selected.insert(context_item_dedupe_key(&candidate));
            candidate.selection_reason = Some(ContextSelectionReason::Coverage);
            candidate.coverage_group = Some(group_id.clone());
            prioritized.push(candidate);
        }
    }
    for mut candidate in globally_ranked.iter().cloned() {
        if prioritized.len() >= DEFAULT_CONTEXT_PRIORITY_LIMIT {
            break;
        }
        let key = context_item_dedupe_key(&candidate);
        if selected.insert(key) {
            candidate.selection_reason = Some(ContextSelectionReason::GlobalFill);
            candidate.coverage_group = None;
            prioritized.push(candidate);
        }
    }
    prioritized.extend(
        globally_ranked
            .into_iter()
            .filter(|item| !selected.contains(&context_item_dedupe_key(item))),
    );
    for (index, item) in prioritized.iter_mut().enumerate() {
        item.rank = index + 1;
    }
    prioritized
}

fn merge_query_group_matches(
    target: &mut Vec<ContextQueryGroupMatch>,
    additional: &[ContextQueryGroupMatch],
) {
    for item in additional {
        if let Some(existing) = target.iter_mut().find(|candidate| candidate.id == item.id) {
            if item.rank < existing.rank {
                *existing = item.clone();
            }
        } else {
            target.push(item.clone());
        }
    }
    target.sort_by_key(|item| context_group_number(&item.id));
}

fn merged_matched_by(matches: &[ContextQueryGroupMatch]) -> MatchedBy {
    let has_fts = matches
        .iter()
        .any(|item| matches!(item.matched_by, MatchedBy::Fts | MatchedBy::FtsAndVector));
    let has_vector = matches
        .iter()
        .any(|item| matches!(item.matched_by, MatchedBy::Vector | MatchedBy::FtsAndVector));
    match (has_fts, has_vector) {
        (true, true) => MatchedBy::FtsAndVector,
        (false, true) => MatchedBy::Vector,
        (true | false, false) => MatchedBy::Fts,
    }
}

fn compare_global_rank(left: &ContextItem, right: &ContextItem) -> std::cmp::Ordering {
    global_rrf_score(right)
        .total_cmp(&global_rrf_score(left))
        .then_with(|| best_group_rank(left).cmp(&best_group_rank(right)))
        .then_with(|| first_group_number(left).cmp(&first_group_number(right)))
        .then_with(|| context_item_dedupe_key(left).cmp(&context_item_dedupe_key(right)))
}

fn global_rrf_score(item: &ContextItem) -> f64 {
    item.query_groups
        .iter()
        .map(|group| 1.0 / (CONTEXT_GROUP_RRF_K + rank_as_f64(group.rank)))
        .sum()
}

fn compare_group_rank(
    left: &ContextItem,
    right: &ContextItem,
    group_id: &str,
) -> std::cmp::Ordering {
    let left_rank = query_group_match(left, group_id).map_or(usize::MAX, |group| group.rank);
    let right_rank = query_group_match(right, group_id).map_or(usize::MAX, |group| group.rank);
    left_rank
        .cmp(&right_rank)
        .then_with(|| compare_global_rank(left, right))
}

fn query_group_match<'item>(
    item: &'item ContextItem,
    group_id: &str,
) -> Option<&'item ContextQueryGroupMatch> {
    item.query_groups.iter().find(|group| group.id == group_id)
}

fn best_group_rank(item: &ContextItem) -> usize {
    item.query_groups
        .iter()
        .map(|group| group.rank)
        .min()
        .unwrap_or(usize::MAX)
}

fn first_group_number(item: &ContextItem) -> usize {
    item.query_groups
        .iter()
        .map(|group| context_group_number(&group.id))
        .min()
        .unwrap_or(usize::MAX)
}

fn context_group_number(id: &str) -> usize {
    id.strip_prefix('Q')
        .and_then(|value| value.parse().ok())
        .unwrap_or(usize::MAX)
}

fn context_item_dedupe_key(item: &ContextItem) -> String {
    if std::env::var_os("ZVEC_RAG_FRAGMENT_FLOW").is_some() {
        if let Some(fragment_id) = &item.fragment_id {
            return format!("fragment:{fragment_id}");
        }
    }
    item.entity_id.as_ref().map_or_else(
        || format!("range:{}:{:?}", item.absolute_path.display(), item.range),
        |id| format!("entity:{id}"),
    )
}

struct ContextItemTarget {
    content: String,
    content_range: ContentRange,
    content_role: ContextContentRole,
    excerpt_range: Option<ContentRange>,
}

fn context_item_target(hit: &SearchHit) -> Result<ContextItemTarget, EngineError> {
    let Some(evidence) = hit.evidence.first() else {
        return Ok(ContextItemTarget {
            content: content_to_text(hit.entity.content()),
            content_range: hit.entity.source_range().into(),
            content_role: ContextContentRole::Source,
            excerpt_range: None,
        });
    };
    let fragment = &evidence.fragment;
    let invalid_fragment = |error| {
        EngineError::storage_failure(format!(
            "invalid search fragment {}: {error}",
            fragment.id.as_str()
        ))
    };
    let (content, excerpt_range) = match (fragment.range, hit.entity.content()) {
        (Range::Full, content) => (content_to_text(content), None),
        (Range::Byte(range), Content::Text(text) | Content::Code(text)) => {
            let start = usize::try_from(range.start_offset()).map_err(|_| {
                invalid_fragment(EngineError::invalid_argument(
                    "fragment start offset exceeds platform limits",
                ))
            })?;
            let end = usize::try_from(range.end_offset()).map_err(|_| {
                invalid_fragment(EngineError::invalid_argument(
                    "fragment end offset exceeds platform limits",
                ))
            })?;
            let content = crate::utils::slice_text(text, start, end).map_err(invalid_fragment)?;
            let lines = crate::utils::line_byte_offsets(text);
            let local = crate::utils::text_range_from_offsets(text, &lines, start, end)
                .map_err(invalid_fragment)?;
            let source = match hit.entity.source_range() {
                Range::Full => local,
                Range::Text(origin) => {
                    crate::utils::map_text_range(local, origin).map_err(invalid_fragment)?
                }
                Range::Byte(_) => {
                    return Err(invalid_fragment(EngineError::invalid_argument(
                        "fragment content coordinates cannot be mapped to this entity source",
                    )));
                }
            };
            (content.to_owned(), Some(Range::Text(source).into()))
        }
        _ => {
            return Err(invalid_fragment(EngineError::invalid_argument(
                "fragments use Full or entity-relative byte ranges for text and code; images require Full",
            )));
        }
    };
    Ok(ContextItemTarget {
        content,
        content_range: excerpt_range
            .clone()
            .unwrap_or_else(|| hit.entity.source_range().into()),
        content_role: ContextContentRole::Source,
        excerpt_range,
    })
}

pub(in crate::pipelines) fn file_freshness_status(
    workspace_root: &Path,
    file: &FileRecord,
) -> ContextItemStatus {
    let absolute_path = workspace_root.join(file.relative_path());
    let metadata = match fs::metadata(&absolute_path) {
        Ok(metadata) => metadata,
        Err(error) => {
            // Missing sources prove drift; inaccessible metadata alone does not.
            return if matches!(
                error.kind(),
                std::io::ErrorKind::NotFound | std::io::ErrorKind::NotADirectory
            ) {
                ContextItemStatus::PossiblyStale
            } else {
                ContextItemStatus::Fresh
            };
        }
    };
    if !metadata.is_file() {
        return ContextItemStatus::PossiblyStale;
    }
    let modified = metadata
        .modified()
        .ok()
        .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
        .and_then(|duration| duration.as_millis().try_into().ok());
    if metadata.len() == file.snapshot().size_bytes
        && modified.is_some()
        && modified == file.snapshot().modified_epoch_ms
    {
        return ContextItemStatus::Fresh;
    }
    if let Some(expected) = &file.snapshot().content_hash
        && let Ok(bytes) = fs::read(&absolute_path)
    {
        return if sha256_hex(&bytes) == *expected {
            ContextItemStatus::Fresh
        } else {
            ContextItemStatus::PossiblyStale
        };
    }
    let metadata_changed = metadata.len() != file.snapshot().size_bytes
        || modified
            .zip(file.snapshot().modified_epoch_ms)
            .is_some_and(|(current, indexed)| current != indexed);
    if metadata_changed {
        ContextItemStatus::PossiblyStale
    } else {
        // Missing index, timestamp or hash information is not proof of an update.
        ContextItemStatus::Fresh
    }
}

fn rank_as_f64(rank: usize) -> f64 {
    f64::from(u32::try_from(rank).unwrap_or(u32::MAX))
}

fn content_to_text(content: &Content) -> String {
    match content {
        Content::Text(text) | Content::Code(text) => text.clone(),
        Content::Image(image) => format!(
            "[image:{} bytes={}]",
            image.format().as_str(),
            image.data().len()
        ),
    }
}

#[cfg(test)]
mod tests {
    use std::{env, path::PathBuf};

    use crate::api::context::{
        ContextOptions,
        options::{ContextRoute, ContextRouteMode},
        result::{
            ContentRange, ContextContentRole, ContextItem, ContextItemKind, ContextItemStatus,
            ContextQueryGroupMatch, ContextQueryGroupRole, ContextSelectionReason, MatchedBy,
        },
    };

    use super::{
        clip_fragment_overlap, coverage_budget_select, fragment_budget_select, fragment_budgets,
        normalize_context_request, select_and_rank_context_items,
    };

    #[test]
    fn best_fragment_preserves_original_content_and_source_range() {
        use crate::{
            domain::{
                ByteRange, Content, Entity, EntityFragment, EntityId, FileId, FileIndexStatus,
                FileRecord, FileSnapshot, FragmentId, Range, TextRange,
            },
            pipelines::indexed_search::pipeline::{SearchEvidence, SearchHit, SearchPlanResult},
        };
        let file_id = FileId::new(1);
        let source = "A中😀\r\nβeta\n尾";
        let source_range = Range::Text(
            TextRange::from_coordinates(9, 9 + source.len(), 2, 4, 2, 3).expect("range"),
        );
        let fragment_range = Range::Byte(ByteRange::new(1, 12).expect("ordered byte offsets"));
        let fragment_source_range = Range::Text(
            TextRange::from_coordinates(10, 21, 2, 3, 3, 2).expect("fragment source range"),
        );
        let content = Content::Text(source.into());
        let entity_id = EntityId::new(file_id, &content, source_range).expect("entity id");
        let fragment = EntityFragment {
            id: FragmentId::new(&entity_id, 0),
            range: fragment_range,
        };
        let full = EntityFragment {
            id: FragmentId::new(&entity_id, 1),
            range: Range::Full,
        };
        let mut hit = SearchHit {
            entity: Entity::new(
                entity_id,
                file_id,
                source_range,
                content,
                None,
                vec![fragment.clone(), full.clone()],
            )
            .expect("entity"),
            file: FileRecord::new(
                file_id,
                crate::domain::SourcePath::new("file.txt").expect("source path"),
                FileSnapshot {
                    size_bytes: (9 + source.len()) as u64,
                    modified_epoch_ms: None,
                    content_hash: None,
                },
                FileIndexStatus::NotIndexed,
            )
            .expect("file"),
            evidence: vec![
                SearchEvidence { fragment },
                SearchEvidence { fragment: full },
            ],
            rank: 1,
            score: 1.0,
            matched_by: MatchedBy::Fts,
            signals: None,
            trace: None,
        };
        let target = super::context_item_target(&hit).expect("fragment content");
        assert_eq!(target.content_role, ContextContentRole::Source);
        assert_eq!(target.content, "中😀\r\nβ");
        assert_eq!(target.excerpt_range, Some(fragment_source_range.into()));
        assert_eq!(target.content_range, fragment_source_range.into());
        assert_eq!(hit.entity.content(), &Content::Text(source.into()));
        let search = SearchPlanResult {
            routes: Vec::new(),
            hits: vec![hit.clone()],
            timings: Vec::new(),
        };
        let request = normalize_context_request(&ContextOptions {
            query: Some("source".into()),
            ..ContextOptions::default()
        })
        .expect("request");
        let items = super::search_plan_to_context_items(
            &search,
            std::path::Path::new("."),
            &request.groups[0],
        )
        .expect("context items");
        assert_eq!(items[0].range, source_range.into());
        assert_eq!(items[0].excerpt_range, Some(fragment_source_range.into()));
        assert_eq!(items[0].content_range, fragment_source_range.into());
        assert_eq!(items[0].content, "中😀\r\nβ");
        hit.evidence.reverse();
        let target = super::context_item_target(&hit).expect("full content");
        assert_eq!(target.content, source);
        assert_eq!(target.content_range, source_range.into());
        assert_eq!(
            target.excerpt_range, None,
            "full fragments do not create an excerpt range"
        );
        hit.evidence[0].fragment.range =
            Range::Byte(ByteRange::new(0, 999).expect("ordered byte offsets"));
        let error = super::context_item_target(&hit)
            .err()
            .expect("invalid fragment must fail");
        assert_eq!(error.code(), crate::EngineError::STORAGE_FAILURE);
    }

    #[test]
    fn markdown_eof_fragments_keep_their_own_source_coordinates() {
        use crate::{
            domain::{
                Content, Entity, EntityFragment, EntityId, FileFormat, FileId, FileIndexStatus,
                FileRecord, FileSnapshot, FragmentId, Range, SourcePath,
            },
            extraction::{ChunkOptions, TextSource, extract_for_indexing},
            pipelines::indexed_search::pipeline::{SearchEvidence, SearchHit},
        };
        for newline in ["", "\n", "\r\n"] {
            let source = format!("# Heading\nprefix {} needle{newline}", "x".repeat(5_000));
            let input = TextSource {
                relative_path: SourcePath::new("sample.md").expect("source path"),
                formats: vec![FileFormat::Markdown],
                text: source.clone(),
            };
            let extracted = extract_for_indexing(&input, ChunkOptions::default())
                .expect("default Markdown extraction")
                .fragments
                .remove(0);
            let file_id = FileId::new(1);
            let id = EntityId::new(file_id, &extracted.content, extracted.source_range)
                .expect("entity ID");
            let fragment = EntityFragment {
                id: FragmentId::new(&id, 0),
                range: extracted.fragments.last().expect("EOF fragment").range,
            };
            let Range::Byte(bytes) = fragment.range else {
                panic!("chunked source")
            };
            let hit = SearchHit {
                entity: Entity::new(
                    id,
                    file_id,
                    extracted.source_range,
                    extracted.content,
                    extracted.metadata,
                    vec![fragment.clone()],
                )
                .expect("entity"),
                file: FileRecord::new(
                    file_id,
                    input.relative_path,
                    FileSnapshot {
                        size_bytes: source.len() as u64,
                        modified_epoch_ms: None,
                        content_hash: None,
                    },
                    FileIndexStatus::NotIndexed,
                )
                .expect("file"),
                evidence: vec![SearchEvidence { fragment }],
                rank: 1,
                score: 1.0,
                matched_by: MatchedBy::Fts,
                signals: None,
                trace: None,
            };
            let target = super::context_item_target(&hit).expect("source fragment");
            let start = usize::try_from(bytes.start_offset()).expect("start");
            let end = usize::try_from(bytes.end_offset()).expect("end");
            assert!(start > "# Heading\nprefix ".len());
            assert_eq!(target.content, source[start..end]);
            assert_eq!(target.content_range.start_line(), Some(2));
            assert_eq!(target.content_range.last_line(), Some(2));
            assert_eq!(target.excerpt_range, Some(target.content_range));
            assert_eq!(target.content.ends_with('\n'), !newline.is_empty());
            assert!(matches!(hit.entity.content(), Content::Text(_)));
        }
    }

    #[test]
    #[expect(
        clippy::too_many_lines,
        reason = "The complete fixture keeps relocation, subdirectory lookup, and freshness checks in one scenario"
    )]
    fn resolves_context_paths_and_freshness_after_workspace_relocation() {
        use crate::{
            domain::{
                Content, Entity, EntityFragment, EntityId, FileId, FileIndexStatus, FileRecord,
                FileSnapshot, FragmentId, IndexDescriptor, IndexState, Range, TextRange, Workspace,
                WorkspaceLayout,
                model::{EmbeddingMetric, EmbeddingModelInfo},
            },
            pipelines::indexed_search::pipeline::{SearchHit, SearchPlanResult},
            utils::sha256_hex,
        };

        let directory = tempfile::tempdir().expect("temporary directory");
        let base = directory.path().canonicalize().expect("source parent");
        let original_root = base.join("original");
        let moved_root = base.join("moved");
        std::fs::create_dir_all(original_root.join("src")).expect("source directory");
        let content = "source contents";
        std::fs::write(original_root.join("src/file.txt"), content).expect("source file");
        let source = FileRecord::new(
            FileId::new(1),
            crate::domain::SourcePath::new("src/file.txt").expect("source path"),
            FileSnapshot {
                size_bytes: content.len() as u64,
                modified_epoch_ms: None,
                content_hash: Some(sha256_hex(content.as_bytes())),
            },
            FileIndexStatus::NotIndexed,
        )
        .expect("file");
        let mut file = source.clone();
        file.set_index_status(FileIndexStatus::Indexed {
            indexed_epoch_ms: 0,
            entity_count: 1,
        })
        .expect("indexed file");
        let source_range = Range::Text(
            TextRange::from_coordinates(0, content.len(), 1, 1, 0, content.len()).expect("range"),
        );
        let content = Content::Text(content.to_owned());
        let id = EntityId::new(source.id(), &content, source_range).expect("entity id");
        let fragment = EntityFragment {
            id: FragmentId::new(&id, 0),
            range: Range::Full,
        };
        let search = SearchPlanResult {
            routes: Vec::new(),
            hits: vec![SearchHit {
                entity: Entity::new(id, source.id(), source_range, content, None, vec![fragment])
                    .expect("entity"),
                file: file.clone(),
                evidence: Vec::new(),
                rank: 1,
                score: 1.0,
                matched_by: MatchedBy::Fts,
                signals: None,
                trace: None,
            }],
            timings: Vec::new(),
        };
        let request = normalize_context_request(&ContextOptions {
            query: Some("source".to_owned()),
            ..ContextOptions::default()
        })
        .expect("request");
        let index = IndexState::Enabled(Box::new(
            IndexDescriptor::new(
                EmbeddingModelInfo::new(
                    crate::domain::model::ModelInfo::new(
                        "local".to_owned(),
                        "fixture".to_owned(),
                        [
                            crate::domain::ContentKind::Text,
                            crate::domain::ContentKind::Code,
                            crate::domain::ContentKind::Image,
                        ],
                        32,
                    )
                    .expect("valid model metadata"),
                    2,
                    EmbeddingMetric::Cosine,
                    None,
                    None,
                )
                .expect("valid embedding metadata"),
                std::collections::BTreeMap::new(),
                std::collections::BTreeMap::new(),
                crate::domain::FTS_CONFIG,
            )
            .expect("index descriptor"),
        ));

        let generation = uuid::Uuid::new_v4().to_string();
        for root in [&original_root, &moved_root] {
            if root == &moved_root {
                std::fs::rename(&original_root, &moved_root).expect("move workspace");
            }
            let workspace = Workspace::new(
                root,
                WorkspaceLayout::new(root.join(".zvec-grep")).expect("layout"),
                crate::domain::ScanRules::default(),
                Some(crate::workspace::CURRENT_INDEX_VERSION),
                Some(generation.clone()),
                0,
                0,
                index.clone(),
            )
            .expect("workspace");
            // The request may start in a subdirectory; source paths retain the workspace base.
            let requested_root = root.join("src");
            let result = super::build_context_result(
                &requested_root,
                &workspace,
                &request,
                &request.groups,
                vec![search.clone()],
            )
            .expect("context result");
            assert_eq!(result.root, requested_root);
            let workspace_index = result.workspace_index.as_ref().expect("workspace index");
            assert_eq!(workspace_index.root, *root);
            assert_eq!(workspace_index.path, root.join(".zvec-grep"));
            assert_eq!(result.items[0].absolute_path, root.join("src/file.txt"));
            assert_eq!(result.items[0].relative_path, PathBuf::from("src/file.txt"));
            assert_eq!(result.items[0].status, ContextItemStatus::Fresh);
        }
        assert_eq!(
            super::file_freshness_status(&original_root, &file),
            ContextItemStatus::PossiblyStale
        );
        std::fs::write(moved_root.join("src/file.txt"), "changed contents")
            .expect("change moved source");
        assert_eq!(
            super::file_freshness_status(&moved_root, &file),
            ContextItemStatus::PossiblyStale
        );
        let mut future_indexed = file;
        future_indexed
            .set_index_status(FileIndexStatus::Indexed {
                indexed_epoch_ms: u64::MAX,
                entity_count: 1,
            })
            .expect("future indexed file");
        assert_eq!(
            super::file_freshness_status(&moved_root, &future_indexed),
            ContextItemStatus::PossiblyStale,
            "an index clock ahead of file mtime does not prove the source is unchanged"
        );
    }

    #[test]
    fn source_freshness_requires_a_snapshot_mismatch_or_missing_file() {
        use crate::domain::{FileId, FileIndexStatus, FileRecord, FileSnapshot, SourcePath};

        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("source.txt");
        std::fs::write(&path, "source contents").expect("source");
        let mut file = FileRecord::new(
            FileId::new(1),
            SourcePath::new("source.txt").expect("source path"),
            FileSnapshot {
                size_bytes: 15,
                modified_epoch_ms: None,
                content_hash: None,
            },
            FileIndexStatus::NotIndexed,
        )
        .expect("file");
        assert_eq!(
            super::file_freshness_status(directory.path(), &file),
            ContextItemStatus::Fresh,
            "missing index/mtime/hash proof is not evidence of an updated file"
        );
        file.set_snapshot(FileSnapshot {
            content_hash: Some(crate::utils::sha256_hex(b"source contents")),
            ..file.snapshot().clone()
        })
        .expect("file snapshot");
        assert_eq!(
            super::file_freshness_status(directory.path(), &file),
            ContextItemStatus::Fresh
        );
        std::fs::write(&path, "edited contents").expect("same-size update");
        assert_eq!(
            super::file_freshness_status(directory.path(), &file),
            ContextItemStatus::PossiblyStale
        );
        std::fs::remove_file(&path).expect("remove source");
        assert_eq!(
            super::file_freshness_status(directory.path(), &file),
            ContextItemStatus::PossiblyStale
        );
    }

    #[test]
    fn normalizes_primary_and_supplemental_query_groups_like_main() {
        let request = normalize_context_request(&ContextOptions {
            query: Some(" alpha ".to_owned()),
            queries: vec![" beta ".to_owned()],
            routes: vec![ContextRoute {
                mode: ContextRouteMode::Fts,
                query: " gamma ".to_owned(),
            }],
            ..ContextOptions::default()
        })
        .expect("normalized context request");

        assert_eq!(request.display_query, "alpha | beta");
        assert_eq!(request.groups.len(), 3);
        assert_eq!(request.groups[0].id, "Q1");
        assert_eq!(request.groups[0].role, ContextQueryGroupRole::Primary);
        assert_eq!(request.groups[0].routes.len(), 2);
        assert_eq!(request.groups[2].id, "Q3");
        assert_eq!(request.groups[2].role, ContextQueryGroupRole::Supplemental);
        assert_eq!(request.routes.len(), 5);
    }

    #[test]
    fn explicit_fts_route_without_query_does_not_add_a_vector_route() {
        let request = normalize_context_request(&ContextOptions {
            routes: vec![ContextRoute {
                mode: ContextRouteMode::Fts,
                query: "alpha".to_owned(),
            }],
            ..ContextOptions::default()
        })
        .expect("normalized context request");

        assert_eq!(request.groups.len(), 1);
        assert_eq!(request.groups[0].role, ContextQueryGroupRole::Supplemental);
        assert_eq!(request.groups[0].routes.len(), 1);
        assert_eq!(request.groups[0].routes[0].mode, ContextRouteMode::Fts);
        assert!(
            !request
                .routes
                .iter()
                .any(|route| route.mode == ContextRouteMode::Vector),
            "explicit FTS-only routes must not gain an implicit vector route"
        );
    }

    #[test]
    fn deduplicates_cross_group_hits_and_preserves_primary_coverage() {
        let items = vec![
            item("shared", group("Q1", 1, MatchedBy::Fts)),
            item("q1-only", group("Q1", 2, MatchedBy::Fts)),
            item("shared", group("Q2", 1, MatchedBy::Vector)),
            item("q2-only", group("Q2", 2, MatchedBy::Vector)),
        ];

        let selected = select_and_rank_context_items(items, &["Q1".to_owned(), "Q2".to_owned()]);

        assert_eq!(selected.len(), 3);
        assert_eq!(selected[0].entity_id.as_deref(), Some("shared"));
        assert_eq!(selected[0].query_groups.len(), 2);
        assert_eq!(selected[0].matched_by, MatchedBy::FtsAndVector);
        assert_eq!(
            selected[0].selection_reason,
            Some(ContextSelectionReason::Coverage)
        );
        assert_eq!(selected[0].coverage_group.as_deref(), Some("Q1"));
        assert_eq!(selected[1].entity_id.as_deref(), Some("q2-only"));
        assert_eq!(selected[1].coverage_group.as_deref(), Some("Q2"));
        assert_eq!(
            selected.iter().map(|item| item.rank).collect::<Vec<_>>(),
            [1, 2, 3]
        );
    }

    fn group(id: &str, rank: usize, matched_by: MatchedBy) -> ContextQueryGroupMatch {
        ContextQueryGroupMatch {
            id: id.to_owned(),
            query: id.to_owned(),
            role: ContextQueryGroupRole::Primary,
            rank,
            matched_by,
        }
    }

    fn item(id: &str, query_group: ContextQueryGroupMatch) -> ContextItem {
        ContextItem {
            kind: ContextItemKind::IndexedEntity,
            rank: query_group.rank,
            absolute_path: PathBuf::from("/workspace/source.rs"),
            relative_path: PathBuf::from("source.rs"),
            range: ContentRange::Text {
                start_line: 1,
                end_line: 1,
                start_byte_offset: 0,
                end_byte_offset: 1,
                start_byte_column: 0,
                end_byte_column: 1,
            },
            content_range: ContentRange::Text {
                start_line: 1,
                end_line: 1,
                start_byte_offset: 0,
                end_byte_offset: 1,
                start_byte_column: 0,
                end_byte_column: 1,
            },
            excerpt_range: None,
            match_ranges: Vec::new(),
            content: id.to_owned(),
            outline: None,
            content_role: Some(ContextContentRole::Source),
            status: ContextItemStatus::Fresh,
            score: Some(1.0),
            matched_by: query_group.matched_by,
            metadata: None,
            entity_id: Some(id.to_owned()),
            fragment_id: None,
            file_sha256: None,
            container: None,
            trace: None,
            query_groups: vec![query_group],
            selection_reason: None,
            coverage_group: None,
        }
    }
    #[test]
    fn fragment_selection_clips_only_duplicate_source_lines() {
        use crate::api::context::result::ContextQueryGroupRole;
        let group = ContextQueryGroupMatch {
            id: "Q1".to_owned(),
            query: "Q1".to_owned(),
            role: ContextQueryGroupRole::Primary,
            rank: 1,
            matched_by: MatchedBy::Fts,
        };
        let mut first = item("A", group.clone());
        first.relative_path = PathBuf::from("same.rs");
        first.file_sha256 = Some("same-sha".to_owned());
        first.content = "alpha\nbravo\n".to_owned();
        first.content_range = ContentRange::Text {
            start_line: 1,
            end_line: 3,
            start_byte_offset: 0,
            end_byte_offset: first.content.len(),
            start_byte_column: 0,
            end_byte_column: 0,
        };
        first.score = Some(2.0);
        let mut second = item("B", group);
        second.relative_path = PathBuf::from("same.rs");
        second.file_sha256 = Some("same-sha".to_owned());
        second.fragment_id = Some("frag-b".to_owned());
        second.content = "bravo\ncharlie\n".to_owned();
        second.content_range = ContentRange::Text {
            start_line: 2,
            end_line: 4,
            start_byte_offset: 6,
            end_byte_offset: 6 + second.content.len(),
            start_byte_column: 0,
            end_byte_column: 0,
        };
        second.score = Some(0.9);
        let selected = fragment_budget_select(vec![first, second], "bravo charlie");
        assert_eq!(selected.len(), 2);
        let kept = selected
            .iter()
            .find(|item| item.fragment_id.as_deref() == Some("frag-b"))
            .expect("clipped fragment");
        assert!(kept.content.contains("charlie"));
        assert!(!kept.content.contains("bravo"));
        assert_eq!(kept.content_range.start_line(), Some(3));
    }

    #[test]
    fn fragment_selection_respects_source_identity() {
        use crate::api::context::result::ContextQueryGroupRole;
        let group = ContextQueryGroupMatch {
            id: "Q1".to_owned(),
            query: "Q1".to_owned(),
            role: ContextQueryGroupRole::Primary,
            rank: 1,
            matched_by: MatchedBy::Fts,
        };
        let mut first = item("A", group.clone());
        first.relative_path = PathBuf::from("same.rs");
        first.file_sha256 = Some("sha-a".to_owned());
        first.content = "alpha\n".to_owned();
        first.content_range = ContentRange::Text {
            start_line: 1,
            end_line: 2,
            start_byte_offset: 0,
            end_byte_offset: first.content.len(),
            start_byte_column: 0,
            end_byte_column: 0,
        };
        let mut second = item("B", group);
        second.relative_path = PathBuf::from("same.rs");
        second.file_sha256 = Some("sha-b".to_owned());
        second.fragment_id = Some("frag-b".to_owned());
        second.content = "alpha\n".to_owned();
        second.content_range = first.content_range.clone();
        let selected = fragment_budget_select(vec![first, second], "alpha");
        assert_eq!(selected.len(), 2);
        assert!(
            selected
                .iter()
                .any(|item| item.fragment_id.as_deref() == Some("frag-b")
                    && item.content == "alpha\n")
        );
    }

    #[test]
    fn fragment_clipping_keeps_both_non_overlapping_remainders() {
        use crate::api::context::result::ContextQueryGroupRole;
        let group = ContextQueryGroupMatch {
            id: "Q1".to_owned(),
            query: "Q1".to_owned(),
            role: ContextQueryGroupRole::Primary,
            rank: 1,
            matched_by: MatchedBy::Fts,
        };
        let mut first = item("A", group.clone());
        first.relative_path = PathBuf::from("same.rs");
        first.file_sha256 = Some("same-sha".to_owned());
        first.content = "line4\nline5\n".to_owned();
        first.content_range = ContentRange::Text {
            start_line: 4,
            end_line: 6,
            start_byte_offset: 18,
            end_byte_offset: 30,
            start_byte_column: 0,
            end_byte_column: 0,
        };
        let mut second = item("B", group);
        second.relative_path = PathBuf::from("same.rs");
        second.file_sha256 = Some("same-sha".to_owned());
        second.fragment_id = Some("frag-b".to_owned());
        second.content = "line1\nline2\nline3\nline4\nline5\nline6\n".to_owned();
        second.content_range = ContentRange::Text {
            start_line: 1,
            end_line: 7,
            start_byte_offset: 0,
            end_byte_offset: second.content.len(),
            start_byte_column: 0,
            end_byte_column: 0,
        };
        let pieces = clip_fragment_overlap(second, &[first]);
        assert_eq!(pieces.len(), 2);
        assert_eq!(pieces[0].content, "line1\nline2\nline3\n");
        assert_eq!(pieces[0].content_range.start_line(), Some(1));
        assert_eq!(pieces[0].content_range.end_line(), Some(4));
        assert_eq!(pieces[1].content, "line6\n");
        assert_eq!(pieces[1].content_range.start_line(), Some(6));
        assert_eq!(pieces[1].content_range.end_line(), Some(7));
    }

    #[test]
    fn coverage_selection_prefers_query_terms_and_keeps_whole_fragments() {
        use crate::api::context::result::ContextQueryGroupRole;
        let group = ContextQueryGroupMatch {
            id: "Q1".to_owned(),
            query: "Q1".to_owned(),
            role: ContextQueryGroupRole::Primary,
            rank: 1,
            matched_by: MatchedBy::Fts,
        };
        let mut low = item("A", group.clone());
        low.rank = 1;
        low.file_sha256 = Some("sha-a".to_owned());
        low.content = "unrelated\n".to_owned();
        let mut high = item("B", group);
        high.rank = 9;
        high.file_sha256 = Some("sha-b".to_owned());
        high.content = "matcher value\n".to_owned();
        let selected = coverage_budget_select(vec![low, high], "matcher value", 64, 24 * 1024);
        assert_eq!(selected.len(), 2);
        assert_eq!(selected[0].content, "matcher value\n");
        assert_eq!(selected[0].rank, 1);
    }

    #[test]
    fn fragment_budget_defaults_to_byte_primary_safeguard() {
        if env::var_os("ZVEC_RAG_ITEM_BUDGET").is_some()
            || env::var_os("ZVEC_RAG_BYTE_BUDGET").is_some()
        {
            return;
        }
        let (items, bytes) = fragment_budgets();
        assert!(
            items >= 64,
            "item count must be a safeguard, not a delivery cutoff"
        );
        assert_eq!(bytes, 24 * 1024);
    }
}
