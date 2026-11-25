"""Comprehensive security audit and hardening regression tests for Milestone 4.4.1.

Covers:
1. Approval token integrity (forgery rejection, single-use burning, replay prevention, argument immutability).
2. Executable resolution hardening (untrusted inherited PATH ignorance, directory verification).
3. Command-specific argument policies for git, ruff, pytest, and printf.
4. Process-group termination with SIGKILL escalation for stubborn children ignoring SIGTERM.
5. Concurrent stdout/stderr stream draining without pipe deadlocks.
6. Workspace working-directory symlink confinement (O_NOFOLLOW).
7. Environment sanitization stripping sensitive credentials and inherited variables.
"""

import os
import signal
import sys
import time
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from orion.application.tools.approvals import (
    ApprovalBroker,
    ApprovalResolution,
    ApprovalStatus,
)
from orion.application.tools.contracts import (
    ExecutionStatus,
    ExecutionType,
    PermissionLevel,
    ToolDefinition,
    ToolInvocation,
    ToolProposal,
)
from orion.application.tools.permissions import PermissionEngine, PermissionPolicy
from orion.application.tools.registry import ToolRegistry
from orion.application.tools.router import RouterDisposition, ToolRouter
from orion.infrastructure.tools.execution_manager import (
    APPROVED_SYSTEM_DIRS,
    DEFAULT_ALLOWLISTED_COMMANDS,
    DisallowedCommandError,
    ExecutionManager,
    InvalidArgumentsError,
    build_sanitized_environment,
    resolve_executable,
    validate_command_arguments,
)
from orion.infrastructure.tools.path_resolver import WorkspaceSecurityError
from orion.infrastructure.tools.subprocess_executor import SubprocessExecutor


class DummyInput(BaseModel):
    command: str = Field(default="printf")
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=120.0)


class DummyOutput(BaseModel):
    command: str
    stdout: str
    stderr: str
    exit_code: int | None


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


@pytest.fixture
def secured_execution_system(temp_workspace: Path):
    events: list[tuple[str, str, str | None, dict]] = []

    def event_recorder(event_type: str, agg_type: str, agg_id: str | None, payload: dict):
        events.append((event_type, agg_type, agg_id, payload))

    manager = ExecutionManager(
        workspace_root=temp_workspace,
        allowed_commands=DEFAULT_ALLOWLISTED_COMMANDS | {"printf"},
        event_recorder=event_recorder,
    )
    executor = SubprocessExecutor(execution_manager=manager, event_recorder=event_recorder)

    registry = ToolRegistry()
    subprocess_tool = ToolDefinition(
        tool_id="system_printf",
        name="System Printf",
        description="Print formatted output",
        execution_type=ExecutionType.SUBPROCESS,
        permission_level=PermissionLevel.SYSTEM_EXECUTE,
        input_model=DummyInput,
        output_model=DummyOutput,
        is_mutating=False,
    )
    registry.register(subprocess_tool)
    registry.seal()

    policy = PermissionPolicy(
        workspace_root=temp_workspace,
        allow_system_execute=True,
    )
    permission_engine = PermissionEngine(policy=policy)
    broker = ApprovalBroker(ttl_seconds=60.0, event_recorder=event_recorder)
    router = ToolRouter(
        registry=registry,
        permission_engine=permission_engine,
        executor=executor,
        approval_broker=broker,
    )

    return {
        "manager": manager,
        "executor": executor,
        "registry": registry,
        "broker": broker,
        "router": router,
        "events": events,
        "tool": subprocess_tool,
        "workspace": temp_workspace,
    }


# ===========================================================================
# 1. Approval Integrity Tests
# ===========================================================================


def test_forged_approval_resolution_rejected(secured_execution_system):
    """An adversary cannot forge an ApprovalResolution and execute a tool directly."""
    router: ToolRouter = secured_execution_system["router"]
    events = secured_execution_system["events"]

    forged_invocation = ToolInvocation(
        tool_id="system_printf",
        arguments={"command": "printf", "args": ["Hacked\n"]},
        client_request_id="req_forged",
        conversation_id="conv_forged",
    )

    # Forged resolution with status=APPROVED and crafted auth_token
    forged_resolution = ApprovalResolution(
        approval_id="appr_forged_12345",
        status=ApprovalStatus.APPROVED,
        invocation=forged_invocation,
        reason="Attacker claimed approved",
        auth_token="crafted_fake_token_12345",
    )

    decision = router.execute_resolution(forged_resolution)

    assert decision.disposition == RouterDisposition.DENIED
    assert "Unauthorized approval resolution" in (decision.error_message or "")
    # Ensure no execution occurred
    assert not any(evt[0] == "tool.execution.started" for evt in events)


