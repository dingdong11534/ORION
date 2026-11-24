"""Security audit and hardening tests for Git subprocess execution."""

import subprocess
from pathlib import Path

import pytest

from orion.application.tools.workspace_contracts import (
    WorkspaceGitDiffInput,
    WorkspaceGitStatusInput,
)
from orion.infrastructure.tools.path_resolver import (
    WorkspacePathResolver,
    WorkspaceSecurityError,
)
from orion.infrastructure.tools.workspace_tools import (
    WorkspaceToolsService,
    run_git_command_bounded,
)


@pytest.fixture
def git_repo(tmp_path: Path):
    """Create a temporary initialized Git repository with an initial commit."""
    ws = tmp_path / "repo"
    ws.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "audit@orion.local"],
        cwd=ws,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Audit Tester"], cwd=ws, check=True, capture_output=True
    )

    f = ws / "tracked.txt"
    f.write_text("initial version\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, check=True, capture_output=True)

    # Modify file so unstaged changes exist
    f.write_text("modified version\n", encoding="utf-8")

    resolver = WorkspacePathResolver(ws)
    service = WorkspaceToolsService(resolver)
    return {
        "root": ws,
        "file": f,
        "resolver": resolver,
        "service": service,
    }


def _create_malicious_script(path: Path, marker_path: Path) -> Path:
    """Create an executable shell script that touches a marker file if invoked."""
    content = f"#!/bin/sh\ntouch '{marker_path}'\nexit 0\n"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_malicious_core_fsmonitor_blocked(git_repo, tmp_path: Path):
    """core.fsmonitor executable hook is blocked and never executed."""
    ws = git_repo["root"]
    marker = tmp_path / "fsmonitor_marker.txt"
    script = tmp_path / "fsmonitor.sh"
    _create_malicious_script(script, marker)

    # Configure malicious fsmonitor in repo git config
    subprocess.run(
        ["git", "config", "core.fsmonitor", str(script)], cwd=ws, check=True, capture_output=True
    )

    service: WorkspaceToolsService = git_repo["service"]
    status_out = service.git_status(WorkspaceGitStatusInput())
    diff_out = service.git_diff(WorkspaceGitDiffInput())

    # Verify marker was NEVER touched
    assert marker.exists() is False, "Security failure: core.fsmonitor executable was invoked!"
    assert status_out.is_git_repo is True
    assert status_out.branch == "main"
    assert "modified version" in diff_out.diff_text


def test_malicious_diff_external_blocked(git_repo, tmp_path: Path):
    """diff.external executable driver is blocked and never executed."""
    ws = git_repo["root"]
    marker = tmp_path / "diff_external_marker.txt"
    script = tmp_path / "diff_external.sh"
    _create_malicious_script(script, marker)

    subprocess.run(
        ["git", "config", "diff.external", str(script)], cwd=ws, check=True, capture_output=True
    )

    service: WorkspaceToolsService = git_repo["service"]
    diff_out = service.git_diff(WorkspaceGitDiffInput())

    assert marker.exists() is False, "Security failure: diff.external executable was invoked!"
    assert "-initial version" in diff_out.diff_text
    assert "+modified version" in diff_out.diff_text


def test_textconv_filter_blocked(git_repo, tmp_path: Path):
    """Textconv filter executables are blocked via --no-textconv."""
    ws = git_repo["root"]
    marker = tmp_path / "textconv_marker.txt"
    script = tmp_path / "textconv.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat \"$1\"\n", encoding="utf-8")
    script.chmod(0o755)

    (ws / ".gitattributes").write_text("*.txt diff=customconv\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "diff.customconv.textconv", str(script)],
        cwd=ws,
        check=True,
        capture_output=True,
    )

    service: WorkspaceToolsService = git_repo["service"]
    diff_out = service.git_diff(WorkspaceGitDiffInput())

    assert marker.exists() is False, "Security failure: textconv executable was invoked!"
    assert "modified version" in diff_out.diff_text


def test_external_pager_blocked(git_repo, tmp_path: Path):
    """External pagers configured via core.pager or PAGER are never invoked."""
    ws = git_repo["root"]
    marker = tmp_path / "pager_marker.txt"
    script = tmp_path / "pager.sh"
    _create_malicious_script(script, marker)

    subprocess.run(
        ["git", "config", "core.pager", str(script)], cwd=ws, check=True, capture_output=True
    )

    service: WorkspaceToolsService = git_repo["service"]
    service.git_status(WorkspaceGitStatusInput())
    service.git_diff(WorkspaceGitDiffInput())

    assert marker.exists() is False, "Security failure: external pager executable was invoked!"


