"""Milestone 4.6a — Capability Policy Cleanup & Sensitive Workspace File Audit Tests."""

import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from orion.application.tools.approvals import ApprovalBroker
from orion.application.tools.contracts import (
    ExecutionStatus,
    ExecutionType,
    PermissionLevel,
    PermissionOutcome,
    ToolDefinition,
    ToolProposal,
)
from orion.application.tools.permissions import PermissionEngine, PermissionPolicy
from orion.application.tools.registry import ToolRegistry
from orion.application.tools.router import RouterDisposition, ToolRouter
from orion.application.tools.workspace_contracts import (
    WorkspaceGitDiffInput,
    WorkspaceGitStatusInput,
    WorkspaceListDirectoryInput,
    WorkspaceReadFileInput,
    WorkspaceSearchFilesInput,
)
from orion.infrastructure.tools.execution_manager import (
    DEFAULT_ALLOWLISTED_COMMANDS,
    ExecutionManager,
)
from orion.infrastructure.tools.path_resolver import WorkspacePathResolver, WorkspaceSecurityError
from orion.infrastructure.tools.workspace_tools import (
    WorkspaceToolsService,
    create_workspace_tool_definitions,
)


class _PytestArgs(BaseModel):
    command: str = Field(default="pytest")
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None


class _PytestOut(BaseModel):
    stdout: str = ""


def _make_pytest_tool() -> ToolDefinition:
    return ToolDefinition(
        tool_id="system_pytest",
        name="Pytest Runner",
        description="Run pytest",
        permission_level=PermissionLevel.CODE_EXECUTION,
        execution_type=ExecutionType.SUBPROCESS,
        is_mutating=False,
        input_model=_PytestArgs,
        output_model=_PytestOut,
    )


# ---------------------------------------------------------------------------
# 1. CODE_EXECUTION Capability & Approval UI Copy
# ---------------------------------------------------------------------------


def test_code_execution_distinct_from_read_only_and_system_execute(tmp_path: Path):
    """CODE_EXECUTION is denied unless allow_code_execution=True, even if read_only & system_execute are True."""
    pytest_tool = _make_pytest_tool()
    registry = ToolRegistry()
    registry.register(pytest_tool)
    for d in create_workspace_tool_definitions():
        registry.register(d)
    registry.seal()

    # Model-eligible catalog must exclude CODE_EXECUTION
    eligible_ids = {t.tool_id for t in registry.list_model_eligible_tools()}
    assert "system_pytest" not in eligible_ids
    assert "workspace_read_file" in eligible_ids

    # Even with allow_read_only=True and allow_system_execute=True, CODE_EXECUTION is DENIED
    policy_deny = PermissionPolicy(
        workspace_root=tmp_path,
        allow_read_only=True,
        allow_system_execute=True,
        allow_code_execution=False,
    )
    router_deny = ToolRouter(
        registry=registry,
        permission_engine=PermissionEngine(policy_deny),
        approval_broker=ApprovalBroker(),
    )
    decision = router_deny.route(
        ToolProposal(tool_id="system_pytest", arguments={"command": "pytest", "args": []}),
        client_request_id="req-1",
        conversation_id="conv-1",
    )
    assert decision.disposition == RouterDisposition.DENIED
    assert decision.permission_decision is not None
    assert decision.permission_decision.outcome == PermissionOutcome.DENY


def test_code_execution_requires_approval_and_has_explicit_warning_copy(tmp_path: Path):
    """When allow_code_execution=True, CODE_EXECUTION requires approval with deterministic pytest warning."""
    pytest_tool = _make_pytest_tool()
    registry = ToolRegistry()
    registry.register(pytest_tool)
    registry.seal()

    broker = ApprovalBroker()
    policy = PermissionPolicy(
        workspace_root=tmp_path,
        allow_code_execution=True,
    )
    router = ToolRouter(
        registry=registry,
        permission_engine=PermissionEngine(policy),
        approval_broker=broker,
    )
    decision = router.route(
        ToolProposal(
            tool_id="system_pytest",
            arguments={"command": "pytest", "args": ["-q"], "cwd": "tests"},
        ),
        client_request_id="req-2",
        conversation_id="conv-2",
    )
    assert decision.disposition == RouterDisposition.AWAITING_APPROVAL
    req = decision.approval_request
    assert req is not None
    assert (
        "Executes project-controlled Python code (`pytest`) inside workspace [tests]."
        in req.action_summary
    )
    assert (
        "Test modules, conftest.py and imported modules can execute arbitrary Python"
        in req.action_summary
    )


