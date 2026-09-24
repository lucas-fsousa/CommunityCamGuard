"""Prevent configuration growth from bypassing the dashboard exposure review."""

import json
from pathlib import Path

from backend.app.config import Settings


def inventory():
    path = Path(__file__).parents[1] / "docs/internal/settings-inventory.json"
    return json.loads(path.read_text())


def test_every_setting_has_one_explicit_classification():
    data = inventory()
    assert data["schema_version"] == 1
    groups = data["groups"]
    assert {group["classification"] for group in groups} == {
        "dashboard_next_operation_candidate", "dashboard_service_restart_candidate",
        "server_only", "unimplemented",
    }
    fields = [field for group in groups for field in group["fields"]]
    assert len(fields) == len(set(fields)), "duplicate configuration classification"
    assert set(fields) == set(Settings.model_fields), "review added/removed settings before exposure"


def test_credentials_and_operator_boundaries_are_not_dashboard_candidates():
    groups = inventory()["groups"]
    server_only = next(group["fields"] for group in groups if group["classification"] == "server_only")
    required = {
        "dashboard_secret_key", "session_signing_key", "aws_access_key_id", "aws_secret_access_key",
        "db_path", "recordings_dir", "frontend_dir", "host", "port", "discovery_scan_subnets",
        "provisioning_remote_ble_enabled", "provisioning_ble_material_file",
        "provisioning_ble_material_max_age_seconds", "live_hwaccel",
    }
    required.update(name for name in Settings.model_fields
                    if name.startswith(("native_av_diagnostic_", "go2rtc_")))
    assert required <= set(server_only)


def test_inventory_contains_names_only_never_environment_values():
    data = inventory()
    assert set(data) == {"schema_version", "groups"}
    for group in data["groups"]:
        assert set(group) == {"classification", "fields"}
        assert all(isinstance(field, str) and field in Settings.model_fields for field in group["fields"])
