"""Integration tests for the workspace tool subsystem (Registry, Router, Permissions)."""

from pathlib import Path

import pytest

from orion.application.tools.contracts import (
    ExecutionStatus,
    ToolProposal,
)
from orion.application.tools.router import RouterDisposition
from orion.infrastructure.tools.workspace_tools import build_workspace_tool_system


@pytest.fixture
def workspace_fixture(tmp_path: Path):
    ws = tmp_path / "integrated_ws"
    ws.mkdir()
    (ws / "readme.md").write_text(
        "# ORION Workspace Integration\nTesting tools.\n", encoding="utf-8"
    )
    sub = ws / "docs"
    sub.mkdir()
    (sub / "guide.txt").write_text("Documentation guide.\n", encoding="utf-8")
    return ws


def test_workspace_subsystem_bootstrap(workspace_fixture: Path):
    """build_workspace_tool_system registers all 6 tools, seals registry, and returns router."""
    registry, _router, _service = build_workspace_tool_system(workspace_fixture)

    assert registry.is_sealed is True
    assert len(registry) == 6

    # Verify all expected tool IDs are present
    expected_ids = {
        "workspace_get_working_directory",
        "workspace_list_directory",
        "workspace_read_file",
        "workspace_search_files",
        "workspace_git_status",
        "workspace_git_diff",
    }
    assert {t.tool_id for t in registry.list_tools()} == expected_ids


def test_router_executes_get_working_directory(workspace_fixture: Path):
    """Router successfully dispatches and executes workspace_get_working_directory."""
    _, router, _ = build_workspace_tool_system(workspace_fixture)

    proposal = ToolProposal(tool_id="workspace_get_working_directory", arguments={})
    decision = router.route(proposal, client_request_id="req_101", conversation_id="conv_202")

    assert decision.disposition == RouterDisposition.DISPATCHED
    assert decision.result is not None
    assert decision.result.status == ExecutionStatus.COMPLETED
    assert decision.result.output == {"workspace_root": str(workspace_fixture.resolve())}


def test_router_executes_read_file(workspace_fixture: Path):
    """Router successfully dispatches and executes workspace_read_file."""
    _, router, _ = build_workspace_tool_system(workspace_fixture)

    proposal = ToolProposal(tool_id="workspace_read_file", arguments={"path": "readme.md"})
    decision = router.route(proposal, client_request_id="req_1", conversation_id="conv_1")

    assert decision.disposition == RouterDisposition.DISPATCHED
    assert decision.result is not None
    assert decision.result.status == ExecutionStatus.COMPLETED
    assert "ORION Workspace Integration" in decision.result.output["content"]
    assert decision.result.output["relative_path"] == "readme.md"


def test_router_executes_list_directory(workspace_fixture: Path):
    """Router successfully dispatches and executes workspace_list_directory."""
    _, router, _ = build_workspace_tool_system(workspace_fixture)

    proposal = ToolProposal(
        tool_id="workspace_list_directory", arguments={"path": "", "max_entries": 10}
    )
    decision = router.route(proposal, client_request_id="req_2", conversation_id="conv_1")

    assert decision.disposition == RouterDisposition.DISPATCHED
    assert decision.result is not None
    assert decision.result.status == ExecutionStatus.COMPLETED
    entry_names = [e["name"] for e in decision.result.output["entries"]]
    assert "readme.md" in entry_names
    assert "docs" in entry_names


def test_router_executes_search_files(workspace_fixture: Path):
    """Router successfully dispatches and executes workspace_search_files."""
    _, router, _ = build_workspace_tool_system(workspace_fixture)

    proposal = ToolProposal(
        tool_id="workspace_search_files",
        arguments={"query": "Documentation", "path": ""},
    )
    decision = router.route(proposal, client_request_id="req_3", conversation_id="conv_1")

    assert decision.disposition == RouterDisposition.DISPATCHED
    assert decision.result is not None
    assert decision.result.status == ExecutionStatus.COMPLETED
    assert decision.result.output["total_matches"] == 1
    assert decision.result.output["matches"][0]["relative_path"] == "docs/guide.txt"


def test_router_denies_path_escaping_workspace(workspace_fixture: Path):
    """Permission engine strictly denies proposals referencing paths escaping workspace."""
    _, router, _ = build_workspace_tool_system(workspace_fixture)

    proposal = ToolProposal(
        tool_id="workspace_read_file",
        arguments={"path": "../outside.txt"},
    )
    decision = router.route(proposal, client_request_id="req_4", conversation_id="conv_1")

    assert decision.disposition == RouterDisposition.DENIED
    assert decision.permission_decision is not None
    assert "traversal" in decision.permission_decision.reason.lower()
    assert decision.result is None


def test_router_rejects_extra_fields_in_proposal(workspace_fixture: Path):
    """Model-supplied proposals with extra unexpected fields fail validation."""
    _, _router, _ = build_workspace_tool_system(workspace_fixture)

    with pytest.raises(Exception):  # noqa: B017
        ToolProposal(
            tool_id="workspace_read_file",
            arguments={"path": "readme.md"},
            is_admin=True,  # type: ignore[call-arg]
        )
