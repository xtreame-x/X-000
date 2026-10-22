#!/usr/bin/env python3
"""Build a machine-readable catalog of the 313 tools from available_tools.md.

Emits catalog/tools.json plus catalog/checkpoint-1.json (first 75 tools) and
catalog/checkpoint-2.json (remaining 238 Vercel tools). Every category count is
asserted against the totals declared in the source document, so a dropped or
duplicated name fails loudly instead of silently shrinking the catalog. The
Notion group is an Oryn addition that postdates the source scan.
"""
from __future__ import annotations

import json
from pathlib import Path

CORE = {
    "Files and code": ["bash_tool", "create_file", "str_replace", "view", "present_files", "Artifact"],
    "Web and search": ["web_search", "web_fetch", "image_search"],
    "Memory": ["memory_list", "memory_read", "memory_write", "memory_append",
               "memory_str_replace", "memory_delete"],
    "Phone and personal": ["alarm_create_v0", "timer_create_v0", "calendar_search_v0",
                           "event_create_v0", "event_create_v1", "event_update_v0",
                           "event_delete_v0", "event_search_v0", "user_location_v0",
                           "user_time_v0", "message_compose_v1"],
    "Places and sports": ["places_search", "places_map_display_v0", "fetch_sports_data"],
    "UI and display": ["ask_user_input_v0", "chart_display_v0", "recipe_display_v0",
                       "show_recommendation_cards", "mcp__visualize__read_me",
                       "mcp__visualize__show_widget"],
    "Connectors": ["search_mcp_registry", "suggest_connectors", "tool_search"],
    "Claude Docs (loaded)": ["mcp__Claude_Docs__batch", "mcp__Claude_Docs__guide",
                             "mcp__Claude_Docs__update"],
    "Other": ["end_conversation"],
}

GOOGLE_DRIVE = [
    "copy_file", "create_file", "download_file_content", "get_file_metadata",
    "get_file_permissions", "list_recent_files", "read_file_content", "search_files",
    "share_file", "trash_file", "update_file",
]

CLAUDE_DOCS_DEFERRED = ["create", "delete", "export", "query", "read"]

THREEJS = ["learn_threejs", "show_threejs_scene"]

SKETCHUP = ["build_model", "list_skills", "read_skill", "save_model", "usage_limit_reached"]

NOTION = [
    "append_blocks", "create_database", "create_page", "delete_block", "get_database",
    "query_database", "read_page", "search", "update_block", "update_page",
]

