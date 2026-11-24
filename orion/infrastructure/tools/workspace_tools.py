"""Implementations and executor for read-only workspace inspection tools."""

import logging
import os
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any

from orion.application.tools.contracts import (
    ExecutionStatus,
    ExecutionType,
    PermissionLevel,
    ToolDefinition,
    ToolInvocation,
    ToolResult,
    utc_now,
)
from orion.application.tools.ports import ToolExecutorPort, ToolRegistryPort
from orion.application.tools.router import ToolRouter
from orion.application.tools.workspace_contracts import (
    DirectoryEntry,
    SearchMatch,
    WorkspaceGetWorkingDirectoryInput,
    WorkspaceGetWorkingDirectoryOutput,
    WorkspaceGitDiffInput,
    WorkspaceGitDiffOutput,
    WorkspaceGitStatusInput,
    WorkspaceGitStatusOutput,
    WorkspaceListDirectoryInput,
    WorkspaceListDirectoryOutput,
    WorkspaceReadFileInput,
    WorkspaceReadFileOutput,
    WorkspaceSearchFilesInput,
    WorkspaceSearchFilesOutput,
)
from orion.infrastructure.tools.path_resolver import (
    DEFAULT_EXCLUDED_DIRS,
    WorkspacePathResolver,
    WorkspaceSecurityError,
)

logger = logging.getLogger(__name__)

# Hardened global flags disabling executable hooks, external diffs, and pagers
GIT_HARDENED_GLOBAL_FLAGS: tuple[str, ...] = (
    "-c",
    "core.fsmonitor=",
    "-c",
    "diff.external=",
    "-c",
    "core.pager=cat",
    "-c",
    "diff.mnemonicPrefix=false",
    "--no-pager",
    "--literal-pathspecs",
)


