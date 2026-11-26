//! Deterministic rule ranking over the complete recalled entity pool.
mod paths;
mod query;
mod support;

use std::collections::HashMap;

use super::pipeline::{ResolvedSearchRoute, SearchHit};
use crate::api::context::options::ContextRouteMode as SearchRouteMode;
use crate::{
    api::context::{options::QueryFilter, result::SearchRankingTrace},
    domain::{Content, EntityMetadata, FileId, Range},
};
use paths::Roles;
use query::QueryFeatures;

const FILE_DECAY: f64 = 0.50;

/// Internal policy values also provide a fixed-pool ablation seam for tests.
#[derive(Clone, Copy)]
struct Policy {
    name: &'static str,
    path_weight: f64,
    weak_names: bool,
    definition_intent: bool,
    support_weight: f64,
    bonus_cap: f64,
}

impl Policy {
    const CURRENT: Self = Self {
        name: "code-rules",
        path_weight: 0.35,
        weak_names: true,
        definition_intent: true,
        support_weight: 0.15,
        bonus_cap: 1.25,
    };

    #[cfg(test)]
    const BASELINE: Self = Self {
        name: "code-rules-baseline",
        path_weight: 0.0,
        weak_names: false,
        definition_intent: false,
        support_weight: 0.0,
        bonus_cap: 0.50,
    };
}

struct Scored {
    hit: SearchHit,
    base: f64,
    static_score: f64,
    symbol_match: &'static str,
    symbol_bonus: f64,
    definition_bonus: f64,
    path_bonus: f64,
    support_bonus: f64,
    support_count: u32,
    roles: Roles,
    overrides: Roles,
}

/// Rank without widening recall, inspecting source files, or loading a model.
pub(super) fn rank(
    hits: Vec<SearchHit>,
    routes: &[ResolvedSearchRoute],
    filter: &QueryFilter,
    maximum_rrf: f64,
    limit: usize,
) -> Vec<SearchHit> {
    rank_with_policy(hits, routes, filter, maximum_rrf, limit, Policy::CURRENT)
}

pub(super) fn rank_fragments(
    hits: Vec<SearchHit>,
    routes: &[ResolvedSearchRoute],
    maximum_rrf: f64,
) -> Vec<SearchHit> {
    if hits.is_empty() {
        return Vec::new();
    }
    let mut queries = routes
        .iter()
        .map(|route| route.query.as_str())
        .collect::<Vec<_>>();
    queries.sort_unstable();
    queries.dedup();
    let query = queries.join("\n");
    let terms = query_terms(&query);
    let fts_max = hits
        .iter()
        .map(|hit| route_score(hit, SearchRouteMode::Fts))
        .fold(0.0_f64, f64::max);
    let vector_max = hits
        .iter()
        .map(|hit| route_score(hit, SearchRouteMode::Vector))
        .fold(0.0_f64, f64::max);
    let query_features = QueryFeatures::new(&query);
    let mut scored = hits
        .into_iter()
        .map(|hit| {
            let rrf = (hit.score / maximum_rrf.max(f64::EPSILON)).clamp(0.0, 1.0);
            let fts = if fts_max > f64::EPSILON {
                route_score(&hit, SearchRouteMode::Fts) / fts_max
            } else {
                0.0
            };
            let vector = if vector_max > f64::EPSILON {
                route_score(&hit, SearchRouteMode::Vector) / vector_max
            } else {
                0.0
            };
            let coverage = term_coverage(&terms, fragment_text(&hit));
            let (symbol_match, symbol_bonus, definition_bonus) = match hit.entity.metadata() {
                Some(EntityMetadata::Code(code)) => {
                    query_features.symbol_bonus_with(code, Policy::CURRENT)
                }
                _ => ("none", 0.0, 0.0),
            };
            let metadata_bonus = (symbol_bonus + definition_bonus).min(0.08);
            let relevance =
                0.45 * rrf + 0.25 * fts + 0.20 * vector + 0.10 * coverage + metadata_bonus;
            (
                hit,
                relevance,
                rrf,
                coverage,
                symbol_match,
                symbol_bonus,
                definition_bonus,
            )
        })
        .collect::<Vec<_>>();
    scored.sort_by(|left, right| {
        right
            .1
            .total_cmp(&left.1)
            .then_with(|| left.0.rank.cmp(&right.0.rank))
            .then_with(|| {
                left.0
                    .entity
                    .id()
                    .as_str()
                    .cmp(right.0.entity.id().as_str())
            })
    });
    scored
        .into_iter()
        .enumerate()
        .map(
            |(
                index,
                (mut hit, relevance, rrf, coverage, symbol_match, symbol_bonus, definition_bonus),
            )| {
                hit.rank = index + 1;
                hit.score = relevance;
                if let Some(trace) = hit.trace.as_mut() {
                    trace.ranking = Some(SearchRankingTrace {
                        policy: "fragment-relevance".to_owned(),
                        query_features: query_features.describe(),
                        normalized_base: rrf,
                        symbol_match: symbol_match.to_owned(),
                        symbol_bonus,
                        definition_bonus,
                        path_bonus: 0.0,
                        file_support_bonus: coverage,
                        file_support_count: 0,
                        path_roles: Vec::new(),
                        path_overrides: Vec::new(),
                        path_factor: 1.0,
                        static_score: relevance,
                        file_count_at_selection: 0,
                        diversity_factor: 1.0,
                        score: relevance,
                        rank: hit.rank,
                    });
                    trace.final_selection.returned_by_limit = false;
                    trace.final_selection.cutoff_rank = usize::MAX;
                }
                hit
            },
        )
        .collect()
}