VERCEL = [
    "accept_project_transfer_request", "activate_kms_signing_key", "add_project_domain",
    "add_route", "add_toolbar_reaction", "aggregate_events", "aggregate_pageviews",
    "approve_rolling_release_stage", "artifact_query", "assign_alias",
    "authorize_team_sso", "buy_domain", "cancel_deployment",
    "change_toolbar_thread_resolve_status", "complete_rolling_release", "count_events",
    "count_pageviews", "create_deployment", "create_drain", "create_edge_config",
    "create_edge_config_token", "create_flag", "create_git_project", "create_kms_issuer",
    "create_kms_issuer_policy", "create_kms_signing_key", "create_observability_query",
    "create_or_transfer_domain", "create_project", "create_project_env", "create_record",
    "create_sandboxes_sessions_by_session_id_snapshot_v2",
    "create_sandboxes_sessions_by_session_id_snapshot_v3", "create_sandboxes_v2",
    "create_sandboxes_v3", "create_sandboxes_v4", "create_sdk_key", "create_session_directory",
    "create_storage_stores_blob", "create_trace_session", "edit_project_env", "edit_route",
    "edit_toolbar_message", "extend_session_timeout", "filter_project_envs",
    "generate_route", "get_access_to_vercel_url", "get_active_attack_status", "get_agent_run",
    "get_agent_run_trace", "get_alias", "get_auth_user", "get_bulk_availability",
    "get_bulk_price", "get_bypass_ip", "get_cert_by_id", "get_configuration", "get_connector",
    "get_connector_project_connection", "get_contact_info_schema", "get_custom_environment",
    "get_deployment", "get_deployment_feature_flags", "get_deployment_file_contents",
    "get_domain", "get_domain_availability", "get_domain_config",
    "get_domain_contact_verification", "get_domain_order", "get_domain_price",
    "get_domain_project_domains", "get_domain_verification_record",
    "get_domains_records_by_record_id", "get_drain", "get_edge_config",
    "get_edge_config_backup", "get_edge_config_backups", "get_edge_config_item",
    "get_edge_config_items", "get_edge_config_token", "get_edge_config_tokens",
    "get_edge_configs", "get_firewall_config", "get_flag", "get_flag_segment",
    "get_flag_settings", "get_git_deployment_context", "get_kms_issuer",
    "get_microfrontends_config", "get_microfrontends_config_for_project",
    "get_named_sandbox", "get_observability_configuration_projects",
    "get_observability_schema", "get_order", "get_project", "get_project_deletion_link",
    "get_project_domain", "get_project_env", "get_project_trace", "get_purchase_quote",
    "get_records", "get_remote_cache_status", "get_rolling_release",
    "get_rolling_release_billing_status", "get_rolling_release_config",
    "get_runtime_errors", "get_runtime_logs", "get_session", "get_session_command",
    "get_session_command_logs", "get_session_snapshot", "get_shared_env_var",
    "get_storage_stores_by_id", "get_team", "get_tld", "get_tld_price", "get_toolbar_thread",
    "get_vercel_ci_invocation", "get_vercel_ci_invocation_logs",
    "get_vercel_ci_invocation_tree", "get_vercel_ci_job_definition",
    "get_vercel_ci_job_run", "get_vercel_ci_job_run_logs", "get_vercel_ci_task_logs",
    "get_vercel_ci_task_run_logs", "get_webhooks", "git_namespaces",
    "import-claude-design-from-url", "invalidate_by_src_images", "invalidate_by_tags",
    "issue_cert", "kill_session_command", "list_access_group_members",
    "list_access_group_projects", "list_access_groups", "list_agent_run_projects",
    "list_agent_runs", "list_aliases", "list_billing_charges",
    "list_bulk_redirect_versions", "list_bulk_redirects", "list_certs",
    "list_connector_project_connections", "list_connectors", "list_contract_commitments",
    "list_deployment_aliases", "list_deployment_events", "list_deployment_files",
    "list_deployments", "list_domains", "list_drains", "list_event_types",
    "list_feature_flag_sdk_keys", "list_flag_segments", "list_flag_versions", "list_flags",
    "list_flags_v2", "list_integration_billing_plans",
    "list_integration_configuration_products", "list_integration_configurations",
    "list_kms_issuers", "list_named_sandboxes", "list_project_connector_connections",
    "list_project_custom_environments", "list_project_domains",
    "list_project_route_versions", "list_project_routes", "list_projects",
    "list_promote_aliases", "list_session_commands", "list_session_snapshots",
    "list_sessions", "list_supported_tlds", "list_team_flag_settings", "list_team_flags",
    "list_team_flags_v2", "list_team_members", "list_teams", "list_toolbar_threads",
    "list_user_events", "list_vercel_ci_invocation_attempts", "list_vercel_ci_invocations",
    "list_vercel_ci_job_definitions", "list_vercel_ci_job_runs",
    "list_vercel_ci_task_definitions", "list_vercel_ci_task_runs",
    "patch_edge_config_items", "patch_url_protection_bypass", "pause_project",
    "put_firewall_config", "read_access_group", "read_access_group_project",
    "read_session_file", "record_events", "replace_domain_dns_records",
    "reply_to_toolbar_thread", "request_promote", "request_rollback", "rerequest_check",
    "restore_edge_config_backup", "restore_redirects", "retry_vercel_ci_invocation",
    "revoke_kms_signing_key", "run_session_command", "search_domains", "search_repo",
    "search_vercel_documentation", "stage_redirects", "stage_routes",
    "start_rolling_release", "stop_session", "submit_feedback", "test_drain",
    "unpause_project", "update_attack_challenge_mode", "update_bulk_redirect_version",
    "update_connector", "update_drain", "update_edge_config", "update_firewall_config",
    "update_flag", "update_flag_segment", "update_flag_settings", "update_kms_issuer",
    "update_kms_issuer_policy", "update_project", "update_project_protection_bypass",
    "update_record", "update_rolling_release_config", "update_route_versions",
    "update_sandbox", "update_session_network_policy", "upload_artifact", "upload_file",
    "upsert_connector_project_connection", "verify_project_domain",
    "web_fetch_vercel_url", "write_session_files",
]

PREFIXES = {
    "Google Drive": "mcp__Google_Drive__",
    "Notion": "mcp__Notion__",
    "Claude Docs": "mcp__Claude_Docs__",
    "Three.js 3D Viewer": "mcp__Three_js_3D_Viewer__",
    "Trimble SketchUp": "mcp__Trimble_SketchUp__",
    "Vercel": "mcp__Vercel__",
}

CHECKPOINT_1_SIZE = 75