def get_configured_filter_overrides(repo_root: Path, safe_env: dict[str, str]) -> list[str]:
    """Discover all filter and diff driver names in repository config and produce -c overrides.

    Guarantees:
    - Neutralizes filter.<driver>.clean, filter.<driver>.process, filter.<driver>.smudge
    - Overrides filter.<driver>.required=false
    - Neutralizes diff.<driver>.command, diff.<driver>.textconv
    - Runs purely via read-only git config query without invoking any hooks or filters
    """
    overrides: list[str] = []
    try:
        res = subprocess.run(
            ["git", "config", "--get-regexp", r"^(filter|diff)\."],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5.0,
            env=safe_env,
            check=False,
            shell=False,
        )
        if res.returncode == 0:
            filter_drivers: set[str] = set()
            diff_drivers: set[str] = set()
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if not parts:
                    continue
                key = parts[0]
                segments = key.split(".")
                if len(segments) >= 3:
                    section = segments[0]
                    driver_name = ".".join(segments[1:-1])
                    if driver_name:
                        if section == "filter":
                            filter_drivers.add(driver_name)
                        elif section == "diff":
                            diff_drivers.add(driver_name)

            for d in sorted(filter_drivers):
                overrides.extend(
                    [
                        "-c",
                        f"filter.{d}.clean=",
                        "-c",
                        f"filter.{d}.process=",
                        "-c",
                        f"filter.{d}.smudge=",
                        "-c",
                        f"filter.{d}.required=false",
                    ]
                )

            for d in sorted(diff_drivers):
                overrides.extend(
                    [
                        "-c",
                        f"diff.{d}.command=",
                        "-c",
                        f"diff.{d}.textconv=",
                    ]
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to query git config for filter overrides: %s", exc)

    return overrides


def get_safe_git_env(workspace_root: Path) -> dict[str, str]:
    """Construct an isolated, sanitized environment for read-only Git subprocesses.

    Explicitly excludes any inherited parent GIT_* variables (e.g. GIT_EXTERNAL_DIFF,
    GIT_PAGER, GIT_SSH, GIT_EXEC_PATH). Enforces non-interactive execution and disables
    system-wide configurations.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(workspace_root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }


def run_git_command_bounded(
    cmd: list[str],
    cwd: Path,
    max_stdout_bytes: int = 131072,
    max_stderr_bytes: int = 65536,
    timeout_seconds: float = 10.0,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, bool]:
    """Execute a Git subprocess with bounded output consumption and process timeouts.

    Guarantees:
    - shell=False strictly enforced.
    - Captures stdout up to max_stdout_bytes in memory.
    - Drains remaining child output to prevent pipe-write buffer deadlocks.
    - Captures stderr up to max_stderr_bytes without losing error messages.
    - Enforces timeout_seconds; terminates runaway child processes cleanly.
    - Returns (returncode, stdout_str, stderr_str, truncated).
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        shell=False,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None

    os.set_blocking(proc.stdout.fileno(), False)
    os.set_blocking(proc.stderr.fileno(), False)

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_bytes = 0
    stderr_bytes = 0
    truncated = False
    deadline = time.monotonic() + timeout_seconds

    try:
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                proc.wait()
                raise TimeoutError(f"Git command timed out after {timeout_seconds} seconds.")

            events = sel.select(timeout=min(remaining, 0.25))
            for key, _ in events:
                stream = key.fileobj
                tag = key.data
                try:
                    chunk = stream.read(65536)
                except OSError:
                    chunk = b""

                if not chunk:
                    sel.unregister(stream)
                    stream.close()
                    continue

                if tag == "stdout":
                    if stdout_bytes < max_stdout_bytes:
                        keep_len = min(len(chunk), max_stdout_bytes - stdout_bytes)
                        stdout_chunks.append(chunk[:keep_len])
                        stdout_bytes += keep_len
                    if stdout_bytes >= max_stdout_bytes:
                        truncated = True
                    # Discard remaining bytes without accumulating in memory
                else:
                    if stderr_bytes < max_stderr_bytes:
                        keep_len = min(len(chunk), max_stderr_bytes - stderr_bytes)
                        stderr_chunks.append(chunk[:keep_len])
                        stderr_bytes += keep_len
    finally:
        sel.close()

    proc.wait()
    stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    return proc.returncode, stdout_str, stderr_str, truncated


def _is_binary(chunk: bytes) -> bool:
    """Check if byte chunk contains null bytes indicating binary content."""
    return b"\x00" in chunk


class WorkspaceToolsService:
    """Concrete business logic for read-only workspace inspection tools."""

    def __init__(self, path_resolver: WorkspacePathResolver) -> None:
        self._resolver = path_resolver
        self._root = path_resolver.workspace_root

    @property
    def workspace_root(self) -> Path:
        return self._root

    # -----------------------------------------------------------------------
    # 1. workspace_get_working_directory
    # -----------------------------------------------------------------------
    def get_working_directory(
        self, _args: WorkspaceGetWorkingDirectoryInput
    ) -> WorkspaceGetWorkingDirectoryOutput:
        """Return the display path of the authorized workspace root."""
        return WorkspaceGetWorkingDirectoryOutput(workspace_root=str(self._root))

    # -----------------------------------------------------------------------
    # 2. workspace_list_directory
    # -----------------------------------------------------------------------
    def list_directory(self, args: WorkspaceListDirectoryInput) -> WorkspaceListDirectoryOutput:
        """List directory contents deterministically with exclusions and bounded results."""
        target_dir = self._resolver.resolve_path(args.path, must_exist=True, require_dir=True)

        entries: list[DirectoryEntry] = []
        truncated = False

        # Read directory entries without following symlinks
        with os.scandir(target_dir) as scanner:
            scanned_entries = list(scanner)

        # Sort deterministically by name
        scanned_entries.sort(key=lambda e: e.name)

        total_count = 0
        for entry in scanned_entries:
            # Check exclusions
            is_excluded = entry.name in DEFAULT_EXCLUDED_DIRS

            # Determine type without following symlinks
            is_symlink = entry.is_symlink()
            if is_symlink:
                entry_type = "symlink"
                size = None
            elif entry.is_dir(follow_symlinks=False):
                entry_type = "directory"
                size = None
            elif entry.is_file(follow_symlinks=False):
                entry_type = "file"
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    size = None
            else:
                entry_type = "other"
                size = None

            total_count += 1
            if len(entries) < args.max_entries:
                entries.append(
                    DirectoryEntry(
                        name=entry.name,
                        entry_type=entry_type,
                        size_bytes=size,
                        is_excluded=is_excluded,
                    )
                )
            else:
                truncated = True

        rel_display = str(target_dir.relative_to(self._root)) if target_dir != self._root else ""
        return WorkspaceListDirectoryOutput(
            relative_path=rel_display,
            entries=entries,
            total_entries=total_count,
            truncated=truncated,
        )

    # -----------------------------------------------------------------------
    # 3. workspace_read_file
    # -----------------------------------------------------------------------
    def read_file(self, args: WorkspaceReadFileInput) -> WorkspaceReadFileOutput:
        """Read a regular text file up to max_bytes, rejecting symlinks and binaries."""
        # Open safely via descriptor walk (O_NOFOLLOW)
        with self._resolver.open_file_safely(args.path) as fp:
            # Read max_bytes + 1 to detect truncation without loading entire oversized file
            raw = fp.read(args.max_bytes + 1)

            if _is_binary(raw[:8192]):
                raise WorkspaceSecurityError(
                    f"Binary file detected at '{args.path}'. Reading binary files is prohibited."
                )

            truncated = len(raw) > args.max_bytes
            if truncated:
                raw = raw[: args.max_bytes]

            try:
                content = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise WorkspaceSecurityError(
                    f"File '{args.path}' contains invalid UTF-8 encoding: {exc}"
                ) from exc

        return WorkspaceReadFileOutput(
            relative_path=args.path,
            content=content,
            size_bytes=len(raw),
            truncated=truncated,
        )

    # -----------------------------------------------------------------------
    # 4. workspace_search_files
    # -----------------------------------------------------------------------
    def search_files(self, args: WorkspaceSearchFilesInput) -> WorkspaceSearchFilesOutput:
        """Perform literal text search across non-excluded workspace files."""
        search_root = self._resolver.resolve_path(args.path, must_exist=True, require_dir=True)

        matches: list[SearchMatch] = []
        truncated = False
        files_inspected = 0
        max_files_to_inspect = 500
        query = args.query if args.case_sensitive else args.query.lower()

        for dirpath, dirnames, filenames in os.walk(search_root, followlinks=False):
            # Prune excluded directories in-place so os.walk does not recurse into them
            dirnames[:] = [
                d
                for d in dirnames
                if d not in DEFAULT_EXCLUDED_DIRS and not (Path(dirpath) / d).is_symlink()
            ]
            dirnames.sort()
            filenames.sort()

            for fname in filenames:
                if files_inspected >= max_files_to_inspect:
                    truncated = True
                    break

                file_path = Path(dirpath) / fname
                # Never inspect symlinks
                if file_path.is_symlink():
                    continue

                rel_file_str = str(file_path.relative_to(self._root))

                try:
                    with self._resolver.open_file_safely(rel_file_str) as fp:
                        head = fp.read(4096)
                        if _is_binary(head):
                            continue
                        files_inspected += 1
                        # Re-read for text scanning
                        fp.seek(0)
                        raw_data = fp.read(524288)  # max 512 KB per file during search
                        text = raw_data.decode("utf-8", errors="replace")

                        for line_no, line in enumerate(text.splitlines(), start=1):
                            target_line = line if args.case_sensitive else line.lower()
                            if query in target_line:
                                if len(matches) < args.max_results:
                                    # Truncate long lines to 200 chars
                                    snippet = line.strip()
                                    if len(snippet) > 200:
                                        snippet = snippet[:197] + "..."
                                    matches.append(
                                        SearchMatch(
                                            relative_path=rel_file_str,
                                            line_number=line_no,
                                            line_content=snippet,
                                        )
                                    )
                                else:
                                    truncated = True
                                    break
                except (WorkspaceSecurityError, OSError, UnicodeDecodeError):
                    continue

                if truncated:
                    break
            if truncated:
                break

        return WorkspaceSearchFilesOutput(
            query=args.query,
            matches=matches,
            total_matches=len(matches),
            files_inspected=files_inspected,
            truncated=truncated,
        )

    # -----------------------------------------------------------------------
    # 5. workspace_git_status
    # -----------------------------------------------------------------------
    def git_status(self, _args: WorkspaceGitStatusInput) -> WorkspaceGitStatusOutput:
        """Inspect Git working-tree and branch status without modifying state."""
        git_dir = self._root / ".git"
        if not git_dir.exists():
            return WorkspaceGitStatusOutput(
                is_git_repo=False,
                summary="The configured workspace root is not a Git repository.",
            )

        safe_env = get_safe_git_env(self._root)
        filter_overrides = get_configured_filter_overrides(self._root, safe_env)
        cmd = [
            "git",
            *GIT_HARDENED_GLOBAL_FLAGS,
            *filter_overrides,
            "status",
            "--porcelain=v1",
            "-b",
        ]
        try:
            returncode, stdout_str, stderr_str, _ = run_git_command_bounded(
                cmd,
                cwd=self._root,
                max_stdout_bytes=65536,
                timeout_seconds=10.0,
                env=safe_env,
            )
        except Exception as exc:  # noqa: BLE001
            return WorkspaceGitStatusOutput(
                is_git_repo=True,
                is_clean=False,
                summary=f"Failed to execute git status: {exc}",
            )

        if returncode != 0:
            return WorkspaceGitStatusOutput(
                is_git_repo=True,
                is_clean=False,
                summary=f"Git status returned error code {returncode}: {stderr_str.strip()}",
            )

        lines = stdout_str.splitlines()
        branch = None
        staged: list[str] = []
        unstaged: list[str] = []
        untracked: list[str] = []

        for line in lines:
            if line.startswith("## "):
                branch = line[3:].strip()
            elif line.startswith("?? "):
                untracked.append(line[3:].strip())
            elif len(line) >= 2:
                index_status = line[0]
                worktree_status = line[1]
                path_str = line[3:].strip()
                if index_status not in (" ", "?"):
                    staged.append(f"{index_status}: {path_str}")
                if worktree_status not in (" ", "?"):
                    unstaged.append(f"{worktree_status}: {path_str}")

        is_clean = not (staged or unstaged or untracked)
        summary = (
            "Working tree clean."
            if is_clean
            else f"{len(staged)} staged, {len(unstaged)} unstaged, {len(untracked)} untracked."
        )

        return WorkspaceGitStatusOutput(
            is_git_repo=True,
            branch=branch,
            is_clean=is_clean,
            staged_changes=staged,
            unstaged_changes=unstaged,
            untracked_files=untracked,
            summary=summary,
        )

    # -----------------------------------------------------------------------
    # 6. workspace_git_diff
    # -----------------------------------------------------------------------
    def git_diff(self, args: WorkspaceGitDiffInput) -> WorkspaceGitDiffOutput:
        """Inspect read-only Git diffs with bounded output and untracked file handling."""
        git_dir = self._root / ".git"
        if not git_dir.exists():
            return WorkspaceGitDiffOutput(
                diff_text="",
                truncated=False,
                untracked_files=[],
            )

        # Reject pathspec magic prefix ':' to prevent pathspec escape
        if args.path and args.path.strip().startswith(":"):
            raise WorkspaceSecurityError(
                f"Git pathspec magic prefix ':' is prohibited: {args.path!r}"
            )

        safe_env = get_safe_git_env(self._root)
        filter_overrides = get_configured_filter_overrides(self._root, safe_env)

        cmd = [
            "git",
            *GIT_HARDENED_GLOBAL_FLAGS,
            *filter_overrides,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
        ]
        if args.staged:
            cmd.append("--cached")

        if args.path:
            # Validate path relative to workspace
            safe_path = self._resolver.resolve_path(args.path)
            rel_path = str(safe_path.relative_to(self._root)) if safe_path != self._root else ""
            if rel_path:
                cmd.extend(["--", rel_path])

        try:
            returncode, stdout_str, stderr_str, truncated = run_git_command_bounded(
                cmd,
                cwd=self._root,
                max_stdout_bytes=args.max_bytes,
                timeout_seconds=10.0,
                env=safe_env,
            )
        except Exception as exc:  # noqa: BLE001
            return WorkspaceGitDiffOutput(
                diff_text=f"Error executing git diff: {exc}",
                truncated=False,
            )

        if returncode != 0 and stderr_str:
            diff_text = f"Git diff error ({returncode}): {stderr_str.strip()}\n{stdout_str}"
        else:
            diff_text = stdout_str

        # Collect untracked files if unstaged diff was requested
        untracked: list[str] = []
        if not args.staged:
            try:
                status_cmd = [
                    "git",
                    *GIT_HARDENED_GLOBAL_FLAGS,
                    *filter_overrides,
                    "status",
                    "--porcelain=v1",
                ]
                st_code, st_out, _, _ = run_git_command_bounded(
                    status_cmd,
                    cwd=self._root,
                    max_stdout_bytes=65536,
                    timeout_seconds=5.0,
                    env=safe_env,
                )
                if st_code == 0:
                    for line in st_out.splitlines():
                        if line.startswith("?? "):
                            untracked.append(line[3:].strip())
            except Exception:  # noqa: BLE001, S110
                pass

        return WorkspaceGitDiffOutput(
            diff_text=diff_text,
            truncated=truncated,
            untracked_files=untracked,
        )


class WorkspaceToolsExecutor(ToolExecutorPort):
    """Executor port implementation mapping tool invocations to WorkspaceToolsService."""

    def __init__(self, service: WorkspaceToolsService) -> None:
        self._service = service

    def execute(self, tool: ToolDefinition, invocation: ToolInvocation) -> ToolResult:
        """Execute a workspace tool and return an immutable ToolResult."""
        started_at = utc_now()
        t0 = time.monotonic()
        tool_id = tool.tool_id

        try:
            output_obj: Any
            if tool_id == "workspace_get_working_directory":
                inp = WorkspaceGetWorkingDirectoryInput.model_validate(invocation.arguments)
                output_obj = self._service.get_working_directory(inp)
            elif tool_id == "workspace_list_directory":
                inp_list = WorkspaceListDirectoryInput.model_validate(invocation.arguments)
                output_obj = self._service.list_directory(inp_list)
            elif tool_id == "workspace_read_file":
                inp_read = WorkspaceReadFileInput.model_validate(invocation.arguments)
                output_obj = self._service.read_file(inp_read)
            elif tool_id == "workspace_search_files":
                inp_search = WorkspaceSearchFilesInput.model_validate(invocation.arguments)
                output_obj = self._service.search_files(inp_search)
            elif tool_id == "workspace_git_status":
                inp_status = WorkspaceGitStatusInput.model_validate(invocation.arguments)
                output_obj = self._service.git_status(inp_status)
            elif tool_id == "workspace_git_diff":
                inp_diff = WorkspaceGitDiffInput.model_validate(invocation.arguments)
                output_obj = self._service.git_diff(inp_diff)
            else:
                raise ValueError(f"Unknown workspace tool ID: '{tool_id}'")

            duration_ms = (time.monotonic() - t0) * 1000.0
            completed_at = utc_now()

            return ToolResult(
                execution_id=invocation.execution_id,
                tool_id=tool_id,
                status=ExecutionStatus.COMPLETED,
                output=output_obj.model_dump(),
                duration_ms=duration_ms,
                started_at=started_at,
                completed_at=completed_at,
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.monotonic() - t0) * 1000.0
            completed_at = utc_now()
            logger.warning("Workspace tool '%s' execution failed: %s", tool_id, exc)
            return ToolResult(
                execution_id=invocation.execution_id,
                tool_id=tool_id,
                status=ExecutionStatus.FAILED,
                error_message=str(exc),
                duration_ms=duration_ms,
                started_at=started_at,
                completed_at=completed_at,
            )


def create_workspace_tool_definitions() -> list[ToolDefinition]:
    """Construct canonical ToolDefinition objects for all 6 workspace inspection tools."""
    return [
        ToolDefinition(
            tool_id="workspace_get_working_directory",
            name="Get Working Directory",
            description="Retrieve the configured local workspace root path.",
            version="1.0.0",
            permission_level=PermissionLevel.READ_ONLY,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=False,
            timeout_seconds=5.0,
            input_model=WorkspaceGetWorkingDirectoryInput,
            output_model=WorkspaceGetWorkingDirectoryOutput,
        ),
        ToolDefinition(
            tool_id="workspace_list_directory",
            name="List Directory",
            description="List files and directories in a workspace folder.",
            version="1.0.0",
            permission_level=PermissionLevel.READ_ONLY,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=False,
            timeout_seconds=10.0,
            input_model=WorkspaceListDirectoryInput,
            output_model=WorkspaceListDirectoryOutput,
        ),
        ToolDefinition(
            tool_id="workspace_read_file",
            name="Read File",
            description="Read UTF-8 text content from a file within the workspace (up to 128 KB).",
            version="1.0.0",
            permission_level=PermissionLevel.READ_ONLY,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=False,
            timeout_seconds=10.0,
            input_model=WorkspaceReadFileInput,
            output_model=WorkspaceReadFileOutput,
        ),
        ToolDefinition(
            tool_id="workspace_search_files",
            name="Search Files",
            description="Perform a literal text search across files in the workspace.",
            version="1.0.0",
            permission_level=PermissionLevel.READ_ONLY,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=False,
            timeout_seconds=20.0,
            input_model=WorkspaceSearchFilesInput,
            output_model=WorkspaceSearchFilesOutput,
        ),
        ToolDefinition(
            tool_id="workspace_git_status",
            name="Git Status",
            description="Check Git branch and working-tree clean/dirty status in the workspace.",
            version="1.0.0",
            permission_level=PermissionLevel.READ_ONLY,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=False,
            timeout_seconds=15.0,
            input_model=WorkspaceGitStatusInput,
            output_model=WorkspaceGitStatusOutput,
        ),
        ToolDefinition(
            tool_id="workspace_git_diff",
            name="Git Diff",
            description="Inspect read-only working tree or staged Git diffs in the workspace.",
            version="1.0.0",
            permission_level=PermissionLevel.READ_ONLY,
            execution_type=ExecutionType.IN_PROCESS,
            is_mutating=False,
            timeout_seconds=15.0,
            input_model=WorkspaceGitDiffInput,
            output_model=WorkspaceGitDiffOutput,
        ),
    ]


def register_workspace_tools(registry: ToolRegistryPort) -> None:
    """Register all 6 workspace inspection tools into the provided tool registry."""
    for definition in create_workspace_tool_definitions():
        registry.register(definition)


def build_workspace_tool_system(
    workspace_root: Path | None = None,
) -> tuple[ToolRegistryPort, ToolRouter, WorkspaceToolsService]:
    """Factory creating and configuring the complete workspace tool subsystem.

    1. Resolves workspace root.
    2. Initializes and registers all 6 workspace tools into ToolRegistry.
    3. Seals the registry.
    4. Constructs WorkspacePathResolver and WorkspaceToolsService.
    5. Configures PermissionEngine with workspace confinement.
    6. Returns sealed (registry, router, service).
    """
    from orion.application.tools.permissions import PermissionEngine, PermissionPolicy
    from orion.application.tools.registry import ToolRegistry
    from orion.application.tools.router import ToolRouter
    from orion.infrastructure.tools.path_resolver import get_configured_workspace_root

    root = workspace_root if workspace_root is not None else get_configured_workspace_root()
    resolver = WorkspacePathResolver(root)
    service = WorkspaceToolsService(resolver)
    executor = WorkspaceToolsExecutor(service)

    registry = ToolRegistry()
    register_workspace_tools(registry)
    registry.seal()

    policy = PermissionPolicy(
        workspace_root=root,
        allow_read_only=True,
        allow_system_execute=False,
    )
    permission_engine = PermissionEngine(policy)
    router = ToolRouter(
        registry=registry,
        permission_engine=permission_engine,
        executor=executor,
    )

    return registry, router, service
