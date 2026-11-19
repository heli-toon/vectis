"""Tests for Phase 5: Production Packaging and Deployment Artifacts."""

import os
import sys
import stat
import subprocess
import tomllib
import yaml
import pytest
import vectis
import config
from init_db import SCHEMA_PATH
from web_ui import DASHBOARD_HTML_PATH


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_vectis_package_exports():
    """Verify top-level package metadata and exports."""
    assert hasattr(vectis, "__version__")
    assert vectis.__version__ == "0.3.0"
    for expected_symbol in [
        "init_database",
        "run_daemon",
        "run_single_heartbeat",
        "process_ticket",
        "claim_ticket",
        "get_ticket_workspace",
        "dispatch_tool",
        "execute_tool_calls",
        "check_agent_tool_permission",
        "record_memory",
        "search_memories",
        "list_memories",
        "config",
    ]:
        assert hasattr(vectis, expected_symbol), f"Missing symbol {expected_symbol} in vectis module"
        assert expected_symbol in vectis.__all__


def test_pyproject_toml_configuration():
    """Verify PEP 621 pyproject.toml contents and script entrypoints."""
    pyproject_path = os.path.join(REPO_ROOT, "pyproject.toml")
    assert os.path.exists(pyproject_path)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})
    assert project.get("name") == "vectis"
    assert project.get("version") == "0.3.0"
    assert "litellm>=1.0.0" in project.get("dependencies", [])
    assert project.get("scripts", {}).get("vectis") == "main:main"

    tool_setuptools = data.get("tool", {}).get("setuptools", {})
    assert "vectis" in tool_setuptools.get("packages", [])
    package_data = tool_setuptools.get("package-data", {})
    assert "schema.sql" in package_data.get("vectis", [])


def test_schema_and_template_resource_paths():
    """Verify schema and html dashboard templates are resolvable."""
    assert os.path.exists(SCHEMA_PATH), f"Schema path {SCHEMA_PATH} does not exist"
    assert os.path.exists(DASHBOARD_HTML_PATH), f"Dashboard HTML {DASHBOARD_HTML_PATH} does not exist"

    # Verify vectis package directory also contains resource files
    pkg_schema = os.path.join(REPO_ROOT, "vectis", "schema.sql")
    pkg_template = os.path.join(REPO_ROOT, "vectis", "templates", "dashboard.html")
    assert os.path.exists(pkg_schema)
    assert os.path.exists(pkg_template)


def test_nested_db_path_directory_creation(tmp_path):
    """Verify config.get_db_connection automatically creates parent directories."""
    nested_db = tmp_path / "deep" / "nested" / "test.db"
    assert not nested_db.parent.exists()

    conn = config.get_db_connection(str(nested_db))
    try:
        assert nested_db.parent.exists()
        assert nested_db.exists()
    finally:
        conn.close()


def test_dockerfile_structure():
    """Verify multi-stage alpine Dockerfile configuration."""
    dockerfile_path = os.path.join(REPO_ROOT, "Dockerfile")
    assert os.path.exists(dockerfile_path)

    with open(dockerfile_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "FROM python:3.12-alpine AS builder" in content
    assert "FROM python:3.12-alpine AS runner" in content
    assert "VOLUME [\"/app/data\", \"/app/workspaces\"]" in content
    assert "ENTRYPOINT [\"docker-entrypoint.sh\"]" in content
    assert "EXPOSE 8080" in content
    assert "USER vectis" in content


def test_docker_compose_structure():
    """Verify docker-compose.yml configuration and services."""
    compose_path = os.path.join(REPO_ROOT, "docker-compose.yml")
    assert os.path.exists(compose_path)

    with open(compose_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    services = data.get("services", {})
    assert "daemon" in services
    assert "ui" in services

    # Verify daemon configuration
    daemon = services["daemon"]
    assert daemon.get("command") == ["run"]
    assert "vectis-data:/app/data" in daemon.get("volumes", [])

    # Verify UI configuration
    ui = services["ui"]
    assert "8080:8080" in ui.get("ports", [])
    assert "ui" in ui.get("command", [])

    # Verify volumes
    volumes = data.get("volumes", {})
    assert "vectis-data" in volumes
    assert "vectis-workspaces" in volumes


def test_docker_entrypoint_permissions_and_logic():
    """Verify docker-entrypoint.sh exists, is executable, and contains auto-init logic."""
    entrypoint_path = os.path.join(REPO_ROOT, "docker-entrypoint.sh")
    assert os.path.exists(entrypoint_path)

    mode = os.stat(entrypoint_path).st_mode
    assert bool(mode & stat.S_IXUSR), "docker-entrypoint.sh should be executable"

    with open(entrypoint_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "vectis init" in content
    assert "exec vectis" in content


def test_systemd_unit_files():
    """Verify vectis.service and vectis-ui.service structure."""
    service_path = os.path.join(REPO_ROOT, "vectis.service")
    ui_service_path = os.path.join(REPO_ROOT, "vectis-ui.service")

    assert os.path.exists(service_path)
    assert os.path.exists(ui_service_path)

    with open(service_path, "r", encoding="utf-8") as f:
        service_content = f.read()

    assert "[Unit]" in service_content
    assert "[Service]" in service_content
    assert "[Install]" in service_content
    assert "ExecStart=/opt/vectis/.venv/bin/vectis run" in service_content
    assert "Restart=always" in service_content

    with open(ui_service_path, "r", encoding="utf-8") as f:
        ui_service_content = f.read()

    assert "ExecStart=/opt/vectis/.venv/bin/vectis ui" in ui_service_content
    assert "Restart=always" in ui_service_content


def test_vectis_cli_execution():
    """Verify the installed vectis binary executes properly."""
    venv_bin = os.path.join(REPO_ROOT, ".venv", "bin", "vectis")
    if os.path.exists(venv_bin):
        res = subprocess.run([venv_bin, "--help"], capture_output=True, text=True)
        assert res.returncode == 0
        assert "Vectis - Ultra-lightweight multi-agent orchestration system" in res.stdout
