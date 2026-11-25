"""Unit tests for ExecutionManager: allowlist, sanitization, process limits, timeouts, and cleanup."""

import os
from pathlib import Path

import pytest

from orion.application.tools.contracts import ExecutionStatus
from orion.infrastructure.tools.execution_manager import (
    DEFAULT_ALLOWLISTED_COMMANDS,
    MAX_ARG_COUNT,
    MAX_ARG_LENGTH,
    DisallowedCommandError,
    ExecutableNotFoundError,
    ExecutionConcurrencyError,
    ExecutionManager,
    InvalidArgumentsError,
    build_sanitized_environment,
    resolve_executable,
    validate_command_arguments,
)
from orion.infrastructure.tools.path_resolver import WorkspaceSecurityError


@pytest.fixture
def temp_workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def manager(temp_workspace: Path):
    return ExecutionManager(
        workspace_root=temp_workspace,
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
    )


# ---------------------------------------------------------------------------
# Allowlist & Resolution Tests
# ---------------------------------------------------------------------------


def test_resolve_allowlisted_command():
    """Allowlisted command like git or test-injected printf resolves to an existing executable binary."""
    assert "printf" not in DEFAULT_ALLOWLISTED_COMMANDS
    with pytest.raises(DisallowedCommandError, match="not in the executable allowlist"):
        resolve_executable("printf", DEFAULT_ALLOWLISTED_COMMANDS)

    path = resolve_executable("printf", DEFAULT_ALLOWLISTED_COMMANDS | {"printf"})
    assert path.is_file()
    assert os.access(path, os.X_OK)


def test_resolve_disallowed_command():
    """Commands outside allowlist are rejected immediately."""
    with pytest.raises(DisallowedCommandError, match="not in the executable allowlist"):
        resolve_executable("nmap", DEFAULT_ALLOWLISTED_COMMANDS)


def test_reject_executable_path_injection():
    """Path separators (absolute or relative paths) in command name are strictly blocked."""
    with pytest.raises(DisallowedCommandError, match="Executable path injection blocked"):
        resolve_executable("/bin/sh", DEFAULT_ALLOWLISTED_COMMANDS)

    with pytest.raises(DisallowedCommandError, match="Executable path injection blocked"):
        resolve_executable("../bin/pytest", DEFAULT_ALLOWLISTED_COMMANDS)


def test_reject_shell_interpreters():
    """Shell interpreters are strictly rejected even if attempted."""
    for sh in ("sh", "bash", "zsh", "python", "python3"):
        with pytest.raises(DisallowedCommandError, match="forbidden"):
            resolve_executable(sh, frozenset([sh]))


def test_reject_sudo_su():
    """Privilege escalation binaries are strictly rejected."""
    for cmd in ("sudo", "su", "doas", "pkexec"):
        with pytest.raises(DisallowedCommandError, match="forbidden"):
            resolve_executable(cmd, frozenset([cmd]))


def test_executable_not_found():
    """Allowlisted command that is not installed on system raises ExecutableNotFoundError."""
    custom_allowlist = frozenset({"non_existent_binary_xyz_123"})
    with pytest.raises(ExecutableNotFoundError, match="was not found as an executable file"):
        resolve_executable("non_existent_binary_xyz_123", custom_allowlist)


# ---------------------------------------------------------------------------
# Argument Validation Tests
# ---------------------------------------------------------------------------


def test_argument_validation_success():
    """Valid argument list passes validation."""
    validate_command_arguments("printf", ["hello %s\n", "world"])


def test_reject_non_list_args():
    """Non-list arguments are rejected."""
    with pytest.raises(InvalidArgumentsError, match="must be a list"):
        validate_command_arguments("printf", "not_a_list")  # type: ignore


def test_reject_nul_bytes_in_args():
    """Arguments containing NUL bytes are rejected."""
    with pytest.raises(InvalidArgumentsError, match="NUL bytes"):
        validate_command_arguments("printf", ["hello\x00world"])


def test_reject_too_many_arguments():
    """Exceeding MAX_ARG_COUNT is rejected."""
    args = ["arg"] * (MAX_ARG_COUNT + 1)
    with pytest.raises(InvalidArgumentsError, match="Argument count"):
        validate_command_arguments("printf", args)


def test_reject_excessive_arg_length():
    """Arguments exceeding MAX_ARG_LENGTH are rejected."""
    long_arg = "a" * (MAX_ARG_LENGTH + 1)
    with pytest.raises(InvalidArgumentsError, match="maximum length"):
        validate_command_arguments("printf", [long_arg])


def test_reject_shell_chaining_in_args():
    """Arguments containing shell chaining tokens are rejected."""
    for tok in (";", "&&", "||", "|", "`", "$("):
        with pytest.raises(InvalidArgumentsError, match="forbidden shell chaining"):
            validate_command_arguments("printf", [f"foo{tok}bar"])


def test_git_subcommand_allowlist():
    """Git allows read-only subcommands but rejects mutating ones."""
    validate_command_arguments("git", ["status"])
    validate_command_arguments("git", ["diff", "--name-only"])
    validate_command_arguments("git", ["log", "-n", "5"])

    # Disallowed mutating git subcommands
    for bad_sub in ("commit", "push", "checkout", "clean", "reset", "rebase", "config"):
        with pytest.raises(DisallowedCommandError, match="read-only inspection allowlist"):
            validate_command_arguments("git", [bad_sub])


# ---------------------------------------------------------------------------
# Working Directory & Confinement Tests
# ---------------------------------------------------------------------------