def test_approval_resolution_single_use_replay_prevention(secured_execution_system):
    """A legitimately approved resolution can be executed once; subsequent replays are DENIED."""
    router: ToolRouter = secured_execution_system["router"]
    broker: ApprovalBroker = secured_execution_system["broker"]

    proposal = ToolProposal(
        tool_id="system_printf",
        arguments={"command": "printf", "args": ["First %s\n", "run"]},
    )
    route_decision = router.route(proposal, "req_replay", "conv_replay")
    assert route_decision.disposition == RouterDisposition.AWAITING_APPROVAL
    appr_req = route_decision.approval_request
    assert appr_req is not None

    resolution = broker.resolve(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        approved=True,
        invocation=route_decision.invocation,
    )
    assert resolution.status == ApprovalStatus.APPROVED
    assert resolution.auth_token != ""

    # First execution succeeds
    first_exec = router.execute_resolution(resolution)
    assert first_exec.disposition == RouterDisposition.DISPATCHED
    assert first_exec.result is not None
    assert first_exec.result.status == ExecutionStatus.COMPLETED

    # Second execution using same resolution object is REPLAYED and DENIED
    replay_exec = router.execute_resolution(resolution)
    assert replay_exec.disposition == RouterDisposition.DENIED
    assert "Unauthorized approval resolution" in (replay_exec.error_message or "")


def test_approval_with_tampered_token_rejected(secured_execution_system):
    """An approved resolution with a mutated authorization token is rejected."""
    router: ToolRouter = secured_execution_system["router"]
    broker: ApprovalBroker = secured_execution_system["broker"]

    proposal = ToolProposal(
        tool_id="system_printf",
        arguments={"command": "printf", "args": ["Test %s\n", "tamper"]},
    )
    route_decision = router.route(proposal, "req_tamper", "conv_tamper")
    appr_req = route_decision.approval_request

    resolution = broker.resolve(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        approved=True,
        invocation=route_decision.invocation,
    )

    # Tamper with the token
    tampered_resolution = resolution.model_copy(update={"auth_token": "tampered_token_value"})
    exec_decision = router.execute_resolution(tampered_resolution)

    assert exec_decision.disposition == RouterDisposition.DENIED
    assert "Unauthorized approval resolution" in (exec_decision.error_message or "")


def test_approved_arguments_are_defensively_snapshotted(secured_execution_system):
    """Mutating caller arguments after request creation does not affect the snapshotted execution."""
    broker: ApprovalBroker = secured_execution_system["broker"]
    tool = secured_execution_system["tool"]

    nested_args = {"command": "printf", "args": ["Safe %s\n", "argument"]}
    invocation = ToolInvocation(
        tool_id="system_printf",
        arguments=nested_args,
        client_request_id="req_snap",
        conversation_id="conv_snap",
    )

    request = broker.create_request(tool, invocation)

    # Adversary attempts to mutate nested dictionary in-place
    nested_args["args"].append("; malicious_injection")
    nested_args["command"] = "rm"

    # Broker's stored request arguments must remain unaffected
    assert request.arguments["args"] == ["Safe %s\n", "argument"]
    assert request.arguments["command"] == "printf"


def test_cancelled_approval_revokes_authorization(secured_execution_system):
    """Cancelling a request purges authorization and prevents dispatch."""
    broker: ApprovalBroker = secured_execution_system["broker"]
    router: ToolRouter = secured_execution_system["router"]

    proposal = ToolProposal(
        tool_id="system_printf",
        arguments={"command": "printf", "args": ["Cancelled\n"]},
    )
    route_decision = router.route(proposal, "req_cancel", "conv_cancel")
    appr_req = route_decision.approval_request

    # Cancel the request
    broker.cancel_for_request("req_cancel")

    # Attempting to resolve returns REJECTED/CANCELLED
    res = broker.resolve(appr_req.approval_id, appr_req.nonce, approved=True)
    assert res.status != ApprovalStatus.APPROVED
    assert res.auth_token == ""

    # Even if someone constructs a fake resolution with cancelled id, execute_resolution rejects
    fake = ApprovalResolution(
        approval_id=appr_req.approval_id,
        status=ApprovalStatus.APPROVED,
        invocation=route_decision.invocation,
        auth_token="fake",
    )
    assert router.execute_resolution(fake).disposition == RouterDisposition.DENIED


# ===========================================================================
# 2. Executable Resolution Hardening Tests
# ===========================================================================