def build() -> dict:
    entries: list[dict] = []

    for group, names in CORE.items():
        for name in names:
            entries.append({
                "name": name,
                "invocation_name": name,
                "group": group,
                "section": "core",
                "deferred": False,
                "requiresToolSearch": False,
            })

    deferred_groups = {
        "Google Drive": GOOGLE_DRIVE,
        "Claude Docs": CLAUDE_DOCS_DEFERRED,
        "Three.js 3D Viewer": THREEJS,
        "Trimble SketchUp": SKETCHUP,
        "Notion": NOTION,
        "Vercel": VERCEL,
    }
    for group, names in deferred_groups.items():
        for name in names:
            entries.append({
                "name": f"{PREFIXES[group]}{name}",
                "invocation_name": f"{PREFIXES[group]}{name}",
                "shortName": name,
                "group": group,
                "section": "deferred",
                "deferred": True,
                # Everything in section 2 of the source document is gated behind
                # tool_search, including the five deferred Claude Docs tools.
                "requiresToolSearch": True,
            })

    for index, entry in enumerate(entries, start=1):
        entry["index"] = index

    # --- validation: counts must match the source document -------------------
    assert len(CORE) == 9, len(CORE)
    core_total = sum(len(v) for v in CORE.values())
    assert core_total == 42, f"core tools: expected 42, got {core_total}"
    assert len(GOOGLE_DRIVE) == 11, len(GOOGLE_DRIVE)
    assert len(CLAUDE_DOCS_DEFERRED) == 5, len(CLAUDE_DOCS_DEFERRED)
    assert len(THREEJS) == 2, len(THREEJS)
    assert len(SKETCHUP) == 5, len(SKETCHUP)
    assert len(NOTION) == 10, len(NOTION)
    assert len(VERCEL) == 238, f"vercel tools: expected 238, got {len(VERCEL)}"
    assert len(entries) == 313, f"total: expected 313, got {len(entries)}"

    names = [e["name"] for e in entries]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"duplicate tool names: {dupes}"

    deferred_total = len(entries) - core_total
    assert core_total + deferred_total == 313

    return {
        "source": "available_tools.md (scan: 2026-10-20) + Notion tools (2026-10-22)",
        "totals": {
            "core": core_total,
            "deferred": deferred_total,
            "grandTotal": len(entries),
            "byGroup": {
                **{g: len(v) for g, v in CORE.items()},
                "Google Drive": len(GOOGLE_DRIVE),
                "Claude Docs (deferred)": len(CLAUDE_DOCS_DEFERRED),
                "Three.js 3D Viewer": len(THREEJS),
                "Trimble SketchUp": len(SKETCHUP),
                "Notion": len(NOTION),
                "Vercel": len(VERCEL),
            },
        },
        "checkpoints": [
            {
                "id": "checkpoint-1",
                "label": "Core + Drive + Notion + Claude Docs + Three.js + SketchUp (everything except Vercel)",
                "range": [1, CHECKPOINT_1_SIZE],
                "count": CHECKPOINT_1_SIZE,
            },
            {
                "id": "checkpoint-2",
                "label": "Vercel",
                "range": [CHECKPOINT_1_SIZE + 1, len(entries)],
                "count": len(entries) - CHECKPOINT_1_SIZE,
            },
        ],
        "tools": entries,
    }


def main() -> None:
    catalog = build()
    out = Path(__file__).resolve().parent.parent / "catalog"
    out.mkdir(parents=True, exist_ok=True)

    tools = catalog["tools"]
    first = tools[:CHECKPOINT_1_SIZE]
    second = tools[CHECKPOINT_1_SIZE:]

    # checkpoint boundary must be exactly "all non-Vercel tools"
    assert not any(t["group"] == "Vercel" for t in first)
    assert all(t["group"] == "Vercel" for t in second)

    (out / "tools.json").write_text(json.dumps(catalog, indent=2) + "\n")
    (out / "checkpoint-1.json").write_text(json.dumps(first, indent=2) + "\n")
    (out / "checkpoint-2.json").write_text(json.dumps(second, indent=2) + "\n")

    print(f"tools.json        {len(tools)} tools")
    print(f"checkpoint-1.json {len(first)} tools  (#{first[0]['index']}-#{first[-1]['index']})")
    print(f"checkpoint-2.json {len(second)} tools (#{second[0]['index']}-#{second[-1]['index']})")
    for group, count in catalog["totals"]["byGroup"].items():
        print(f"  {group:<26} {count}")


if __name__ == "__main__":
    main()