# ---------------------------------------------------------------------------
# 2. printf Removed from Production Allowlist
# ---------------------------------------------------------------------------


def test_printf_not_in_production_allowlist(tmp_path: Path):
    """printf must not be in DEFAULT_ALLOWLISTED_COMMANDS and is rejected by default ExecutionManager."""
    assert "printf" not in DEFAULT_ALLOWLISTED_COMMANDS
    mgr = ExecutionManager(workspace_root=tmp_path)
    out = mgr.run_command("printf", ["hello"])
    assert out.status == ExecutionStatus.FAILED
    assert "not in the executable allowlist" in (out.error_message or "")


# ---------------------------------------------------------------------------
# 3. Sensitive Workspace File Access Audit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        ".env",
        ".env.local",
        ".env.production",
        "certs/server.key",
        "certs/server.pem",
        "certs/ca.crt",
        "keys/id_rsa",
        "keys/id_ed25519",
        ".aws/credentials",
        "config/secrets.json",
        "config/token.json",
    ],
)
def test_sensitive_files_blocked_across_read_list_and_search(tmp_path: Path, rel_path: str):
    """Sensitive files must be blocked by read_file, omitted from list_directory, and skipped by search_files."""
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("TOP_SECRET_VALUE=hunter2\n")
    (ws / "normal.txt").write_text("hello world\n")

    svc = WorkspaceToolsService(WorkspacePathResolver(ws))

    # 1. workspace_read_file must reject
    with pytest.raises(WorkspaceSecurityError, match="sensitive"):
        svc.read_file(WorkspaceReadFileInput(path=rel_path))

    # 2. workspace_list_directory must not expose sensitive filenames
    parent_rel = str(Path(rel_path).parent)
    list_path = "" if parent_rel == "." else parent_rel
    if not (Path(rel_path).parts[0].startswith(".") and Path(rel_path).parts[0] in (".aws",)):
        listing = svc.list_directory(WorkspaceListDirectoryInput(path=list_path))
        listed_names = {e.name for e in listing.entries}
        assert Path(rel_path).name not in listed_names

    # 3. workspace_search_files must never leak sensitive file content
    search_res = svc.search_files(WorkspaceSearchFilesInput(path="", query="TOP_SECRET_VALUE"))
    assert search_res.total_matches == 0
    assert search_res.matches == []


def test_sensitive_files_filtered_from_git_status_and_diff(tmp_path: Path):
    """workspace_git_status and workspace_git_diff must not expose sensitive filenames or diffs."""
    ws = tmp_path / "repo"
    ws.mkdir()
    subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "Test"], check=True)

    (ws / "app.py").write_text("x = 1\n")
    (ws / ".env.local").write_text("API_KEY=initial\n")
    subprocess.run(["git", "-C", str(ws), "add", "app.py", ".env.local"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-m", "init"], check=True, capture_output=True)

    # Modify both tracked files and add an untracked sensitive file
    (ws / "app.py").write_text("x = 2\n")
    (ws / ".env.local").write_text("API_KEY=changed_secret\n")
    (ws / "private.pem").write_text("-----BEGIN PRIVATE KEY-----\n")

    svc = WorkspaceToolsService(WorkspacePathResolver(ws))
    st = svc.git_status(WorkspaceGitStatusInput())
    assert all(".env.local" not in s for s in st.unstaged_changes)
    assert "private.pem" not in st.untracked_files
    assert any("app.py" in s for s in st.unstaged_changes)

    diff = svc.git_diff(WorkspaceGitDiffInput())
    assert "changed_secret" not in diff.diff_text
    assert ".env.local" not in diff.diff_text
    assert "x = 2" in diff.diff_text
    assert "private.pem" not in diff.untracked_files