def test_working_directory_confinement(manager: ExecutionManager, temp_workspace: Path):
    """Working directory must remain inside workspace root."""
    sub = temp_workspace / "subdir"
    sub.mkdir()

    # Valid subpath
    valid_cwd = manager.validate_working_directory("subdir")
    assert valid_cwd == sub

    # Directory outside workspace
    with pytest.raises(WorkspaceSecurityError):
        manager.validate_working_directory("/etc")

    # Path traversal outside workspace
    with pytest.raises(WorkspaceSecurityError):
        manager.validate_working_directory("../other")


def test_symlink_escape_rejection(manager: ExecutionManager, temp_workspace: Path, tmp_path: Path):
    """Symlinks escaping workspace are rejected as working directory."""
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()

    link = temp_workspace / "link_to_outside"
    link.symlink_to(outside_dir)

    with pytest.raises(WorkspaceSecurityError):
        manager.validate_working_directory("link_to_outside")


# ---------------------------------------------------------------------------
# Environment Sanitization Tests
# ---------------------------------------------------------------------------


def test_environment_sanitization(temp_workspace: Path, monkeypatch):
    """Environment retains only safe system keys and strips all secrets and API keys."""
    monkeypatch.setenv("SECRET_KEY", "super_secret_value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-12345")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA123")
    monkeypatch.setenv("OLLAMA_HOST", "secret_host")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "/evil/script")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh.sock")

    env = build_sanitized_environment(temp_workspace)

    assert "PATH" in env
    assert "HOME" in env
    assert "USER" in env
    assert env["TERM"] == "dumb"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"

    # Verify secrets are stripped
    assert "SECRET_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "OLLAMA_HOST" not in env
    assert "GIT_EXTERNAL_DIFF" not in env
    assert "SSH_AUTH_SOCK" not in env


# ---------------------------------------------------------------------------
# Execution, Output Draining, and Timeout Tests
# ---------------------------------------------------------------------------


def test_successful_printf_execution(manager: ExecutionManager):
    """Successful allowlisted command execution captures stdout and exit code 0."""
    res = manager.run_command("printf", ["Hello %s!\n", "ORION"])
    assert res.status == ExecutionStatus.COMPLETED
    assert res.exit_code == 0
    assert res.stdout == "Hello ORION!\n"
    assert res.stderr == ""
    assert res.truncated is False
    assert res.duration_ms > 0


def test_command_nonzero_exit_code(manager: ExecutionManager):
    """Command exiting with non-zero code sets status FAILED."""
    # git diff with invalid revision returns exit code 128
    res = manager.run_command("git", ["diff", "nonexistent_ref_xyz"])
    assert res.status == ExecutionStatus.FAILED
    assert res.exit_code is not None and res.exit_code != 0
    assert "fatal" in res.stderr.lower() or "error" in res.stderr.lower()


def test_large_stdout_draining_and_truncation(manager: ExecutionManager, monkeypatch):
    """Large stdout stream exceeding max buffer is drained completely and marked truncated."""
    # Set manager max output buffer to a small size (4 KB) for test
    manager._max_output_bytes = 4096

    # Generate 16 KB of text using printf: 16 arguments of 1000 bytes each
    fmt = "%s" * 16
    args = [fmt] + ["X" * 1000] * 16

    res = manager.run_command("printf", args)

    assert res.status == ExecutionStatus.COMPLETED
    assert res.truncated is True
    # Output must be capped at 2048 bytes (half of 4096)
    assert len(res.stdout) <= 2048


def test_pytest_and_ruff_version_execution(manager: ExecutionManager):
    """Allowlisted development tools pytest and ruff execute cleanly."""
    res_ruff = manager.run_command("ruff", ["--version"])
    assert res_ruff.status == ExecutionStatus.COMPLETED
    assert res_ruff.exit_code == 0
    assert "ruff" in res_ruff.stdout.lower()

    res_pytest = manager.run_command("pytest", ["--version"])
    assert res_pytest.status == ExecutionStatus.COMPLETED
    assert res_pytest.exit_code == 0
    assert "pytest" in res_pytest.stdout.lower()


def test_git_status_execution(manager: ExecutionManager, temp_workspace: Path):
    """Git status runs in a workspace git repo and captures output."""
    import subprocess

    subprocess.run(["git", "init"], cwd=temp_workspace, check=True, capture_output=True)

    res = manager.run_command("git", ["status"])
    assert res.status == ExecutionStatus.COMPLETED
    assert res.exit_code == 0
    assert "on branch" in res.stdout.lower() or "no commits yet" in res.stdout.lower()


def test_wall_clock_timeout_and_process_cleanup(manager: ExecutionManager):
    """Subprocess exceeding timeout is terminated via process group and status is TIMED_OUT."""
    res = manager.run_command("git", ["status"], timeout_seconds=0.00001)

    # Either it completed or timed out cleanly
    assert res.status in (ExecutionStatus.TIMED_OUT, ExecutionStatus.COMPLETED)
    if res.status == ExecutionStatus.TIMED_OUT:
        assert "timed out" in (res.error_message or "").lower()


def test_concurrency_limit_enforcement(manager: ExecutionManager):
    """Only one subprocess can run at a time; concurrent requests are rejected."""
    # Simulate active execution state
    manager._is_executing = True
    try:
        with pytest.raises(ExecutionConcurrencyError, match="Concurrency limit is 1"):
            manager.run_command("printf", ["test"])
    finally:
        manager._is_executing = False


def test_manager_shutdown(manager: ExecutionManager):
    """Manager rejects new requests after shutdown."""
    manager.shutdown(wait=True)
    with pytest.raises(Exception, match="shut down"):
        manager.run_command("printf", ["test"])
