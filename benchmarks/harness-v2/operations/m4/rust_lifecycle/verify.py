"""Pure verification helpers for the Rust lifecycle pair."""
from __future__ import annotations

from typing import Any


def query_latency_arm_reasons(role: str, report: dict[str, Any]) -> list[str]:
    reasons = []
    if report.get("arm") != role:
        reasons.append("arm_role_mismatch")
    if report.get("concurrent_query_latency_p95_comparable") is not True:
        reasons.append("arm_report_marks_p95_non_comparable")
    required_counts = (
        "concurrent_query_total",
        "concurrent_query_offered_slots",
        "concurrent_query_accepted_slots",
        "concurrent_query_started",
        "concurrent_query_dropped_slots",
        "concurrent_query_slots_not_offered_by_cap",
        "concurrent_query_schedule_slots_missed",
        "concurrent_query_failures",
        "concurrent_query_sampling_capped_passes",
        "concurrent_query_max_schedule_lag_us",
        "reader_query_interval_ms",
    )
    values: dict[str, int] = {}
    for key in required_counts:
        value = report.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            reasons.append("missing_or_invalid_offer_telemetry")
            return reasons
        values[key] = value
    if values["reader_query_interval_ms"] == 0:
        reasons.append("invalid_query_interval")
    p95 = report.get("concurrent_query_p95_us")
    if isinstance(p95, bool) or not isinstance(p95, int) or p95 < 0:
        reasons.append("missing_or_invalid_p95")
    if values["concurrent_query_total"] < 20:
        reasons.append("fewer_than_20_successful_queries")
    if not (
        values["concurrent_query_offered_slots"]
        == values["concurrent_query_accepted_slots"]
        == values["concurrent_query_started"]
        == values["concurrent_query_total"]
    ):
        reasons.append("offers_not_all_accepted_started_and_completed")
    if (
        values["concurrent_query_dropped_slots"]
        or values["concurrent_query_slots_not_offered_by_cap"]
    ):
        reasons.append("offers_dropped_or_capped")
    if values["concurrent_query_schedule_slots_missed"]:
        reasons.append("scheduled_starts_missed")
    if values["concurrent_query_failures"]:
        reasons.append("query_failures")
    if values["concurrent_query_sampling_capped_passes"]:
        reasons.append("sampling_capped")
    if (
        values["concurrent_query_max_schedule_lag_us"]
        > values["reader_query_interval_ms"] * 1000
    ):
        reasons.append("scheduler_lag_exceeds_one_interval")
    return reasons


def query_latency_pair_gate(arm_results: list[dict[str, Any]]) -> dict[str, Any]:
    by_role = {item.get("role"): item.get("workload_report") for item in arm_results}
    native, m4 = by_role.get("native"), by_role.get("m4")
    if not isinstance(native, dict) or not isinstance(m4, dict):
        return {
            "comparable": False,
            "reasons": ["missing_native_or_m4_report"],
            "p95_us_by_role": {},
        }
    protocol_fields = (
        "reader_query_interval_ms",
        "reader_query_max_samples_per_pass",
        "reader_query_queue_capacity",
        "query_filter",
        "query_route",
    )

    def valid_protocol(report: dict[str, Any]) -> bool:
        for key in (
            "reader_query_interval_ms",
            "reader_query_max_samples_per_pass",
            "reader_query_queue_capacity",
        ):
            value = report.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                return False
        return all(
            isinstance(report.get(key), str) and bool(report[key].strip())
            for key in ("query_filter", "query_route")
        )

    protocols_valid = valid_protocol(native) and valid_protocol(m4)
    protocol_equal = protocols_valid and all(
        native[key] == m4[key] for key in protocol_fields
    )
    reasons = [] if protocols_valid else ["missing_or_invalid_offered_load_protocol"]
    if protocols_valid and not protocol_equal:
        reasons.append("offered_load_protocol_mismatch")
    native_reasons = query_latency_arm_reasons("native", native)
    m4_reasons = query_latency_arm_reasons("m4", m4)
    reasons.extend(f"native:{reason}" for reason in native_reasons)
    reasons.extend(f"m4:{reason}" for reason in m4_reasons)
    return {
        "comparable": protocol_equal and not native_reasons and not m4_reasons,
        "offered_load_protocol_equal": protocol_equal,
        "reasons": reasons,
        "p95_us_by_role": {
            "native": native.get("concurrent_query_p95_us"),
            "m4": m4.get("concurrent_query_p95_us"),
        },
    }