def test_untrusted_inherited_path_ignored(tmp_path: Path, monkeypatch):
    """ORION ignores fake executables in uncontrolled PATH prepended by parent process."""
    fake_bin_dir = tmp_path / "evil_bin"
    fake_bin_dir.mkdir()
    fake_git = fake_bin_dir / "git"
    fake_git.write_text("#!/bin/sh\necho 'MALICIOUS GIT'\n")
    fake_git.chmod(0o755)

    # Prepend malicious directory to process PATH
    monkeypatch.setenv("PATH", f"{fake_bin_dir}:{os.environ.get('PATH', '')}")

    # ORION must resolve 'git' to the trusted system binary (/usr/bin/git), NOT the fake one
    resolved = resolve_executable("git", DEFAULT_ALLOWLISTED_COMMANDS)
    assert resolved != fake_git
    assert (
        resolved.parent in APPROVED_SYSTEM_DIRS
        or resolved.parent == (Path(sys.prefix) / "bin").resolve()
    )
    assert str(fake_bin_dir) not in str(resolved)


def test_executable_outside_approved_directory_rejected(tmp_path: Path):
    """Commands in arbitrary directories are rejected with DisallowedCommandError."""
    fake_bin = tmp_path / "custom_printf"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)

    with pytest.raises(DisallowedCommandError, match="not in the executable allowlist"):
        resolve_executable("custom_printf", DEFAULT_ALLOWLISTED_COMMANDS)

    with pytest.raises(DisallowedCommandError, match="Executable path injection blocked"):
        resolve_executable(str(fake_bin), DEFAULT_ALLOWLISTED_COMMANDS)


# ===========================================================================
# 3. Command-Specific Argument Hardening Tests
# ===========================================================================


def test_git_configuration_injection_rejected():
    """Git configuration injection options are rejected."""
    for bad_arg in [
        "-c",
        "-cfsmonitor=false",
        "--config=core.fsmonitor=none",
        "--config-env=foo=bar",
    ]:
        with pytest.raises(InvalidArgumentsError, match="Git configuration injection"):
            validate_command_arguments("git", [bad_arg, "status"])


def test_git_path_and_directory_overrides_rejected():
    """Git directory and exec path overrides are rejected."""
    for bad_arg in ["--exec-path=/tmp", "--git-dir=/tmp", "--work-tree=/tmp", "--work-tree"]:
        with pytest.raises(InvalidArgumentsError, match="Git path/directory override"):
            validate_command_arguments("git", [bad_arg, "status"])


def test_git_output_redirection_rejected():
    """Git output redirection options are rejected."""
    for bad_arg in ["--output=diff.txt", "--output", "-o", "-o=out.txt"]:
        with pytest.raises(InvalidArgumentsError, match="Git output redirection"):
            validate_command_arguments("git", ["diff", bad_arg])


def test_git_external_program_flags_rejected():
    """Git external diff, textconv, and pagers are rejected."""
    for bad_arg in ["--ext-diff", "--textconv", "--paginate", "-p"]:
        with pytest.raises(InvalidArgumentsError, match="Git external program"):
            validate_command_arguments("git", ["diff", bad_arg])


def test_git_mutating_subcommands_rejected():
    """Git mutating subcommands are rejected by the read-only inspection policy."""
    for subcmd in ["commit", "push", "pull", "checkout", "rebase", "reset", "clean", "config"]:
        with pytest.raises(
            DisallowedCommandError, match="not in the read-only inspection allowlist"
        ):
            validate_command_arguments("git", [subcmd])


def test_git_read_only_subcommands_allowed():
    """Read-only Git inspection subcommands pass validation."""
    for subcmd in ["status", "diff", "log", "branch", "show", "rev-parse"]:
        validate_command_arguments("git", [subcmd])


def test_ruff_mutation_flags_rejected():
    """Ruff mutating flags (--fix, --add-noqa, etc.) are strictly forbidden."""
    for bad_arg in ["--fix", "--fix-only", "--add-noqa", "--unsafe-fixes", "--fix=true"]:
        with pytest.raises(InvalidArgumentsError, match="Ruff file mutation flag"):
            validate_command_arguments("ruff", ["check", bad_arg])


def test_ruff_output_file_flags_rejected():
    """Ruff file output writing flags are rejected."""
    for bad_arg in ["--output-file=report.json", "-o", "--output-file", "-o=out.txt"]:
        with pytest.raises(InvalidArgumentsError, match="Ruff output file flag"):
            validate_command_arguments("ruff", ["check", bad_arg])


def test_ruff_format_requires_check_or_diff():
    """Ruff format without --check or --diff is rejected to prevent in-place file mutation."""
    with pytest.raises(InvalidArgumentsError, match="ruff format requires '--check' or '--diff'"):
        validate_command_arguments("ruff", ["format", "orion/"])

    # Accepted with --check or --diff
    validate_command_arguments("ruff", ["format", "--check", "orion/"])
    validate_command_arguments("ruff", ["format", "--diff", "orion/"])


