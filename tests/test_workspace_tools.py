"""Unit tests for the 6 read-only workspace inspection tools."""

import subprocess
from pathlib import Path

import pytest

from orion.application.tools.workspace_contracts import (
    WorkspaceGetWorkingDirectoryInput,
    WorkspaceGitDiffInput,
    WorkspaceGitStatusInput,
    WorkspaceListDirectoryInput,
    WorkspaceReadFileInput,
    WorkspaceSearchFilesInput,
)
from orion.infrastructure.tools.path_resolver import (
    WorkspacePathResolver,
    WorkspaceSecurityError,
)
from orion.infrastructure.tools.workspace_tools import WorkspaceToolsService


@pytest.fixture
def workspace_service(tmp_path: Path):
    """Provide a configured WorkspaceToolsService with sample files, subdirs, and Git repo."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    # Create subdirectories and files
    src = ws / "src"
    src.mkdir()
    (src / "main.py").write_text("def main():\n    print('Hello World')\n", encoding="utf-8")
    (src / "utils.py").write_text("def helper():\n    return 42\n", encoding="utf-8")

    # Create excluded directories
    git_dir = ws / ".git"
    git_dir.mkdir()
    venv_dir = ws / ".venv"
    venv_dir.mkdir()
    (venv_dir / "lib.py").write_text("secret_venv", encoding="utf-8")

    # Create a binary file
    bin_file = ws / "sample.bin"
    bin_file.write_bytes(b"\x00\x01\x02\x03\xff\xfe")

    # Create a large text file (>128 KB)
    large_file = ws / "large.txt"
    large_file.write_text("Line of text.\n" * 15000, encoding="utf-8")  # ~210 KB

    # Initialize a temporary Git repository for git tools tests
    subprocess.run(["git", "init", "-b", "main"], cwd=ws, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@orion.local"], cwd=ws, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Orion Tester"], cwd=ws, capture_output=True, check=True
    )
    subprocess.run(["git", "add", "src/main.py"], cwd=ws, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=ws, capture_output=True, check=True
    )

    # Modify src/main.py so there are unstaged changes
    (src / "main.py").write_text("def main():\n    print('Modified World')\n", encoding="utf-8")

    resolver = WorkspacePathResolver(ws)
    return WorkspaceToolsService(resolver)


# ---------------------------------------------------------------------------
# 1. get_working_directory
# ---------------------------------------------------------------------------
def test_get_working_directory(workspace_service: WorkspaceToolsService):
    res = workspace_service.get_working_directory(WorkspaceGetWorkingDirectoryInput())
    assert res.workspace_root == str(workspace_service.workspace_root)


# ---------------------------------------------------------------------------
# 2. list_directory
# ---------------------------------------------------------------------------
def test_list_directory_deterministic_and_exclusions(workspace_service: WorkspaceToolsService):
    # List workspace root
    res = workspace_service.list_directory(WorkspaceListDirectoryInput(path="", max_entries=50))
    entry_names = [e.name for e in res.entries]
    # Check deterministic sorting
    assert entry_names == sorted(entry_names)

    # Check that .git and .venv are flagged as excluded
    git_entry = next(e for e in res.entries if e.name == ".git")
    assert git_entry.is_excluded is True

    venv_entry = next(e for e in res.entries if e.name == ".venv")
    assert venv_entry.is_excluded is True

    src_entry = next(e for e in res.entries if e.name == "src")
    assert src_entry.entry_type == "directory"
    assert src_entry.is_excluded is False


def test_list_directory_bounded_results(workspace_service: WorkspaceToolsService):
    res = workspace_service.list_directory(WorkspaceListDirectoryInput(path="", max_entries=2))
    assert len(res.entries) == 2
    assert res.truncated is True
    assert res.total_entries > 2


# ---------------------------------------------------------------------------
# 3. read_file
# ---------------------------------------------------------------------------
def test_read_file_utf8_success(workspace_service: WorkspaceToolsService):
    res = workspace_service.read_file(WorkspaceReadFileInput(path="src/main.py"))
    assert "Modified World" in res.content
    assert res.truncated is False
    assert res.size_bytes > 0


def test_read_file_binary_rejection(workspace_service: WorkspaceToolsService):
    with pytest.raises(WorkspaceSecurityError, match="Binary file detected"):
        workspace_service.read_file(WorkspaceReadFileInput(path="sample.bin"))


def test_read_file_truncation_large_file(workspace_service: WorkspaceToolsService):
    res = workspace_service.read_file(WorkspaceReadFileInput(path="large.txt", max_bytes=1024))
    assert res.truncated is True
    assert len(res.content.encode("utf-8")) == 1024


# ---------------------------------------------------------------------------
# 4. search_files
# ---------------------------------------------------------------------------
def test_search_files_literal_matching(workspace_service: WorkspaceToolsService):
    res = workspace_service.search_files(
        WorkspaceSearchFilesInput(query="helper", path="", case_sensitive=True)
    )
    assert res.total_matches == 1
    assert res.matches[0].relative_path == "src/utils.py"
    assert "helper" in res.matches[0].line_content


def test_search_files_case_sensitivity(workspace_service: WorkspaceToolsService):
    # Case sensitive: 'HELPER' should find 0 matches
    res1 = workspace_service.search_files(
        WorkspaceSearchFilesInput(query="HELPER", case_sensitive=True)
    )
    assert res1.total_matches == 0

    # Case insensitive: 'HELPER' should find 1 match
    res2 = workspace_service.search_files(
        WorkspaceSearchFilesInput(query="HELPER", case_sensitive=False)
    )
    assert res2.total_matches == 1


def test_search_files_skips_excluded_dirs(workspace_service: WorkspaceToolsService):
    # .venv has secret_venv, but search should skip it
    res = workspace_service.search_files(WorkspaceSearchFilesInput(query="secret_venv", path=""))
    assert res.total_matches == 0


# ---------------------------------------------------------------------------
# 5. git_status & git_diff
# ---------------------------------------------------------------------------
def test_git_status_modified_and_untracked(workspace_service: WorkspaceToolsService):
    res = workspace_service.git_status(WorkspaceGitStatusInput())
    assert res.is_git_repo is True
    assert res.branch == "main"
    assert res.is_clean is False
    assert any("src/main.py" in s for s in res.unstaged_changes)
    assert any("src/utils.py" in u for u in res.untracked_files)


def test_git_diff_unstaged(workspace_service: WorkspaceToolsService):
    res = workspace_service.git_diff(WorkspaceGitDiffInput(path="src/main.py"))
    assert "+    print('Modified World')" in res.diff_text
    assert "-    print('Hello World')" in res.diff_text
    assert res.truncated is False


def test_git_diff_non_git_repo(tmp_path: Path):
    non_git_ws = tmp_path / "plain_dir"
    non_git_ws.mkdir()
    resolver = WorkspacePathResolver(non_git_ws)
    svc = WorkspaceToolsService(resolver)

    status = svc.git_status(WorkspaceGitStatusInput())
    assert status.is_git_repo is False

    diff = svc.git_diff(WorkspaceGitDiffInput())
    assert diff.diff_text == ""