fn route_score(hit: &SearchHit, mode: SearchRouteMode) -> f64 {
    hit.trace.as_ref().map_or(0.0, |trace| {
        trace
            .recall
            .iter()
            .filter(|recall| recall.path == mode)
            .filter_map(|recall| recall.score)
            .fold(0.0_f64, f64::max)
    })
}

fn query_terms(query: &str) -> Vec<String> {
    let mut terms = query
        .split(|c: char| !c.is_alphanumeric() && c != '_')
        .filter(|word| word.len() >= 3)
        .map(|word| word.to_lowercase())
        .collect::<Vec<_>>();
    terms.sort_unstable();
    terms.dedup();
    terms.truncate(24);
    terms
}

fn term_coverage(terms: &[String], content: &str) -> f64 {
    if terms.is_empty() {
        return 0.0;
    }
    let lower = content.to_lowercase();
    let matched = terms
        .iter()
        .filter(|term| lower.contains(term.as_str()))
        .count();
    matched as f64 / terms.len() as f64
}

fn fragment_text(hit: &SearchHit) -> &str {
    let content = match hit.entity.content() {
        Content::Text(text) | Content::Code(text) => text.as_str(),
        Content::Image(_) => return "",
    };
    let Some(fragment) = hit.evidence.first().map(|evidence| &evidence.fragment) else {
        return content;
    };
    match fragment.range {
        Range::Full => content,
        Range::Byte(range) => {
            let Ok(start) = usize::try_from(range.start_offset()) else {
                return content;
            };
            let Ok(end) = usize::try_from(range.end_offset()) else {
                return content;
            };
            crate::utils::slice_text(content, start, end).unwrap_or(content)
        }
        Range::Text(_) => content,
    }
}