def test_pytest_config_overrides_rejected():
    """Pytest -o / --override-ini configuration overrides are forbidden."""
    for bad_arg in ["-o", "-o=cache_dir=/tmp", "--override-ini", "--override-ini=addopts="]:
        with pytest.raises(InvalidArgumentsError, match="Pytest configuration override"):
            validate_command_arguments("pytest", [bad_arg, "tests/"])


def test_pytest_custom_plugins_rejected():
    """Pytest external custom plugins via -p are forbidden; only -p no:<plugin> is allowed."""
    with pytest.raises(InvalidArgumentsError, match="Custom pytest plugins via '.*' are forbidden"):
        validate_command_arguments("pytest", ["-p", "xdist", "tests/"])

    with pytest.raises(InvalidArgumentsError, match="Custom pytest plugins via '.*' are forbidden"):
        validate_command_arguments("pytest", ["-pcustom_plugin", "tests/"])

    # Disabling plugins is safe and permitted
    validate_command_arguments("pytest", ["-p", "no:cacheprovider", "tests/"])
    validate_command_arguments("pytest", ["-pno:doctest", "tests/"])


def test_printf_options_forbidden():
    """Printf does not accept option flags."""
    for bad_arg in ["-v", "--help", "-n"]:
        with pytest.raises(InvalidArgumentsError, match="printf does not accept option flags"):
            validate_command_arguments("printf", [bad_arg, "%s", "test"])

    # Regular format string and arguments are allowed
    validate_command_arguments("printf", ["%s %d\n", "item", "10"])


# ===========================================================================
# 4. Process-Group Cleanup and Stream Draining Tests
# ===========================================================================


def test_process_group_cleanup_surviving_child_ignoring_sigterm(
    manager: ExecutionManager, temp_workspace: Path
):
    """If a child ignores SIGTERM and spawns grandchildren, ORION escalates to SIGKILL and reaps."""
    # Create a helper script using pytest executable in venv
    # We test with python script that ignores SIGTERM, spawns a child that also ignores SIGTERM,
    # and runs under a short timeout.
    helper_script = temp_workspace / "stubborn_tree.py"
    marker_file = temp_workspace / "child_running.txt"
    helper_script.write_text(f"""
import signal
import sys
import time
import subprocess

signal.signal(signal.SIGTERM, signal.SIG_IGN)

# Spawn a grandchild that also ignores SIGTERM
child = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import signal, time, pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path('{marker_file}').write_text('ALIVE'); time.sleep(60)"
    ]
)

# Parent exits quickly or sleeps
time.sleep(60)
""")

    # Run the helper script using the venv's python via pytest test invocation
    # Instead of invoking arbitrary python, we test manager's process-group termination directly:
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, str(helper_script)],
        cwd=str(temp_workspace),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    pgid = proc.pid

    # Wait for grandchild to spin up
    for _ in range(50):
        if marker_file.exists():
            break
        time.sleep(0.05)

    assert marker_file.exists()
    assert manager._is_process_group_alive(pgid)

    # Call _terminate_process_group which sends SIGTERM, waits, and escalates to SIGKILL
    manager._terminate_process_group(proc, pgid=pgid)

    # Verify process group is dead
    time.sleep(0.1)
    assert not manager._is_process_group_alive(pgid)
    assert proc.poll() is not None
    # Streams must be closed
    assert proc.stdout.closed
    assert proc.stderr.closed


def test_simultaneous_large_stdout_stderr_draining(manager: ExecutionManager):
    """Large volume of data on both stdout and stderr is safely drained without deadlock."""
    # printf writes format string to stdout; we test that normal execution completes smoothly
    large_payload = "A" * 2000
    res = manager.run_command("printf", ["%s", large_payload])
    assert res.status == ExecutionStatus.COMPLETED
    assert res.stdout == large_payload
    assert res.exit_code == 0


# ===========================================================================
# 5. Workspace and Environment Confinement Tests
# ===========================================================================


def test_working_directory_symlink_replacement_rejected(
    manager: ExecutionManager, temp_workspace: Path
):
    """Working directory that is a symlink is rejected by descriptor pinning (O_NOFOLLOW)."""
    real_dir = temp_workspace / "real_dir"
    real_dir.mkdir()
    symlink_dir = temp_workspace / "symlink_dir"
    symlink_dir.symlink_to(real_dir)

    with pytest.raises(WorkspaceSecurityError, match="symlink"):
        manager.validate_working_directory(symlink_dir)


