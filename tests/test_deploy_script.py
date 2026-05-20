"""
tests/test_deploy_script.py – Validate Dockerfile, fly.toml, and deploy script.
"""

from __future__ import annotations

import os
import pathlib
import stat
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend_stub"
SCRIPTS_DIR = ROOT / "scripts"


# ---------------------------------------------------------------------------
# Dockerfile validation
# ---------------------------------------------------------------------------


class TestDockerfile:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.path = BACKEND_DIR / "Dockerfile"
        assert self.path.exists(), f"Dockerfile not found at {self.path}"
        self.text = self.path.read_text()
        self.lines = [
            line.strip()
            for line in self.text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_from_python312_slim(self):
        """Must use python:3.12-slim base image."""
        assert any(
            line.startswith("FROM python:3.12-slim") for line in self.lines
        ), "Dockerfile must start with 'FROM python:3.12-slim'"

    def test_pip_install_fastapi_uvicorn(self):
        """Must install fastapi and uvicorn via pip."""
        pip_lines = [ln for ln in self.lines if ln.startswith("RUN pip install")]
        assert pip_lines, "Dockerfile must have a 'RUN pip install' instruction"
        combined = " ".join(pip_lines).lower()
        assert "fastapi" in combined, "pip install must include fastapi"
        assert "uvicorn" in combined, "pip install must include uvicorn"

    def test_cmd_uvicorn_main_app(self):
        """CMD must run uvicorn main:app."""
        cmd_lines = [ln for ln in self.lines if ln.startswith("CMD")]
        assert cmd_lines, "Dockerfile must have a CMD instruction"
        combined = " ".join(cmd_lines).lower()
        assert "uvicorn" in combined, "CMD must invoke uvicorn"
        assert "main:app" in combined, "CMD must reference main:app"

    def test_expose_8080(self):
        """Must expose port 8080."""
        expose_lines = [ln for ln in self.lines if ln.startswith("EXPOSE")]
        assert expose_lines, "Dockerfile must have an EXPOSE instruction"
        assert any("8080" in ln for ln in expose_lines), "Must expose port 8080"

    def test_workdir_set(self):
        """Must set a WORKDIR."""
        workdir_lines = [ln for ln in self.lines if ln.startswith("WORKDIR")]
        assert workdir_lines, "Dockerfile must have a WORKDIR instruction"

    def test_copy_instruction(self):
        """Must have a COPY instruction."""
        copy_lines = [ln for ln in self.lines if ln.startswith("COPY")]
        assert copy_lines, "Dockerfile must have a COPY instruction"

    def test_no_cache_pip(self):
        """pip install should use --no-cache-dir for smaller image."""
        pip_lines = [ln for ln in self.lines if ln.startswith("RUN pip install")]
        assert pip_lines, "Expected pip install line"
        assert "--no-cache-dir" in pip_lines[0], "pip install should use --no-cache-dir"


# ---------------------------------------------------------------------------
# fly.toml validation
# ---------------------------------------------------------------------------


class TestFlyToml:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.path = BACKEND_DIR / "fly.toml"
        assert self.path.exists(), f"fly.toml not found at {self.path}"
        self.text = self.path.read_text()
        self.config = tomllib.loads(self.text)

    def test_valid_toml(self):
        """fly.toml must be valid TOML (already parsed in fixture)."""
        assert isinstance(self.config, dict)

    def test_app_name(self):
        """App name must be oyster-backend-stub."""
        assert self.config.get("app") == "oyster-backend-stub"

    def test_region(self):
        """Primary region must be iad."""
        assert self.config.get("primary_region") == "iad"

    def test_http_service_port(self):
        """HTTP service internal port must be 8080."""
        http = self.config.get("http_service", {})
        assert http.get("internal_port") == 8080

    def test_build_section(self):
        """Must have a [build] section referencing the Dockerfile."""
        build = self.config.get("build", {})
        assert "dockerfile" in build, "[build] must specify dockerfile"

    def test_vm_section(self):
        """Must have a [[vm]] section."""
        vms = self.config.get("vm", [])
        assert isinstance(vms, list) and len(vms) > 0, "Must have [[vm]] section"


# ---------------------------------------------------------------------------
# Deploy script validation
# ---------------------------------------------------------------------------


class TestDeployScript:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.path = SCRIPTS_DIR / "deploy_backend.sh"
        assert self.path.exists(), f"deploy_backend.sh not found at {self.path}"
        self.text = self.path.read_text()

    def test_is_executable(self):
        """Script must have the executable bit set."""
        mode = os.stat(self.path).st_mode
        assert mode & stat.S_IXUSR, "deploy_backend.sh must be executable"

    def test_shebang(self):
        """Script must start with a bash shebang."""
        assert self.text.startswith("#!/usr/bin/env bash") or self.text.startswith(
            "#!/bin/bash"
        ), "Script must have a bash shebang"

    def test_set_strict_mode(self):
        """Script must use set -euo pipefail for safety."""
        assert (
            "set -euo pipefail" in self.text
        ), "Script must include 'set -euo pipefail'"

    def test_references_backend_stub(self):
        """Script must reference the backend_stub directory."""
        assert (
            "backend_stub" in self.text
        ), "Script must reference backend_stub directory"

    def test_flyctl_deploy(self):
        """Script must invoke flyctl deploy."""
        assert "flyctl deploy" in self.text, "Script must run 'flyctl deploy'"

    def test_no_hardcoded_token(self):
        """Script must NOT contain any hardcoded fly token."""
        lower = self.text.lower()
        forbidden = ["fly_token", "api_token", "secret_key", "password"]
        for token in forbidden:
            assert token not in lower, f"Script must not contain hardcoded '{token}'"

    def test_flyctl_check(self):
        """Script should check that flyctl is available."""
        assert "flyctl" in self.text, "Script should reference flyctl"
