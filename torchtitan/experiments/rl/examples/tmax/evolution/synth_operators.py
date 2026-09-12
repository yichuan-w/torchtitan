#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""RST's rewrite-operator taxonomy, transcribed from the paper's Table 7.

Forty operators in five families. The families are not application domains but
the five places difficulty can be introduced into a terminal task — what must be
true before a command runs, the command sequence itself, the artifacts produced,
the state that must stay consistent, and the evidence needed to work out what
went wrong. Domains overlap and do not tell you how to make a seed harder;
mechanisms do.

Definitions are the paper's own wording so a generated task can be traced back
to the operator that was supposed to shape it.
"""
from __future__ import annotations

import os


def harder_uses_operators() -> bool:
    """Opt in to the fixed menu; student-guided hardening is the default."""
    value = os.environ.get("EVOLVE_HARDER_OPERATORS", "0")
    if value not in {"0", "1"}:
        raise ValueError("EVOLVE_HARDER_OPERATORS must be 0 or 1")
    return value == "1"


OPERATORS: dict[str, dict[str, str]] = {
    "environment_runtime_substrate": {
        "dependency_version_alignment": "Align dependency/runtime versions and verify the toolchain.",
        "path_workdir_alignment": "Resolve working-directory and path/entrypoint mismatches.",
        "permission_executable_alignment": "Repair executable bits, ownership, or access needed by the workflow.",
        "environment_variable_resolution": "Infer/configure env vars, locale, timezone, shell init, or profiles.",
        "toolchain_availability_check": "Ensure required compilers/interpreters/CLI tools exist.",
        "container_build_alignment": "Align Dockerfile, build context, and image setup.",
        "service_process_lifecycle": "Start/stop/inspect local services, daemons, or ports.",
        "resource_cleanup_and_limits": "Handle temp files, timeouts, log growth, and cleanup.",
    },
    "build_test_execution_workflow": {
        "compile_link_package_workflow": "Compile, link, package, or assemble a runnable artifact.",
        "unit_test_failure_repair": "Run unit tests, interpret failures, and complete the workflow.",
        "integration_test_workflow": "Coordinate multiple components and validate integrated behavior.",
        "cli_argument_behavior": "Validate CLI flags, stdio, exit codes, and argument behavior.",
        "lint_format_static_check": "Run lint/format/type-check and resolve surfaced issues.",
        "runtime_error_debug_loop": "Diagnose runtime exceptions/stack traces and repair behavior.",
        "build_artifact_generation": "Produce a binary/package/bundle and verify usability.",
        "performance_or_benchmark_smoke": "Run a bounded benchmark/smoke check and record a result.",
    },
    "data_artifact_report_processing": {
        "format_conversion_validation": "Convert formats while validating semantic preservation.",
        "schema_content_validation": "Validate schema, fields, types, ranges, or completeness.",
        "aggregation_summary_report": "Compute aggregates and produce a summary/report.",
        "artifact_inventory_reconciliation": "Reconcile outputs, manifests, and directory inventories.",
        "archive_compression_extraction": "Extract/pack/inspect archives such as tar/zip/gzip.",
        "dedup_sort_normalization": "Deduplicate, sort, normalize, or canonicalize artifacts.",
        "checksum_hash_provenance": "Generate or verify hashes/checksums/provenance.",
        "media_or_binary_metadata_processing": "Inspect lightweight media/binary metadata.",
    },
    "configuration_state_migration": {
        "config_data_consistency": "Reconcile configuration and data/source consistency.",
        "manifest_lockfile_reconciliation": "Align manifests, lockfiles, or package metadata.",
        "state_migration_transform": "Migrate state across formats/versions/layouts.",
        "cache_index_regeneration": "Regenerate caches, indexes, or derived metadata.",
        "database_or_file_state_initialization": "Initialize and validate file-backed/DB state.",
        "template_render_consistency": "Keep templates and rendered outputs consistent.",
        "profile_feature_flag_selection": "Select/validate profiles or feature-flag branches.",
        "backup_rollback_idempotency": "Support backup, rollback, and idempotent reruns.",
    },
    "diagnostics_audit_forensics": {
        "log_error_diagnosis": "Inspect logs/stderr, identify cause, complete workflow.",
        "trace_event_correlation": "Correlate logs/events/timestamps across sources.",
        "verifier_failure_interpretation": "Use local validation output to infer missing state.",
        "security_permission_audit": "Audit permissions, sensitive files, or unsafe config.",
        "data_quality_anomaly_investigation": "Investigate missing/duplicate/anomalous data.",
        "process_endpoint_inspection": "Inspect local processes, ports, sockets, or endpoints.",
        "git_history_diff_forensics": "Use git history/diff to diagnose or produce state.",
        "evidence_bundle_generation": "Create a verifiable evidence bundle with logs/reports.",
    },
}

# What in a seed suggests a family has something to work with. The paper calls
# this the local affordance scan and names package manifests, structured-data
# formats, build scripts, command-line interfaces, archives, databases and logs.
AFFORDANCE: dict[str, tuple[str, ...]] = {
    "environment_runtime_substrate": (
        "apt-get",
        "pip install",
        "chmod",
        "chown",
        "export ",
        "path=",
        "systemctl",
        "service",
        "port",
        "user ",
        "workdir",
    ),
    "build_test_execution_workflow": (
        "make",
        "cmake",
        "gcc",
        "g++",
        "cargo",
        "go build",
        "npm",
        "pytest",
        "lint",
        "mvn",
        "gradle",
        "./configure",
    ),
    "data_artifact_report_processing": (
        "csv",
        "json",
        "xml",
        "yaml",
        "tar ",
        "zip",
        "gzip",
        "sha256",
        "md5",
        "sort",
        "uniq",
        "report",
    ),
    "configuration_state_migration": (
        "config",
        ".conf",
        ".ini",
        "lockfile",
        "lock.json",
        "migrate",
        "template",
        "cache",
        "sqlite",
        "database",
        "backup",
    ),
    "diagnostics_audit_forensics": (
        "log",
        "stderr",
        "traceback",
        "journalctl",
        "git log",
        "git diff",
        "permission",
        "audit",
        "anomal",
        "ps aux",
        "netstat",
    ),
}


def all_operator_ids() -> list[tuple[str, str]]:
    return [(f, op) for f, ops in OPERATORS.items() for op in ops]