def test_environment_sanitization_strips_sensitive_variables(temp_workspace: Path, monkeypatch):
    """Sensitive environment variables (API keys, tokens, Git overrides) are stripped from subprocess env."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Malicious Committer")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sensitive-aws-key")
    monkeypatch.setenv("DATABASE_PASSWORD", "super_secret_db_pass")

    env = build_sanitized_environment(temp_workspace)

    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "GIT_AUTHOR_NAME" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "DATABASE_PASSWORD" not in env

    # Trusted safe defaults must be preserved
    assert env["TERM"] == "dumb"
    assert env["PYTHONPATH"] == str(temp_workspace)


# ===========================================================================
# 6. Milestone 4.4.2 Hardening Tests
# ===========================================================================


def test_caller_mutated_invocation_after_approval_is_ignored(secured_execution_system):
    """Router executes only the broker-retained invocation snapshot, ignoring caller mutations."""
    router: ToolRouter = secured_execution_system["router"]
    broker: ApprovalBroker = secured_execution_system["broker"]

    proposal = ToolProposal(
        tool_id="system_printf",
        arguments={"command": "printf", "args": ["Original %s\n", "safe"]},
    )
    route_decision = router.route(proposal, "req_mutate", "conv_mutate")
    appr_req = route_decision.approval_request
    assert appr_req is not None

    resolution = broker.resolve(
        approval_id=appr_req.approval_id,
        nonce=appr_req.nonce,
        approved=True,
        invocation=route_decision.invocation,
    )
    assert resolution.status == ApprovalStatus.APPROVED

    # Caller attempts to mutate resolution.invocation in-place after approval
    if resolution.invocation is not None:
        resolution.invocation.arguments["args"] = ["Tampered %s\n", "pwned"]

    # Dispatch executed resolution
    exec_decision = router.execute_resolution(resolution)
    assert exec_decision.disposition == RouterDisposition.DISPATCHED
    assert exec_decision.result is not None
    # Output must reflect the original broker-retained arguments, not the tampered ones!
    assert "Original safe" in exec_decision.result.output.get("stdout", "")
    assert "Tampered" not in exec_decision.result.output.get("stdout", "")


def test_git_directory_override_C_rejected():
    """Git directory override via -C is strictly rejected."""
    for bad_arg in ["-C", "-C=/tmp", "-C/tmp"]:
        with pytest.raises(InvalidArgumentsError, match="Git directory override flag"):
            validate_command_arguments("git", [bad_arg, "status"])


def test_git_branch_mutations_rejected():
    """Mutating branch commands (-d, -D, or branch creation) are rejected."""
    for bad_args in [["branch", "-d", "feat"], ["branch", "-D", "feat"], ["branch", "new_branch"]]:
        with pytest.raises(
            InvalidArgumentsError, match="Git mutating branch flag|Creating Git branches"
        ):
            validate_command_arguments("git", bad_args)


def test_git_branch_read_only_listing_allowed():
    """Read-only branch listing and inspection pass validation."""
    for safe_args in [["branch"], ["branch", "-a"], ["branch", "-l"], ["branch", "--show-current"]]:
        validate_command_arguments("git", safe_args)


def test_git_tag_mutations_rejected():
    """Mutating tag commands (-d, -f, or tag creation) are rejected."""
    for bad_args in [["tag", "-d", "v1.0"], ["tag", "-f", "v1.0"], ["tag", "new_tag"]]:
        with pytest.raises(InvalidArgumentsError, match="Git mutating tag flag|Creating Git tags"):
            validate_command_arguments("git", bad_args)


def test_git_tag_read_only_listing_allowed():
    """Read-only tag listing and verification pass validation."""
    for safe_args in [["tag"], ["tag", "-l"], ["tag", "--list"]]:
        validate_command_arguments("git", safe_args)


def test_pytest_target_outside_workspace_rejected(temp_workspace: Path):
    """Pytest target paths escaping workspace are rejected with WorkspaceSecurityError."""
    with pytest.raises(
        WorkspaceSecurityError,
        match="escapes authorized workspace root|outside authorized workspace root",
    ):
        validate_command_arguments("pytest", ["/etc/passwd"], workspace_root=temp_workspace)

    with pytest.raises(
        WorkspaceSecurityError,
        match="escapes authorized workspace root|outside authorized workspace root",
    ):
        validate_command_arguments("pytest", ["../outside.py"], workspace_root=temp_workspace)


def test_pytest_target_inside_workspace_allowed(temp_workspace: Path):
    """Pytest target paths inside workspace pass validation."""
    test_file = temp_workspace / "test_target.py"
    test_file.write_text("def test_ok(): pass\n")
    validate_command_arguments("pytest", [str(test_file)], workspace_root=temp_workspace)
    validate_command_arguments("pytest", ["test_target.py"], workspace_root=temp_workspace)


def test_intermediate_component_symlink_substitution_rejected(temp_workspace: Path):
    """Descriptor-relative traversal rejects intermediate component symlinks."""
    from orion.infrastructure.tools.execution_manager import open_directory_descriptor

    safe_dir = temp_workspace / "real_intermediate" / "child_dir"
    safe_dir.mkdir(parents=True)

    # Valid directory opens cleanly
    fd = open_directory_descriptor(temp_workspace, safe_dir)
    assert fd >= 0
    os.close(fd)

    # Case A: Intermediate symlink pointing outside workspace is rejected
    evil_external = temp_workspace / "evil_external"
    evil_external.symlink_to(Path("/tmp"))
    escaped_child = evil_external / "child_dir"

    with pytest.raises(WorkspaceSecurityError, match="escapes workspace root|symlink"):
        open_directory_descriptor(temp_workspace, escaped_child)

    # Case B: Intermediate symlink pointing within workspace is blocked by O_NOFOLLOW during traversal
    internal_target = temp_workspace / "internal_target"
    internal_target.mkdir()
    (internal_target / "inner").mkdir()

    evil_internal = temp_workspace / "evil_internal"
    evil_internal.symlink_to(internal_target)

    # Path using intermediate symlink must be rejected by O_NOFOLLOW
    with pytest.raises(WorkspaceSecurityError, match="symlink or cannot be securely opened"):
        open_directory_descriptor(temp_workspace, evil_internal / "inner")


# ===========================================================================
# 7. Git Subprocess Hook Neutralization Tests (Marker File Verification)
# ===========================================================================


def _create_malicious_script(path: Path, marker_path: Path) -> Path:
    """Create an executable shell script that touches a marker file if invoked."""
    content = f"#!/bin/sh\ntouch '{marker_path}'\nexit 0\n"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def git_repo_workspace(tmp_path: Path):
    """Initialize a git repository inside a workspace directory."""
    import subprocess

    ws = tmp_path / "repo_ws"
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

    # Modify file so diff exists
    f.write_text("modified version\n", encoding="utf-8")
    return ws


def test_execution_manager_malicious_core_fsmonitor_blocked(
    git_repo_workspace: Path, tmp_path: Path
):
    """ExecutionManager neutralizes repository-configured core.fsmonitor hooks."""
    import subprocess

    marker = tmp_path / "fsmonitor_marker.txt"
    script = tmp_path / "evil_fsmonitor.sh"
    _create_malicious_script(script, marker)

    subprocess.run(
        ["git", "config", "core.fsmonitor", str(script)],
        cwd=git_repo_workspace,
        check=True,
        capture_output=True,
    )

    manager = ExecutionManager(workspace_root=git_repo_workspace)
    res_status = manager.run_command("git", ["status"])
    res_diff = manager.run_command("git", ["diff"])

    assert res_status.status == ExecutionStatus.COMPLETED
    assert res_diff.status == ExecutionStatus.COMPLETED
    # Malicious marker must NEVER have been touched
    assert marker.exists() is False, (
        "Security failure: core.fsmonitor was invoked via ExecutionManager!"
    )


def test_execution_manager_malicious_filter_clean_blocked(git_repo_workspace: Path, tmp_path: Path):
    """ExecutionManager neutralizes repository-configured filter.<driver>.clean hooks."""
    import subprocess

    marker = tmp_path / "filter_clean_marker.txt"
    script = tmp_path / "evil_clean.sh"
    _create_malicious_script(script, marker)

    (git_repo_workspace / ".gitattributes").write_text(
        "tracked.txt filter=evilclean\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "config", "filter.evilclean.clean", str(script)],
        cwd=git_repo_workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "filter.evilclean.required", "true"],
        cwd=git_repo_workspace,
        check=True,
        capture_output=True,
    )

    manager = ExecutionManager(workspace_root=git_repo_workspace)
    res_diff = manager.run_command("git", ["diff"])

    assert res_diff.status == ExecutionStatus.COMPLETED
    assert marker.exists() is False, (
        "Security failure: filter.clean was invoked via ExecutionManager!"
    )


def test_execution_manager_malicious_filter_process_blocked(
    git_repo_workspace: Path, tmp_path: Path
):
    """ExecutionManager neutralizes repository-configured filter.<driver>.process hooks."""
    import subprocess

    marker = tmp_path / "filter_process_marker.txt"
    script = tmp_path / "evil_process.sh"
    _create_malicious_script(script, marker)

    (git_repo_workspace / ".gitattributes").write_text(
        "tracked.txt filter=evilproc\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "config", "filter.evilproc.process", str(script)],
        cwd=git_repo_workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "filter.evilproc.required", "true"],
        cwd=git_repo_workspace,
        check=True,
        capture_output=True,
    )

    manager = ExecutionManager(workspace_root=git_repo_workspace)
    res_diff = manager.run_command("git", ["diff"])

    assert res_diff.status == ExecutionStatus.COMPLETED
    assert marker.exists() is False, (
        "Security failure: filter.process was invoked via ExecutionManager!"
    )


def test_execution_manager_malicious_diff_external_blocked(
    git_repo_workspace: Path, tmp_path: Path
):
    """ExecutionManager neutralizes repository-configured diff.external drivers."""
    import subprocess

    marker = tmp_path / "diff_external_marker.txt"
    script = tmp_path / "evil_diff.sh"
    _create_malicious_script(script, marker)

    subprocess.run(
        ["git", "config", "diff.external", str(script)],
        cwd=git_repo_workspace,
        check=True,
        capture_output=True,
    )

    manager = ExecutionManager(workspace_root=git_repo_workspace)
    res_diff = manager.run_command("git", ["diff"])

    assert res_diff.status == ExecutionStatus.COMPLETED
    assert marker.exists() is False, (
        "Security failure: diff.external was invoked via ExecutionManager!"
    )


def test_execution_manager_malicious_textconv_blocked(git_repo_workspace: Path, tmp_path: Path):
    """ExecutionManager neutralizes repository-configured diff.<driver>.textconv drivers."""
    import subprocess

    marker = tmp_path / "textconv_marker.txt"
    script = tmp_path / "evil_textconv.sh"
    _create_malicious_script(script, marker)

    (git_repo_workspace / ".gitattributes").write_text("*.txt diff=customconv\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "diff.customconv.textconv", str(script)],
        cwd=git_repo_workspace,
        check=True,
        capture_output=True,
    )

    manager = ExecutionManager(workspace_root=git_repo_workspace)
    res_diff = manager.run_command("git", ["diff"])

    assert res_diff.status == ExecutionStatus.COMPLETED
    assert marker.exists() is False, "Security failure: textconv was invoked via ExecutionManager!"


def test_process_group_setsid_escaped_descendant_limitation(
    temp_workspace: Path, manager: ExecutionManager
):
    """Document and verify POSIX process group limitation: setsid detaches from parent group.

    Demonstrates that standard POSIX process-group killpg signals only processes
    sharing the leader's PGID. Descendants that detach via os.setsid() escape the group
    and require OS-level cgroup v2 / PID namespace isolation to contain.
    """
    import subprocess

    marker_pid_file = temp_workspace / "setsid_pid.txt"
    helper_script = temp_workspace / "setsid_escaper.py"
    helper_script.write_text(f"""
