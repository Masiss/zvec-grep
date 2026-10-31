"""Declarative disposition registry for M4 operation families.

The registry does not launch anything. It records which policy section owns an
operation, whether V2 execution is authorized, and why a family is historical
or research-only. Policy text remains the source of truth; this module only
fails closed when the two disagree.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Status = Literal["active", "research_only", "historical_only", "unregistered"]


@dataclass(frozen=True)
class OperationDescriptor:
    name: str
    policy_section: str | None
    purpose: str | None
    status: Status
    execution_allowed: bool
    runner: str | None
    notes: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DESCRIPTORS: tuple[OperationDescriptor, ...] = (
    OperationDescriptor(
        "rust_lifecycle",
        "rust_pipeline_lifecycle_pair",
        "m4-rust-full-lifecycle-pair",
        "active",
        True,
        "benchmarks/harness-v2/operations/m4/rust_lifecycle/executor.py",
        "P3 adapter; CLI shim remains in research/m4-native/tools.",
    ),
    OperationDescriptor(
        "lock_contention_microbench",
        "lock_contention_microbench",
        "m4-lock-contention-microbench",
        "active",
        True,
        "research/m4-native/tools/run_lock_contention_microbench.py",
        "Legacy runner remains the execution owner; V2 owns descriptor/receipt.",
    ),
    OperationDescriptor(
        "write_amplification",
        "immutable_write_amplification_pair",
        "immutable-write-amplification-pair",
        "historical_only",
        False,
        None,
        "No tracked runner references this purpose; retain receipts as historical.",
    ),
    OperationDescriptor(
        "shared_collection",
        "shared_collection_pair",
        "m4-shared-collection-pair",
        "research_only",
        False,
        "research/m4-native/tools/run_shared_collection_pair.py",
        "Hardcoded build/dictionary paths; archive or refactor separately.",
    ),
    OperationDescriptor(
        "native_query_lock_split",
        "native_query_lock_split_pair",
        "m4-native-query-lock-split-pair",
        "research_only",
        False,
        "benchmarks/harness-v2/archive/native-query-lock-split/run_native_query_lock_split_pair.py",
        "Archived closed/partial investigation; never a V2 entrypoint.",
    ),
    OperationDescriptor(
        "observer_overhead",
        "observer_overhead_diagnostic",
        "m4-observer-overhead-diagnostic",
        "research_only",
        True,
        "research/m4-native/tools/run_observer_overhead_diagnostic.py",
        "Diagnostic only; never a performance result.",
    ),
    OperationDescriptor(
        "observer_overhead_reduced",
        "observer_overhead_reduced_diagnostic",
        "m4-observer-overhead-diagnostic",
        "research_only",
        True,
        "research/m4-native/tools/run_observer_overhead_reduced.py",
        "Reduced fixture; shares the diagnostic purpose.",
    ),
    OperationDescriptor(
        "qgroup_reclaim",
        "qgroup_reclaim_diagnostic",
        "m4-qgroup-reclaim-diagnostic",
        "research_only",
        True,
        "research/m4-native/tools/run_qgroup_reclaim_diagnostic.py",
        "Diagnostic only; host-setup dependency.",
    ),
    OperationDescriptor(
        "scalar_open_latency",
        None,
        "m4-scalar-open-latency-diagnostic",
        "unregistered",
        False,
        "research/m4-native/tools/run_scalar_open_latency_diagnostic.py",
        "Receipts exist but no top-level policy purpose; do not launch as V2.",
    ),
    OperationDescriptor(
        "scalar_open_resource_rehearsal",
        None,
        "m4-scalar-open-resource-rehearsal",
        "unregistered",
        False,
        "research/m4-native/tools/run_scalar_open_latency_diagnostic.py",
        "Resource rehearsal for the unregistered scalar diagnostic.",
    ),
    OperationDescriptor(
        "synthetic_containment",
        None,
        "synthetic-containment-rehearsal",
        "research_only",
        False,
        "research/m4-native/tools/test_m4_containment_runner.py",
        "Test-only containment rehearsal.",
    ),
)


class RegistryError(ValueError):
    pass


def get(name: str) -> OperationDescriptor:
    for descriptor in DESCRIPTORS:
        if descriptor.name == name:
            return descriptor
    raise RegistryError(f"unknown operation: {name}")


def validate(policy: Any) -> None:
    """Check descriptors against policy sections and require full coverage."""
    claimed: dict[str, str] = {}
    for descriptor in DESCRIPTORS:
        if descriptor.policy_section is None:
            if descriptor.status not in {"historical_only", "research_only", "unregistered"}:
                raise RegistryError(f"{descriptor.name}: missing policy section")
            if descriptor.execution_allowed:
                raise RegistryError(f"{descriptor.name}: execution without policy section")
            continue
        section = policy.section(descriptor.policy_section)
        if descriptor.purpose is not None and section.get("purpose") != descriptor.purpose:
            raise RegistryError(
                f"{descriptor.name}: policy purpose {section.get('purpose')!r} "
                f"does not match {descriptor.purpose!r}"
            )
        previous = claimed.setdefault(descriptor.policy_section, descriptor.name)
        if previous != descriptor.name:
            raise RegistryError(
                f"policy section {descriptor.policy_section} claimed twice"
            )
        approved = section.get("approval_status") == "approved"
        if descriptor.execution_allowed and not approved:
            raise RegistryError(f"{descriptor.name}: execution requires approved policy")
    for name, section in policy.raw.items():
        if isinstance(section, dict) and section.get("purpose") and name not in claimed:
            raise RegistryError(f"policy section {name} has no registered operation")


def describe_all() -> list[dict[str, Any]]:
    return [descriptor.as_dict() for descriptor in DESCRIPTORS]