fn rank_with_policy(
    hits: Vec<SearchHit>,
    routes: &[ResolvedSearchRoute],
    filter: &QueryFilter,
    maximum_rrf: f64,
    limit: usize,
    policy: Policy,
) -> Vec<SearchHit> {
    if hits.is_empty() || limit == 0 {
        return Vec::new();
    }
    let mut queries = routes.iter().map(|r| r.query.as_str()).collect::<Vec<_>>();
    queries.sort_unstable();
    queries.dedup();
    let query = QueryFeatures::new(&queries.join("\n"));
    let mut overrides = Roles::overrides(&query, filter);
    let roles = hits
        .iter()
        .map(|hit| Roles::from_path(&hit.file.relative_path().display().to_string()))
        .collect::<Vec<_>>();
    // A common dependency prior carries no information within this search scope.
    overrides.dependency |= roles.iter().all(|r| r.dependency);
    let mut scored = hits
        .into_iter()
        .zip(roles)
        .map(|(hit, roles)| {
            let (symbol_match, symbol_bonus, definition_bonus) = match hit.entity.metadata() {
                Some(EntityMetadata::Code(code)) => query.symbol_bonus_with(code, policy),
                _ => ("none", 0.0, 0.0),
            };
            let base = hit.score / maximum_rrf;
            let mut path_bonus = policy.path_weight
                * query.path_affinity(&hit.file.relative_path().display().to_string());
            // Weak lexical coverage is one signal even when repeated in the path.
            if symbol_match == "tokens" {
                path_bonus = (path_bonus - symbol_bonus).max(0.0);
            }
            let static_score = (base
                + (symbol_bonus + definition_bonus + path_bonus).min(policy.bonus_cap))
                * roles.factor(overrides);
            Scored {
                hit,
                base,
                static_score,
                symbol_match,
                symbol_bonus,
                definition_bonus,
                path_bonus,
                support_bonus: 0.0,
                support_count: 0,
                roles,
                overrides,
            }
        })
        .collect::<Vec<_>>();
    if policy.support_weight > 0.0 {
        support::apply(&mut scored, policy.support_weight);
    }
    select(scored, &query, limit, policy)
}

fn select(
    mut scored: Vec<Scored>,
    query: &QueryFeatures,
    limit: usize,
    policy: Policy,
) -> Vec<SearchHit> {
    let file_decay = if std::env::var_os("ZVEC_RAG_NO_DIVERSITY").is_some()
        || std::env::var_os("ZVEC_RAG_FRAGMENT_FLOW").is_some()
    {
        0.0
    } else {
        FILE_DECAY
    };
    let single_file = scored
        .iter()
        .all(|s| s.hit.file.id() == scored[0].hit.file.id());
    let mut selected_per_file = HashMap::<FileId, u32>::new();
    let mut output = Vec::with_capacity(limit.min(scored.len()));
    while output.len() < limit && !scored.is_empty() {
        let effective = |item: &Scored| {
            let count = if single_file {
                0
            } else {
                selected_per_file
                    .get(&item.hit.file.id())
                    .copied()
                    .unwrap_or(0)
            };
            item.static_score / (1.0 + file_decay * f64::from(count))
        };
        let best = scored
            .iter()
            .enumerate()
            .min_by(|(_, a), (_, b)| {
                effective(b)
                    .total_cmp(&effective(a))
                    .then_with(|| a.hit.rank.cmp(&b.hit.rank))
                    .then_with(|| a.hit.entity.id().as_str().cmp(b.hit.entity.id().as_str()))
            })
            .map_or(0, |(index, _)| index);
        let mut item = scored.swap_remove(best);
        let count = selected_per_file.entry(item.hit.file.id()).or_default();
        let factor = if single_file {
            1.0
        } else {
            1.0 / (1.0 + file_decay * f64::from(*count))
        };
        item.hit.score = item.static_score * factor;
        item.hit.rank = output.len() + 1;
        if let Some(trace) = item.hit.trace.as_mut() {
            trace.ranking = Some(SearchRankingTrace {
                policy: policy.name.to_owned(),
                query_features: query.describe(),
                normalized_base: item.base,
                symbol_match: item.symbol_match.to_owned(),
                symbol_bonus: item.symbol_bonus,
                definition_bonus: item.definition_bonus,
                path_bonus: item.path_bonus,
                file_support_bonus: item.support_bonus,
                file_support_count: item.support_count,
                path_roles: item.roles.names(),
                path_overrides: item.overrides.names(),
                path_factor: item.roles.factor(item.overrides),
                static_score: item.static_score,
                file_count_at_selection: *count,
                diversity_factor: factor,
                score: item.hit.score,
                rank: item.hit.rank,
            });
            trace.final_selection.returned_by_limit = true;
            trace.final_selection.cutoff_rank = limit;
        }
        *count = count.saturating_add(1);
        output.push(item.hit);
    }
    output
}

#[cfg(test)]
mod replay;
#[cfg(test)]
mod tests;