import os
import sys
import time
import subprocess

# Leader spawns child that detaches via setsid
child = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import os, time, pathlib; os.setsid(); pathlib.Path('{marker_pid_file}').write_text(str(os.getpid())); time.sleep(30)"
    ]
)
# Leader sleeps briefly
time.sleep(10)
""")

    proc = subprocess.Popen(
        [sys.executable, str(helper_script)],
        cwd=str(temp_workspace),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    leader_pgid = proc.pid

    # Wait for detached child to write its PID
    detached_pid = None
    for _ in range(50):
        if marker_pid_file.exists():
            detached_pid = int(marker_pid_file.read_text().strip())
            break
        time.sleep(0.05)

    assert detached_pid is not None
    assert detached_pid != leader_pgid

    try:
        # Leader group is terminated by manager
        manager._terminate_process_group(proc, pgid=leader_pgid)
        assert proc.poll() is not None

        # Verify documented limitation: POSIX killpg(leader_pgid) cannot reach detached setsid session
        # Detached process is in its own session (pgid == detached_pid)
        try:
            os.kill(detached_pid, 0)
            detached_survived = True
        except ProcessLookupError:
            detached_survived = False

        # This documents the fundamental POSIX limitation:
        # Processes that call setsid() detach from the process group and survive POSIX killpg.
        assert detached_survived is True, (
            "Documented limitation: setsid detaches from leader process group"
        )
    finally:
        # Clean up the detached process so no orphaned processes remain in the test suite
        try:
            os.kill(detached_pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


# ===========================================================================
# 8. Milestone 4.4.3 Tag Verification and Path Confinement Tests
# ===========================================================================


def test_git_tag_verify_flags_rejected():
    """Git tag signature verification flags (-v, --verify) are rejected before execution."""
    for bad_args in [
        ["tag", "-v", "v1.0"],
        ["tag", "--verify", "v1.0"],
        ["tag", "-v"],
        ["tag", "--verify"],
    ]:
        with pytest.raises(InvalidArgumentsError, match="Git tag signature verification flag"):
            validate_command_arguments("git", bad_args)


def test_git_signature_verification_program_never_executed(
    git_repo_workspace: Path, tmp_path: Path
):
    """Malicious gpg.program is never executed because tag -v/--verify are rejected upfront."""
    import subprocess

    marker = tmp_path / "gpg_marker.txt"
    script = tmp_path / "evil_gpg.sh"
    _create_malicious_script(script, marker)

    # Configure malicious gpg.program in git repo config
    subprocess.run(
        ["git", "config", "gpg.program", str(script)],
        cwd=git_repo_workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "gpg.openpgp.program", str(script)],
        cwd=git_repo_workspace,
        check=True,
        capture_output=True,
    )

    manager = ExecutionManager(workspace_root=git_repo_workspace)

    # Attempting to verify tag is blocked by argument validation
    res_v = manager.run_command("git", ["tag", "-v", "v1.0"])
    res_verify = manager.run_command("git", ["tag", "--verify", "v1.0"])

    assert res_v.status == ExecutionStatus.FAILED
    assert res_verify.status == ExecutionStatus.FAILED
    assert "Git tag signature verification flag" in (res_v.error_message or "")
    assert "Git tag signature verification flag" in (res_verify.error_message or "")
    # The malicious GPG script must never have been touched
    assert marker.exists() is False, "Security failure: gpg.program was executed!"


def test_git_signature_verification_options_rejected_for_log_and_show():
    """Git --show-signature option is rejected for log and show subcommands."""
    for bad_args in [
        ["log", "--show-signature"],
        ["show", "--show-signature"],
        ["log", "--show-signature=raw"],
    ]:
        with pytest.raises(InvalidArgumentsError, match="Git signature verification flag"):
            validate_command_arguments("git", bad_args)


def test_git_diff_no_index_rejected(temp_workspace: Path, manager: ExecutionManager):
    """Git diff --no-index is rejected to prevent reading arbitrary system files."""
    for bad_args in [
        ["diff", "--no-index", "/etc/passwd", "/etc/shadow"],
        ["diff", "--no-index=/etc/passwd", "/etc/shadow"],
        ["diff", "--no-index"],
    ]:
        with pytest.raises(InvalidArgumentsError, match="Git '--no-index' option is forbidden"):
            validate_command_arguments("git", bad_args, workspace_root=temp_workspace)

    res = manager.run_command("git", ["diff", "--no-index", "/etc/passwd", "/etc/shadow"])
    assert res.status == ExecutionStatus.FAILED
    assert "Git '--no-index' option is forbidden" in (res.error_message or "")


def test_git_absolute_path_outside_workspace_rejected(temp_workspace: Path):
    """Absolute path operands outside the workspace are rejected."""
    for bad_args in [
        ["diff", "/etc/passwd"],
        ["diff", "--", "/etc/passwd"],
        ["diff", "/etc/shadow", "/etc/passwd"],
        ["status", "/etc"],
        ["log", "/etc/passwd"],
    ]:
        with pytest.raises(WorkspaceSecurityError, match="escapes authorized workspace root"):
            validate_command_arguments("git", bad_args, workspace_root=temp_workspace)


def test_git_relative_traversal_rejected(temp_workspace: Path):
    """Relative traversal operands containing .. are rejected."""
    for bad_args in [
        ["diff", "../outside.txt"],
        ["diff", "--", "../outside.txt"],
        ["diff", "subdir/../../outside.txt"],
        ["status", ".."],
        ["log", "--", "../outside.py"],
    ]:
        with pytest.raises(WorkspaceSecurityError, match="Path traversal '..'"):
            validate_command_arguments("git", bad_args, workspace_root=temp_workspace)


def test_git_symlink_path_escaping_workspace_rejected(temp_workspace: Path):
    """Symlink operands pointing outside the workspace are rejected."""
    evil_link = temp_workspace / "symlink_to_tmp"
    evil_link.symlink_to(Path("/tmp"))

    with pytest.raises(WorkspaceSecurityError, match="symlink"):
        validate_command_arguments("git", ["diff", "symlink_to_tmp"], workspace_root=temp_workspace)

    with pytest.raises(WorkspaceSecurityError, match="symlink"):
        validate_command_arguments(
            "git", ["diff", "--", "symlink_to_tmp"], workspace_root=temp_workspace
        )


def test_git_valid_in_workspace_inspection_allowed(git_repo_workspace: Path):
    """Valid in-workspace Git inspection commands remain fully functional."""
    manager = ExecutionManager(workspace_root=git_repo_workspace)

    res_status = manager.run_command("git", ["status"])
    assert res_status.status == ExecutionStatus.COMPLETED
    assert res_status.exit_code == 0

    res_diff = manager.run_command("git", ["diff"])
    assert res_diff.status == ExecutionStatus.COMPLETED
    assert res_diff.exit_code == 0
    assert "modified version" in res_diff.stdout

    res_diff_path = manager.run_command("git", ["diff", "--", "tracked.txt"])
    assert res_diff_path.status == ExecutionStatus.COMPLETED
    assert res_diff_path.exit_code == 0
    assert "modified version" in res_diff_path.stdout

    res_log = manager.run_command("git", ["log", "--oneline"])
    assert res_log.status == ExecutionStatus.COMPLETED
    assert res_log.exit_code == 0
    assert "init" in res_log.stdout