def test_git_env_injection_ignored(git_repo, monkeypatch, tmp_path: Path):
    """Environment variables like GIT_EXTERNAL_DIFF and GIT_PAGER are stripped."""
    marker_env_diff = tmp_path / "env_diff_marker.txt"
    script_env_diff = tmp_path / "env_diff.sh"
    _create_malicious_script(script_env_diff, marker_env_diff)

    marker_env_pager = tmp_path / "env_pager_marker.txt"
    script_env_pager = tmp_path / "env_pager.sh"
    _create_malicious_script(script_env_pager, marker_env_pager)

    # Inject malicious variables into process environment
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(script_env_diff))
    monkeypatch.setenv("GIT_PAGER", str(script_env_pager))
    monkeypatch.setenv("PAGER", str(script_env_pager))

    service: WorkspaceToolsService = git_repo["service"]
    service.git_status(WorkspaceGitStatusInput())
    service.git_diff(WorkspaceGitDiffInput())

    assert marker_env_diff.exists() is False, "Security failure: GIT_EXTERNAL_DIFF was inherited!"
    assert marker_env_pager.exists() is False, "Security failure: GIT_PAGER was inherited!"


def test_git_pathspec_magic_rejected(git_repo):
    """Pathspec magic prefixes like :(top) or :(literal) are rejected."""
    service: WorkspaceToolsService = git_repo["service"]

    with pytest.raises(WorkspaceSecurityError, match="magic prefix"):
        service.git_diff(WorkspaceGitDiffInput(path=":(top)tracked.txt"))

    with pytest.raises(WorkspaceSecurityError, match="magic prefix"):
        service.git_diff(WorkspaceGitDiffInput(path=":(literal)../secret"))


def test_extremely_large_git_diff_bounded(git_repo):
    """Extremely large diffs are bounded in memory and marked truncated without deadlocking."""
    ws = git_repo["root"]
    f = ws / "tracked.txt"
    # Write ~300 KB of modifications
    f.write_text("Extra line content for diff.\n" * 10000, encoding="utf-8")

    service: WorkspaceToolsService = git_repo["service"]
    diff_out = service.git_diff(WorkspaceGitDiffInput(max_bytes=4096))

    assert diff_out.truncated is True
    assert len(diff_out.diff_text.encode("utf-8")) <= 4096


def test_git_substantial_stderr_captured(tmp_path: Path):
    """Substantial stderr from Git commands is captured without loss."""
    # Test run_git_command_bounded with a command outputting large stderr
    code, _stdout_str, stderr_str, _ = run_git_command_bounded(
        ["python3", "-c", "import sys; sys.stderr.write('Warning message\\n' * 50); sys.exit(1)"],
        cwd=tmp_path,
        max_stderr_bytes=8192,
    )
    assert code == 1
    assert "Warning message" in stderr_str
    assert stderr_str.count("Warning message") == 50


def test_normal_git_operations_preserved(git_repo):
    """Legitimate read-only Git status and diff operations work cleanly."""
    service: WorkspaceToolsService = git_repo["service"]

    status = service.git_status(WorkspaceGitStatusInput())
    assert status.is_git_repo is True
    assert status.branch == "main"
    assert status.is_clean is False
    assert any("tracked.txt" in s for s in status.unstaged_changes)

    diff = service.git_diff(WorkspaceGitDiffInput(path="tracked.txt"))
    assert "-initial version" in diff.diff_text
    assert "+modified version" in diff.diff_text
    assert diff.truncated is False


def test_malicious_filter_clean_blocked(git_repo, tmp_path: Path):
    """Malicious filter.<driver>.clean executable is neutralized and never executed."""
    ws = git_repo["root"]
    marker = tmp_path / "filter_clean_marker.txt"
    script = tmp_path / "filter_clean.sh"
    _create_malicious_script(script, marker)

    (ws / ".gitattributes").write_text("tracked.txt filter=evilclean\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "filter.evilclean.clean", str(script)],
        cwd=ws,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "filter.evilclean.required", "true"],
        cwd=ws,
        check=True,
        capture_output=True,
    )

    service: WorkspaceToolsService = git_repo["service"]
    status_out = service.git_status(WorkspaceGitStatusInput())
    diff_out = service.git_diff(WorkspaceGitDiffInput())

    assert marker.exists() is False, "Security failure: filter.clean executable was invoked!"
    assert status_out.is_git_repo is True
    assert "-initial version" in diff_out.diff_text
    assert "+modified version" in diff_out.diff_text


def test_malicious_filter_process_blocked(git_repo, tmp_path: Path):
    """Malicious filter.<driver>.process executable is neutralized and never executed."""
    ws = git_repo["root"]
    marker = tmp_path / "filter_process_marker.txt"
    script = tmp_path / "filter_proc.sh"
    _create_malicious_script(script, marker)

    (ws / ".gitattributes").write_text("tracked.txt filter=evilproc\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "filter.evilproc.process", str(script)],
        cwd=ws,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "filter.evilproc.required", "true"],
        cwd=ws,
        check=True,
        capture_output=True,
    )

    service: WorkspaceToolsService = git_repo["service"]
    status_out = service.git_status(WorkspaceGitStatusInput())
    diff_out = service.git_diff(WorkspaceGitDiffInput())

    assert marker.exists() is False, "Security failure: filter.process executable was invoked!"
    assert status_out.is_git_repo is True
    assert "-initial version" in diff_out.diff_text
    assert "+modified version" in diff_out.diff_text


def test_filter_assigned_through_gitattributes(git_repo, tmp_path: Path):
    """Multiple filters assigned through .gitattributes are all neutralized."""
    ws = git_repo["root"]
    m1 = tmp_path / "m1.txt"
    s1 = tmp_path / "s1.sh"
    _create_malicious_script(s1, m1)

    m2 = tmp_path / "m2.txt"
    s2 = tmp_path / "s2.sh"
    _create_malicious_script(s2, m2)

    (ws / ".gitattributes").write_text(
        "tracked.txt filter=f1\nother.txt filter=f2\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "config", "filter.f1.clean", str(s1)], cwd=ws, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "filter.f2.clean", str(s2)], cwd=ws, check=True, capture_output=True
    )

    other = ws / "other.txt"
    other.write_text("other text\n", encoding="utf-8")

    service: WorkspaceToolsService = git_repo["service"]
    service.git_status(WorkspaceGitStatusInput())
    service.git_diff(WorkspaceGitDiffInput())

    assert m1.exists() is False, "Filter f1 was invoked!"
    assert m2.exists() is False, "Filter f2 was invoked!"


def test_multiple_differently_named_filter_drivers(git_repo, tmp_path: Path):
    """Dynamic discovery overrides differently named filter drivers (hyphens, dots)."""
    ws = git_repo["root"]
    m_hyphen = tmp_path / "m_hyphen.txt"
    s_hyphen = tmp_path / "s_hyphen.sh"
    _create_malicious_script(s_hyphen, m_hyphen)

    m_dots = tmp_path / "m_dots.txt"
    s_dots = tmp_path / "s_dots.sh"
    _create_malicious_script(s_dots, m_dots)

    (ws / ".gitattributes").write_text("tracked.txt filter=my-driver\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "filter.my-driver.clean", str(s_hyphen)],
        cwd=ws,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "filter.driver.with.dots.clean", str(s_dots)],
        cwd=ws,
        check=True,
        capture_output=True,
    )

    service: WorkspaceToolsService = git_repo["service"]
    service.git_diff(WorkspaceGitDiffInput())

    assert m_hyphen.exists() is False
    assert m_dots.exists() is False


def test_clean_filter_in_repository_config_with_required_true(git_repo, tmp_path: Path):
    """clean-filter with required=true in repo config succeeds without failing."""
    ws = git_repo["root"]
    marker = tmp_path / "marker_req.txt"
    script = tmp_path / "req.sh"
    _create_malicious_script(script, marker)

    (ws / ".gitattributes").write_text("tracked.txt filter=reqfilter\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "filter.reqfilter.clean", str(script)],
        cwd=ws,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "filter.reqfilter.required", "true"],
        cwd=ws,
        check=True,
        capture_output=True,
    )

    service: WorkspaceToolsService = git_repo["service"]
    res = service.git_diff(WorkspaceGitDiffInput())

    assert marker.exists() is False
    assert "-initial version" in res.diff_text
    assert "+modified version" in res.diff_text


def test_normal_staged_diff_works(git_repo):
    """Normal staged Git diff operations work accurately."""
    ws = git_repo["root"]
    subprocess.run(["git", "add", "tracked.txt"], cwd=ws, check=True, capture_output=True)

    service: WorkspaceToolsService = git_repo["service"]
    # Unstaged diff should now be empty
    diff_unstaged = service.git_diff(WorkspaceGitDiffInput(staged=False))
    assert diff_unstaged.diff_text == ""

    # Staged diff should show the modification
    diff_staged = service.git_diff(WorkspaceGitDiffInput(staged=True))
    assert "-initial version" in diff_staged.diff_text
    assert "+modified version" in diff_staged.diff_text


def test_missing_or_invalid_filter_configuration_fails_safely(git_repo):
    """Missing or invalid filter configurations do not cause crashes or unhandled errors."""
    ws = git_repo["root"]
    (ws / ".gitattributes").write_text(
        "tracked.txt filter=completely_nonexistent\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "config", "filter.malformed.required", "true"],
        cwd=ws,
        check=True,
        capture_output=True,
    )

    service: WorkspaceToolsService = git_repo["service"]
    status_out = service.git_status(WorkspaceGitStatusInput())
    assert status_out.is_git_repo is True
    assert status_out.is_clean is False

    diff_out = service.git_diff(WorkspaceGitDiffInput())
    assert "-initial version" in diff_out.diff_text
    assert "+modified version" in diff_out.diff_text
    assert diff_out.truncated is False
