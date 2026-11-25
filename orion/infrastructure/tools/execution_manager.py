"""Controlled subprocess execution manager for ORION.

Confines process execution to allowlisted binaries, validated workspace directories,
sanitized environments, bounded concurrency, and strict wall-clock timeouts with
process-group termination.
"""

import codecs
import concurrent.futures
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel, ConfigDict, Field

from orion.application.tools.contracts import ExecutionStatus, utc_now
from orion.infrastructure.tools.path_resolver import (
    WorkspacePathResolver,
    WorkspaceSecurityError,
    get_configured_workspace_root,
)
from orion.infrastructure.tools.workspace_tools import (
    GIT_HARDENED_GLOBAL_FLAGS,
    get_configured_filter_overrides,
    get_safe_git_env,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants and Policy Bounds
# ---------------------------------------------------------------------------

DEFAULT_ALLOWLISTED_COMMANDS: frozenset[str] = frozenset({"pytest", "ruff", "git"})

ALLOWED_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "diff",
        "log",
        "rev-parse",
        "branch",
        "show",
        "describe",
        "tag",
        "version",
        "--version",
    }
)

DISALLOWED_INTERPRETERS: frozenset[str] = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "csh",
        "tcsh",
        "dash",
        "fish",
        "ksh",
        "python",
        "python3",
        "perl",
        "ruby",
        "node",
        "php",
    }
)

FORBIDDEN_ELEVATION_COMMANDS: frozenset[str] = frozenset({"sudo", "su", "doas", "pkexec", "chroot"})

APPROVED_SYSTEM_DIRS: tuple[Path, ...] = (
    Path("/usr/bin"),
    Path("/bin"),
    Path("/usr/local/bin"),
)

FORBIDDEN_RUFF_FLAGS: frozenset[str] = frozenset(
    {"--fix", "--fix-only", "--add-noqa", "--unsafe-fixes"}
)

FORBIDDEN_RUFF_SUBCOMMANDS: frozenset[str] = frozenset({"clean", "server"})

FORBIDDEN_BRANCH_FLAGS: frozenset[str] = frozenset(
    {
        "-d",
        "-D",
        "--delete",
        "-m",
        "-M",
        "--move",
        "-c",
        "-C",
        "--copy",
        "-u",
        "--set-upstream-to",
        "--unset-upstream",
        "--edit-description",
        "-f",
        "--force",
    }
)

FORBIDDEN_TAG_FLAGS: frozenset[str] = frozenset(
    {
        "-v",
        "--verify",
        "-d",
        "--delete",
        "-f",
        "--force",
        "-a",
        "--annotate",
        "-s",
        "--sign",
        "-u",
        "--local-user",
        "-m",
        "--message",
        "-F",
        "--file",
    }
)

MAX_ARG_COUNT: int = 64
MAX_ARG_LENGTH: int = 4096
MAX_OUTPUT_BYTES: int = 256 * 1024  # 256 KB retained output
DEFAULT_TIMEOUT_SECONDS: float = 30.0
MAX_TIMEOUT_SECONDS: float = 120.0

SAFE_ENV_KEYS: frozenset[str] = frozenset(
    {"HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR"}
)

SENSITIVE_ENV_SUBSTRINGS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "AUTH",
    "PASS",
    "CREDENTIAL",
    "SSH_",
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GCLOUD_",
    "OLLAMA_",
    "GIT_",
    "OPENAI_",
    "ANTHROPIC_",
    "GEMINI_",
)

CHAINING_CHARACTERS: tuple[str, ...] = (";", "&&", "||", "|", "`", "$(")

OutputChunkListener = Callable[[str, str, str], None]
LifecycleListener = Callable[[str, str, dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ExecutionError(Exception):
    """Base exception for subprocess execution errors."""


class DisallowedCommandError(ExecutionError):
    """Raised when an executable is not allowlisted or prohibited."""


class InvalidArgumentsError(ExecutionError):
    """Raised when command arguments violate length, count, or character rules."""


class ExecutableNotFoundError(ExecutionError):
    """Raised when an allowlisted executable cannot be resolved to an existing binary."""


class ExecutionTimeoutError(ExecutionError):
    """Raised when execution exceeds the configured wall-clock timeout."""


class ExecutionConcurrencyError(ExecutionError):
    """Raised when execution is rejected due to active subprocess concurrency limit."""


# ---------------------------------------------------------------------------
# Typed Contracts
# ---------------------------------------------------------------------------


class ExecutionRequest(BaseModel):
    """Typed parameters for an authorized subprocess execution."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    execution_id: str = Field(default_factory=lambda: f"sub_{uuid.uuid4().hex[:12]}")
    command: str = Field(..., min_length=1)
    args: list[str] = Field(default_factory=list)
    cwd: Path
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0.0, le=MAX_TIMEOUT_SECONDS)
    conversation_id: str = ""
    client_request_id: str = ""
    output_listener: Any = None


class ExecutionOutput(BaseModel):
    """Immutable result contract produced after subprocess completion or termination."""

    model_config = ConfigDict(frozen=True)

    execution_id: str
    command: str
    exit_code: int | None
    status: ExecutionStatus
    stdout: str
    stderr: str
    truncated: bool
    duration_ms: float
    started_at: datetime
    completed_at: datetime
    error_message: str | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0


# ---------------------------------------------------------------------------
# Environment Sanitization
# ---------------------------------------------------------------------------


def build_sanitized_environment(
    workspace_root: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a tightly controlled minimal environment, stripping secrets and inherited state."""
    env: dict[str, str] = {}

    # 1. Validated PATH: virtualenv bin first, then system binaries
    path_entries: list[str] = []
    venv_bin = Path(sys.prefix) / "bin"
    if venv_bin.is_dir():
        path_entries.append(str(venv_bin))
    for std_path in ("/usr/local/bin", "/usr/bin", "/bin"):
        if Path(std_path).is_dir() and std_path not in path_entries:
            path_entries.append(std_path)
    env["PATH"] = ":".join(path_entries)

    # 2. Whitelisted safe environment keys
    for key in SAFE_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    env.setdefault("HOME", "/tmp")
    env.setdefault("USER", "nobody")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")

    # 3. Predictable non-interactive runtime flags
    env["TERM"] = "dumb"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    if workspace_root is not None:
        env["PYTHONPATH"] = str(workspace_root)

    # 4. Optional extra environment with strict secret filtering
    if extra_env:
        for k, v in extra_env.items():
            upper = k.upper()
            if not any(pattern in upper for pattern in SENSITIVE_ENV_SUBSTRINGS):
                env[k] = v

    return env


# ---------------------------------------------------------------------------
# Command and Argument Validation
# ---------------------------------------------------------------------------


def resolve_executable(
    command_name: str,
    allowed_commands: frozenset[str] = DEFAULT_ALLOWLISTED_COMMANDS,
) -> Path:
    """Resolve an allowlisted command name to a trusted absolute binary path.

    Raises:
        DisallowedCommandError: If command is not in the allowlist, contains path separators,
            or resolves outside approved directories.
        ExecutableNotFoundError: If binary cannot be found on trusted search path.
    """
    cmd = command_name.strip()
    if not cmd:
        raise DisallowedCommandError("Command cannot be empty.")

    # Never accept path separators: prevent directory traversal and absolute path injection
    if "/" in cmd or "\\" in cmd:
        raise DisallowedCommandError(
            f"Executable path injection blocked: '{command_name}'. Only simple command names are permitted."
        )

    if cmd in FORBIDDEN_ELEVATION_COMMANDS:
        raise DisallowedCommandError(f"Privilege escalation command '{cmd}' is strictly forbidden.")

    if cmd in DISALLOWED_INTERPRETERS:
        raise DisallowedCommandError(
            f"Shell interpreter '{cmd}' is forbidden; direct script invocation is not permitted."
        )

    if cmd not in allowed_commands:
        raise DisallowedCommandError(
            f"Command '{cmd}' is not in the executable allowlist: {sorted(allowed_commands)}."
        )

    approved_parents: set[Path] = {d.resolve() for d in APPROVED_SYSTEM_DIRS if d.is_dir()}
    venv_bin = (Path(sys.prefix) / "bin").resolve()
    if venv_bin.is_dir():
        approved_parents.add(venv_bin)

    allowed_prefixes: tuple[Path, ...] = (
        Path("/usr"),
        Path("/bin"),
        Path("/usr/local"),
        Path(sys.prefix).resolve(),
    )

    # 1. Check active virtualenv first (for pytest, ruff)
    venv_candidate = Path(sys.prefix) / "bin" / cmd
    if venv_candidate.is_file() and os.access(venv_candidate, os.X_OK):
        resolved = venv_candidate.resolve()
        if (
            resolved.is_file()
            and os.access(resolved, os.X_OK)
            and any(resolved == p or p in resolved.parents for p in allowed_prefixes)
        ):
            return resolved

    # 2. Trusted system search ONLY — never inherit uncontrolled PATH
    trusted_search_dirs = [
        d for d in [Path(sys.prefix) / "bin", *APPROVED_SYSTEM_DIRS] if d.is_dir()
    ]
    trusted_path_str = ":".join(str(d) for d in trusted_search_dirs)

    which_path = shutil.which(cmd, path=trusted_path_str)
    if which_path is not None:
        candidate_path = Path(which_path)
        # Verify the candidate executable was discovered directly in an approved directory
        if (
            candidate_path.parent in APPROVED_SYSTEM_DIRS
            or candidate_path.parent.resolve() in approved_parents
        ):
            resolved = candidate_path.resolve()
            if (
                resolved.is_file()
                and os.access(resolved, os.X_OK)
                and any(resolved == p or p in resolved.parents for p in allowed_prefixes)
            ):
                return resolved
            raise DisallowedCommandError(
                f"Resolved binary '{resolved}' is in untrusted directory '{resolved.parent}'."
            )

    raise ExecutableNotFoundError(
        f"Allowlisted command '{cmd}' was not found as an executable file in trusted system directories."
    )


def validate_git_path_confinement(
    args: list[str],
    workspace_root: Path | None,
) -> None:
    """Validate that Git path operands do not reference or traverse files outside the workspace.

    Checks:
    - Rejects --no-index.
    - Rejects path traversal (..) in any argument.
    - Rejects absolute paths escaping workspace_root.
    - Rejects symlink operands pointing outside workspace_root.
    - Strictly confines all pathspecs following option terminator (--).
    """
    if workspace_root is None:
        return

    root_resolved = workspace_root.resolve()
    resolver = WorkspacePathResolver(workspace_root)
    after_double_dash = False

    for arg in args:
        if arg == "--no-index" or arg.startswith("--no-index="):
            raise InvalidArgumentsError("Git '--no-index' option is forbidden.")

        if arg == "--":
            after_double_dash = True
            continue

        # Reject path traversal
        if ".." in arg.split("/") or ".." in arg.split("\\"):
            raise WorkspaceSecurityError(
                f"Path traversal '..' in Git argument '{arg}' is forbidden."
            )

        # Absolute path checks
        if arg.startswith(("/", "\\")):
            try:
                p = Path(arg).resolve()
            except Exception as exc:
                raise WorkspaceSecurityError(
                    f"Invalid path in Git argument '{arg}': {exc}"
                ) from exc
            if p != root_resolved and root_resolved not in p.parents:
                raise WorkspaceSecurityError(
                    f"Git path argument '{arg}' escapes authorized workspace root '{workspace_root}'."
                )

        # If argument follows '--', it is definitively a path operand
        if after_double_dash:
            try:
                resolved = resolver.resolve_path(arg, must_exist=False)
                if resolved != root_resolved and root_resolved not in resolved.parents:
                    raise WorkspaceSecurityError(
                        f"Git path operand '{arg}' resolves outside authorized workspace root '{workspace_root}'."
                    )
            except Exception as exc:
                raise WorkspaceSecurityError(
                    f"Git path operand '{arg}' is outside authorized workspace root '{workspace_root}': {exc}"
                ) from exc

            # Verify symlink bounds if target exists
            target_path = workspace_root / arg if not Path(arg).is_absolute() else Path(arg)
            if target_path.is_symlink():
                resolved_symlink = target_path.resolve()
                if (
                    resolved_symlink != root_resolved
                    and root_resolved not in resolved_symlink.parents
                ):
                    raise WorkspaceSecurityError(
                        f"Git path operand '{arg}' is a symlink escaping authorized workspace root '{workspace_root}'."
                    )
        else:
            # Arguments before '--' that exist as symlinks escaping workspace are blocked
            if not arg.startswith("-"):
                target_path = workspace_root / arg if not Path(arg).is_absolute() else Path(arg)
                if target_path.is_symlink():
                    resolved_symlink = target_path.resolve()
                    if (
                        resolved_symlink != root_resolved
                        and root_resolved not in resolved_symlink.parents
                    ):
                        raise WorkspaceSecurityError(
                            f"Git path argument '{arg}' is a symlink escaping authorized workspace root '{workspace_root}'."
                        )


def validate_command_arguments(
    command_name: str,
    args: list[str],
    workspace_root: Path | None = None,
) -> None:
    """Validate argument count, lengths, and forbid dangerous metacharacters and chaining."""
    if not isinstance(args, list):
        raise InvalidArgumentsError("Arguments must be a list of strings.")

    if len(args) > MAX_ARG_COUNT:
        raise InvalidArgumentsError(
            f"Argument count ({len(args)}) exceeds maximum limit of {MAX_ARG_COUNT}."
        )

    for idx, arg in enumerate(args):
        if not isinstance(arg, str):
            raise InvalidArgumentsError(f"Argument at index {idx} is not a string.")

        if "\x00" in arg:
            raise InvalidArgumentsError(f"Argument at index {idx} contains forbidden NUL bytes.")

        if len(arg) > MAX_ARG_LENGTH:
            raise InvalidArgumentsError(
                f"Argument at index {idx} exceeds maximum length of {MAX_ARG_LENGTH} characters."
            )

        # Prohibit command chaining or shell interpreter embedding in arguments
        for token in CHAINING_CHARACTERS:
            if token in arg:
                raise InvalidArgumentsError(
                    f"Argument at index {idx} contains forbidden shell chaining token '{token}'."
                )

        if arg in FORBIDDEN_ELEVATION_COMMANDS:
            raise DisallowedCommandError(
                f"Argument at index {idx} contains forbidden command '{arg}'."
            )

    # Git-specific argument hardening: allow only read-only subcommands and forbid config/override/output/external flags
    if command_name == "git":
        # Find first non-option token (the git subcommand)
        subcommand = None
        for arg in args:
            if not arg.startswith("-"):
                subcommand = arg
                break
            if arg in ("--version", "-v"):
                subcommand = "version"
                break

        if subcommand is None:
            subcommand = "status"

        if subcommand not in ALLOWED_GIT_SUBCOMMANDS:
            raise DisallowedCommandError(
                f"Git subcommand '{subcommand}' is not in the read-only inspection allowlist: {sorted(ALLOWED_GIT_SUBCOMMANDS)}."
            )

        for idx, arg in enumerate(args):
            # Prohibit --no-index
            if arg == "--no-index" or arg.startswith("--no-index="):
                raise InvalidArgumentsError("Git '--no-index' option is forbidden.")
            # Prohibit GPG signature verification options
            if arg == "--show-signature" or arg.startswith("--show-signature="):
                raise InvalidArgumentsError(
                    f"Git signature verification flag '{arg}' is forbidden."
                )
            # Directory override: -C, --work-tree, --git-dir, --exec-path
            if arg == "-C" or arg.startswith(("-C=", "-C")):
                raise InvalidArgumentsError(f"Git directory override flag '{arg}' is forbidden.")
            # Configuration injection
            if arg == "-c" or arg.startswith(("-c", "--config", "--config-env")):
                raise InvalidArgumentsError(
                    f"Git configuration injection flag '{arg}' is forbidden."
                )
            # Exec path & directory overrides
            if arg in ("--exec-path", "--git-dir", "--work-tree") or arg.startswith(
                ("--exec-path=", "--git-dir=", "--work-tree=")
            ):
                raise InvalidArgumentsError(
                    f"Git path/directory override flag '{arg}' is forbidden."
                )
            # Output file flags
            if arg in ("--output", "-o") or arg.startswith(("--output=", "-o=")):
                raise InvalidArgumentsError(f"Git output redirection flag '{arg}' is forbidden.")
            # External diff, pagers, textconv flags
            if arg in ("--ext-diff", "--textconv", "--paginate", "-p", "-P", "--no-pager"):
                raise InvalidArgumentsError(f"Git external program flag '{arg}' is forbidden.")
            if arg.startswith(("--ext-diff", "--textconv")):
                raise InvalidArgumentsError(f"Git external program flag '{arg}' is forbidden.")
            # Server hooks
            if arg in ("--upload-pack", "--receive-pack") or arg.startswith(
                ("--upload-pack=", "--receive-pack=")
            ):
                raise InvalidArgumentsError(f"Git hook/server flag '{arg}' is forbidden.")

        # Enforce read-only profile for branch: reject mutation flags and creation
        if subcommand == "branch":
            for arg in args:
                if arg in FORBIDDEN_BRANCH_FLAGS or any(
                    arg.startswith(f"{f}=") for f in FORBIDDEN_BRANCH_FLAGS
                ):
                    raise InvalidArgumentsError(f"Git mutating branch flag '{arg}' is forbidden.")
            listing_options = {
                "-a",
                "-r",
                "--all",
                "--remotes",
                "-l",
                "--list",
                "--show-current",
                "--contains",
                "--no-contains",
                "--merged",
                "--no-merged",
                "-v",
                "-vv",
                "--verbose",
                "--sort",
            }
            positional = [a for a in args if not a.startswith("-") and a != "branch"]
            has_listing_opt = any(
                a in listing_options
                or any(
                    a.startswith(f"{opt}=")
                    for opt in ("--sort", "--contains", "--no-contains", "--merged", "--no-merged")
                )
                for a in args
            )
            if positional and not has_listing_opt:
                raise InvalidArgumentsError(
                    "Creating Git branches is forbidden; only branch listing/inspection is permitted."
                )

        # Enforce read-only profile for tag: reject mutation flags and creation
        elif subcommand == "tag":
            for arg in args:
                if arg in ("-v", "--verify") or arg.startswith(("-v=", "--verify=")):
                    raise InvalidArgumentsError(
                        f"Git tag signature verification flag '{arg}' is forbidden."
                    )
                if arg in FORBIDDEN_TAG_FLAGS or any(
                    arg.startswith(f"{f}=") for f in FORBIDDEN_TAG_FLAGS
                ):
                    raise InvalidArgumentsError(f"Git mutating tag flag '{arg}' is forbidden.")
            listing_options = {
                "-l",
                "--list",
                "-n",
                "--sort",
                "--contains",
                "--no-contains",
                "--merged",
                "--no-merged",
                "--points-at",
            }
            positional = [a for a in args if not a.startswith("-") and a != "tag"]
            has_listing_opt = any(
                a in listing_options
                or any(
                    a.startswith(f"{opt}=")
                    for opt in (
                        "--sort",
                        "--contains",
                        "--no-contains",
                        "--merged",
                        "--no-merged",
                        "--points-at",
                    )
                )
                for a in args
            )
            if positional and not has_listing_opt:
                raise InvalidArgumentsError(
                    "Creating Git tags is forbidden; only tag listing is permitted."
                )

        # Git path confinement: validate all path operands
        validate_git_path_confinement(args, workspace_root)

    # Ruff-specific argument hardening: forbid mutations, file writes, and mutating subcommands
    elif command_name == "ruff":
        for arg in args:
            if arg in FORBIDDEN_RUFF_FLAGS or any(
                arg.startswith(f"{f}=") for f in FORBIDDEN_RUFF_FLAGS
            ):
                raise InvalidArgumentsError(f"Ruff file mutation flag '{arg}' is forbidden.")
            if arg in ("--output-file", "-o") or arg.startswith(("--output-file=", "-o=")):
                raise InvalidArgumentsError(f"Ruff output file flag '{arg}' is forbidden.")
            if arg in FORBIDDEN_RUFF_SUBCOMMANDS:
                raise DisallowedCommandError(f"Ruff subcommand '{arg}' is forbidden.")

        subcommand = None
        for arg in args:
            if not arg.startswith("-"):
                subcommand = arg
                break
        if subcommand == "format" and not any(a in ("--check", "--diff") for a in args):
            raise InvalidArgumentsError(
                "ruff format requires '--check' or '--diff' to prevent modifying files."
            )

    # Pytest-specific argument hardening: forbid configuration override (-o, --override-ini) and custom plugins
    elif command_name == "pytest":
        for idx, arg in enumerate(args):
            if arg in ("-o", "--override-ini") or arg.startswith(("-o=", "--override-ini=")):
                raise InvalidArgumentsError(f"Pytest configuration override '{arg}' is forbidden.")
            if arg == "-p":
                if idx + 1 >= len(args) or not args[idx + 1].startswith("no:"):
                    raise InvalidArgumentsError(
                        "Custom pytest plugins via '-p' are forbidden; only '-p no:<plugin>' is allowed."
                    )
            elif arg.startswith("-p") and not arg.startswith("-pno:"):
                raise InvalidArgumentsError(
                    f"Custom pytest plugins via '{arg}' are forbidden; only '-p no:<plugin>' is allowed."
                )

        # Pytest target path confinement: non-flag arguments are test targets and must not escape workspace
        if workspace_root is not None:
            root_res = workspace_root.resolve()
            resolver = WorkspacePathResolver(workspace_root)
            for arg in args:
                if not arg.startswith("-"):
                    p = Path(arg)
                    if p.is_absolute():
                        resolved_p = p.resolve()
                        if resolved_p != root_res and root_res not in resolved_p.parents:
                            raise WorkspaceSecurityError(
                                f"Pytest target path '{arg}' escapes authorized workspace root '{workspace_root}'."
                            )
                        if not resolved_p.exists():
                            raise WorkspaceSecurityError(
                                f"Pytest target path '{arg}' does not exist."
                            )
                    else:
                        try:
                            resolver.resolve_path(arg, must_exist=True)
                        except Exception as exc:
                            raise WorkspaceSecurityError(
                                f"Pytest target path '{arg}' is outside authorized workspace root '{workspace_root}': {exc}"
                            ) from exc

    # Printf-specific argument hardening: forbid option flags
    elif command_name == "printf":
        for arg in args:
            if arg.startswith("-"):
                raise InvalidArgumentsError(f"printf does not accept option flags: '{arg}'.")


# ---------------------------------------------------------------------------
# Output Batching & Stream Drain Helper
# ---------------------------------------------------------------------------


class OutputBatcher:
    """Coalesces streaming stdout and stderr chunks to avoid flooding UI event loops.

    Buffers chunks per stream and emits them:
    - When accumulated characters exceed max_batch_chars (default 1024), OR
    - Every flush_interval_seconds (default 50ms = 0.05s) via background timer, AND
    - During final close() to guarantee zero dropped output.
    """

    def __init__(
        self,
        execution_id: str,
        emit_fn: Callable[[str, str, str], None],
        flush_interval_seconds: float = 0.05,
        max_batch_chars: int = 1024,
        max_pending_chars: int = MAX_OUTPUT_BYTES,
    ) -> None:
        self._execution_id = execution_id
        self._emit_fn = emit_fn
        self._flush_interval = flush_interval_seconds
        self._max_batch_chars = max_batch_chars
        self._max_pending_chars = max_pending_chars

        self._lock = threading.Lock()
        self._buffers: dict[str, list[str]] = {"stdout": [], "stderr": []}
        self._buffer_lens: dict[str, int] = {"stdout": 0, "stderr": 0}
        self._stopped = threading.Event()
        self._flusher_thread = threading.Thread(
            target=self._flusher_loop,
            name=f"output-batcher-{execution_id[:8]}",
            daemon=True,
        )
        self._flusher_thread.start()

    def push(self, stream: str, text: str) -> None:
        """Push decoded text for a stream ('stdout' or 'stderr') within bounded pending budget."""
        if not text:
            return
        to_emit: list[tuple[str, str]] = []
        with self._lock:
            total_pending = self._buffer_lens["stdout"] + self._buffer_lens["stderr"]
            remaining_pending = self._max_pending_chars - total_pending
            if remaining_pending <= 0:
                return
            if len(text) > remaining_pending:
                text = text[:remaining_pending]
            self._buffers[stream].append(text)
            self._buffer_lens[stream] += len(text)
            if self._buffer_lens[stream] >= self._max_batch_chars:
                flushed = "".join(self._buffers[stream])
                self._buffers[stream].clear()
                self._buffer_lens[stream] = 0
                to_emit.append((stream, flushed))

        for st, chunk in to_emit:
            try:
                self._emit_fn(self._execution_id, st, chunk)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error emitting batched output chunk: %s", exc)

    def _flush_locked(self) -> list[tuple[str, str]]:
        to_emit: list[tuple[str, str]] = []
        for stream in ("stdout", "stderr"):
            if self._buffers[stream]:
                flushed = "".join(self._buffers[stream])
                self._buffers[stream].clear()
                self._buffer_lens[stream] = 0
                to_emit.append((stream, flushed))
        return to_emit

    def flush(self) -> None:
        """Flush any pending buffered output immediately."""
        with self._lock:
            to_emit = self._flush_locked()
        for stream, chunk in to_emit:
            try:
                self._emit_fn(self._execution_id, stream, chunk)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error emitting batched output chunk: %s", exc)

    def _flusher_loop(self) -> None:
        while not self._stopped.is_set():
            self._stopped.wait(timeout=self._flush_interval)
            self.flush()

    def close(self) -> None:
        """Stop background flusher thread and perform final flush."""
        self._stopped.set()
        if self._flusher_thread.is_alive():
            self._flusher_thread.join(timeout=0.2)
        self.flush()


def _drain_stream(
    stream: IO[bytes],
    buffer: bytearray,
    max_bytes: int,
    overflow_flag: list[bool],
    lock: threading.Lock,
    stream_name: str = "stdout",
    batcher: OutputBatcher | None = None,
    total_bytes_counter: list[int] | None = None,
) -> None:
    """Drain stream concurrently until EOF, retaining up to max_bytes without pipe deadlocks."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        read_fn = stream.read1 if hasattr(stream, "read1") else stream.read
        while True:
            chunk = read_fn(4096)
            if not chunk:
                if batcher is not None:
                    final_text = decoder.decode(b"", final=True)
                    if final_text:
                        batcher.push(stream_name, final_text)
                break

            text_to_emit = ""
            with lock:
                if total_bytes_counter is not None:
                    total_bytes_counter[0] += len(chunk)
                remaining = max_bytes - len(buffer)
                if remaining > 0:
                    to_take = min(len(chunk), remaining)
                    buffer.extend(chunk[:to_take])
                    if len(chunk) > to_take:
                        overflow_flag[0] = True
                    text_to_emit = decoder.decode(chunk[:to_take], final=False)
                else:
                    overflow_flag[0] = True

            if batcher is not None and text_to_emit:
                batcher.push(stream_name, text_to_emit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Error draining stream %s: %s", stream_name, exc)
    finally:
        try:
            stream.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error closing stream %s: %s", stream_name, exc)


def open_directory_descriptor(workspace_root: Path, target_dir: Path) -> int:
    """Safely open target_dir component-by-component relative to workspace_root with O_NOFOLLOW.

    Guarantees:
    - Root directory descriptor is opened with O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC.
    - Every intermediate path component is traversed descriptor-relative via openat (os.open with dir_fd).
    - If any intermediate or leaf component is a symlink, O_NOFOLLOW causes immediate failure (ELOOP).
    - Prevents directory traversal out of workspace root via intermediate symlinks.
    - Returns an open file descriptor to target_dir. Caller must close the returned descriptor.
    """
    raw_root = Path(str(workspace_root))
    if raw_root.is_symlink():
        raise WorkspaceSecurityError(f"Workspace root '{workspace_root}' is a symlink.")

    root_resolved = raw_root.resolve()

    try:
        # Check relative path without following intermediate symlinks
        if target_dir.is_absolute():
            rel = target_dir.relative_to(raw_root)
        else:
            rel = target_dir
    except ValueError as exc:
        raise WorkspaceSecurityError(
            f"Target directory '{target_dir}' escapes workspace root '{workspace_root}'."
        ) from exc

    if ".." in rel.parts:
        raise WorkspaceSecurityError("Parent directory traversal '..' is forbidden.")

    open_flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        open_flags |= os.O_CLOEXEC

    try:
        current_fd = os.open(str(root_resolved), open_flags)
    except OSError as exc:
        raise WorkspaceSecurityError(
            f"Workspace root directory '{root_resolved}' cannot be securely opened: {exc}"
        ) from exc

    try:
        for part in rel.parts:
            if not part or part == ".":
                continue
            child_fd = os.open(part, open_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except OSError as exc:
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise WorkspaceSecurityError(
            f"Working directory '{target_dir}' or one of its components is a symlink or cannot be securely opened: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# ExecutionManager
# ---------------------------------------------------------------------------


class ExecutionManager:
    """Orchestrates controlled local subprocess execution within strict security bounds.

    Features:
    - Executable allowlist resolution.
    - Workspace confinement and symlink rejection.
    - Environment sanitization.
    - Dedicated single-worker pool ensuring no saturation of main assistant pool.
    - Concurrency limit (1 active subprocess).
    - Concurrent stdout/stderr draining with live incremental output delivery.
    - Bounded memory buffer (256 KB) with overflow tracking.
    - Wall-clock timeouts with process-group cleanup (SIGTERM -> SIGKILL).
    - Scoped cancellation by execution_id.
    """

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        allowed_commands: frozenset[str] = DEFAULT_ALLOWLISTED_COMMANDS,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
        default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_timeout: float = MAX_TIMEOUT_SECONDS,
        path_resolver: WorkspacePathResolver | None = None,
        event_recorder: Callable[[str, str, str | None, dict[str, Any]], None] | None = None,
        output_listener: "OutputChunkListener | None" = None,
        lifecycle_listener: "LifecycleListener | None" = None,
    ) -> None:
        if path_resolver is not None:
            self._path_resolver = path_resolver
        else:
            root = (
                Path(workspace_root).resolve()
                if workspace_root is not None
                else get_configured_workspace_root()
            )
            self._path_resolver = WorkspacePathResolver(root)

        self._allowed_commands = allowed_commands
        self._max_output_bytes = max_output_bytes
        self._default_timeout = default_timeout
        self._max_timeout = max_timeout
        self._event_recorder = event_recorder
        self._output_listener: OutputChunkListener | None = output_listener
        self._lifecycle_listener: LifecycleListener | None = lifecycle_listener

        # Concurrency guard: strictly 1 active subprocess
        self._concurrency_lock = threading.Lock()
        self._is_executing = False

        # Active execution tracking for scoped cancellation (O(1) bounded to single active execution)
        self._active_execution_id: str | None = None
        self._active_proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._active_pgid: int | None = None
        self._active_cancellation_requested: bool = False
        self._active_terminated: bool = False

        # Isolated thread pool for subprocess management
        self._executor_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="orion-subprocess-worker",
        )
        self._is_shut_down = False

    def set_output_listener(self, listener: "OutputChunkListener | None") -> None:
        """Set or replace the incremental output listener (thread-safe: written before execution)."""
        self._output_listener = listener

    def set_lifecycle_listener(self, listener: "LifecycleListener | None") -> None:
        """Set or replace the lifecycle event listener."""
        self._lifecycle_listener = listener

    def cancel(self, execution_id: str) -> bool:
        """Cancel the active execution identified by execution_id.

        Returns True if the execution was found and cancellation was initiated.
        Returns False if no matching active execution exists.

        Thread-safe: may be called from any thread while execution is in progress.
        """
        with self._concurrency_lock:
            if (
                self._active_execution_id != execution_id
                or not self._is_executing
                or self._active_cancellation_requested
            ):
                return False
            self._active_cancellation_requested = True
            proc = self._active_proc
            pgid = self._active_pgid

        if proc is not None:
            logger.info("Cancelling execution %s", execution_id)
            self._terminate_execution_once(execution_id, proc, pgid=pgid)
            return True
        return False

    @property
    def workspace_root(self) -> Path:
        return self._path_resolver.workspace_root

    def validate_working_directory(self, cwd_param: Path | str | None) -> Path:
        """Validate that working directory exists, is canonical, and does not escape workspace."""
        if cwd_param is None or str(cwd_param).strip() in ("", ".", "./"):
            return self.workspace_root

        # Handle absolute Path or string
        subpath_str = str(cwd_param).strip()
        if subpath_str.startswith(("/", "\\")) or (
            isinstance(cwd_param, Path) and cwd_param.is_absolute()
        ):
            resolved = Path(subpath_str).resolve()
            if not resolved.exists():
                raise WorkspaceSecurityError(f"Working directory '{subpath_str}' does not exist.")
            if not resolved.is_dir():
                raise WorkspaceSecurityError(
                    f"Working directory '{subpath_str}' is not a directory."
                )
            if resolved != self.workspace_root and self.workspace_root not in resolved.parents:
                raise WorkspaceSecurityError(
                    f"Working directory '{subpath_str}' escapes authorized workspace root '{self.workspace_root}'."
                )
            # Recheck symlinks
            raw = Path(subpath_str)
            if raw.is_symlink():
                raise WorkspaceSecurityError(f"Working directory '{subpath_str}' is a symlink.")
            return resolved

        # Relative path within workspace
        try:
            resolved = self._path_resolver.resolve_path(subpath_str, must_exist=True)
            if not resolved.is_dir():
                raise WorkspaceSecurityError(
                    f"Working directory '{subpath_str}' is not a directory."
                )
            return resolved
        except Exception as exc:
            raise WorkspaceSecurityError(
                f"Working directory validation failed for '{cwd_param}': {exc}"
            ) from exc

    def execute(self, request: ExecutionRequest) -> ExecutionOutput:
        """Synchronously dispatch an ExecutionRequest through the isolated worker pool."""
        if self._is_shut_down:
            raise ExecutionError("ExecutionManager has been shut down.")

        future = self._executor_pool.submit(self._run_internal, request)
        return future.result()

    def run_command(
        self,
        command: str,
        args: list[str] | None = None,
        cwd: Path | str | None = None,
        timeout_seconds: float | None = None,
        execution_id: str | None = None,
        conversation_id: str = "",
        client_request_id: str = "",
    ) -> ExecutionOutput:
        """High-level entrypoint constructing an ExecutionRequest and executing it."""
        valid_args = args if args is not None else []
        valid_cwd = self.validate_working_directory(cwd)

        effective_timeout = (
            float(timeout_seconds) if timeout_seconds is not None else self._default_timeout
        )
        effective_timeout = min(effective_timeout, self._max_timeout)

        req_id = execution_id or f"sub_{uuid.uuid4().hex[:12]}"
        request = ExecutionRequest(
            execution_id=req_id,
            command=command,
            args=valid_args,
            cwd=valid_cwd,
            timeout_seconds=effective_timeout,
            conversation_id=conversation_id,
            client_request_id=client_request_id,
        )
        return self.execute(request)

    def _run_internal(self, request: ExecutionRequest) -> ExecutionOutput:
        """Internal execution loop executed on the isolated worker thread."""
        # 1. Enforce concurrency limit: 1 subprocess at a time
        with self._concurrency_lock:
            if self._is_executing:
                raise ExecutionConcurrencyError(
                    "Another subprocess is currently executing. Concurrency limit is 1."
                )
            self._is_executing = True
            self._active_execution_id = request.execution_id
            self._active_proc = None
            self._active_pgid = None

        started_at = utc_now()
        start_mono = time.monotonic()
        binary_path = None
        dir_fd = -1
        proc = None
        pgid = None
        batcher: OutputBatcher | None = None

        self._record_event(
            "tool.execution.requested",
            request.execution_id,
            {
                "command": request.command,
                "args_count": len(request.args),
                "cwd": str(request.cwd),
                "timeout_seconds": request.timeout_seconds,
                "conversation_id": request.conversation_id,
                "client_request_id": request.client_request_id,
            },
        )

        # Notify lifecycle listener that execution started
        self._emit_lifecycle(
            request.execution_id,
            "started",
            {
                "command": request.command,
                "args": list(request.args),
                "cwd": str(request.cwd),
                "timeout_seconds": request.timeout_seconds,
            },
        )

        try:
            # 2. Validation
            binary_path = resolve_executable(request.command, self._allowed_commands)
            validate_command_arguments(
                request.command, request.args, workspace_root=self.workspace_root
            )
            working_dir = self.validate_working_directory(request.cwd)
            env = build_sanitized_environment(self.workspace_root)

            # Working directory descriptor pinning with component-by-component O_NOFOLLOW
            dir_fd = open_directory_descriptor(self.workspace_root, working_dir)
            proc_fd_path = Path(f"/proc/self/fd/{dir_fd}")
            cwd_str = str(proc_fd_path) if proc_fd_path.exists() else str(working_dir)

            # Assemble argv and env with enforced safe defaults
            if request.command == "git":
                safe_git_env = get_safe_git_env(self.workspace_root)
                env.update(safe_git_env)

                subcommand = "status"
                subcommand_index = None
                for idx, a in enumerate(request.args):
                    if not a.startswith("-"):
                        subcommand = a
                        subcommand_index = idx
                        break
                    if a in ("--version", "-v"):
                        subcommand = "version"
                        subcommand_index = idx
                        break

                filter_overrides = get_configured_filter_overrides(working_dir, safe_git_env)
                git_global_flags = [*GIT_HARDENED_GLOBAL_FLAGS, *filter_overrides]

                subcommand_extras = []
                if subcommand in ("diff", "log", "show"):
                    if "--no-ext-diff" not in request.args:
                        subcommand_extras.append("--no-ext-diff")
                    if "--no-textconv" not in request.args:
                        subcommand_extras.append("--no-textconv")

                if subcommand_index is not None and subcommand_extras:
                    reconstructed_args = [
                        *request.args[: subcommand_index + 1],
                        *subcommand_extras,
                        *request.args[subcommand_index + 1 :],
                    ]
                else:
                    reconstructed_args = [*request.args, *subcommand_extras]

                argv = [str(binary_path), *git_global_flags, *reconstructed_args]
            elif request.command == "pytest":
                env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
                safe_pytest_flags = ["-p", "no:cacheprovider"]
                argv = [str(binary_path), *safe_pytest_flags, *request.args]
            else:
                argv = [str(binary_path), *request.args]

            logger.info("Starting subprocess %s: %s in %s", request.execution_id, argv, working_dir)

            # 3. Process Launch
            proc = subprocess.Popen(
                argv,
                shell=False,
                cwd=cwd_str,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,  # Process group leader
            )
            pgid = proc.pid

            # Register active process for cancellation
            with self._concurrency_lock:
                self._active_proc = proc
                self._active_pgid = pgid

            self._record_event(
                "tool.execution.started",
                request.execution_id,
                {
                    "command": request.command,
                    "pid": proc.pid,
                    "conversation_id": request.conversation_id,
                    "client_request_id": request.client_request_id,
                },
            )

            # 4. Concurrent Stream Draining with optional live output
            stdout_buffer = bytearray()
            stderr_buffer = bytearray()
            buffer_lock = threading.Lock()
            overflow = [False]

            # Create output batcher if listener is configured
            output_listener = self._output_listener
            if output_listener is not None:
                batcher = OutputBatcher(
                    execution_id=request.execution_id,
                    emit_fn=output_listener,
                )
            else:
                batcher = None

            # Wire per-request listener if present (from ExecutionRequest)
            req_listener = getattr(request, "output_listener", None)
            if req_listener is not None and batcher is None:
                batcher = OutputBatcher(
                    execution_id=request.execution_id,
                    emit_fn=req_listener,
                )
            elif req_listener is not None and batcher is not None:
                # Wrap both listeners
                _orig_listener = output_listener
                _req_listener = req_listener

                def _combined_listener(eid: str, stream: str, text: str) -> None:
                    if _orig_listener:
                        try:
                            _orig_listener(eid, stream, text)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("Output listener error: %s", exc)
                    try:
                        _req_listener(eid, stream, text)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Request output listener error: %s", exc)

                batcher = OutputBatcher(
                    execution_id=request.execution_id,
                    emit_fn=_combined_listener,
                )

            half_cap = self._max_output_bytes // 2
            stdout_total_bytes = [0]
            stderr_total_bytes = [0]
            t_out = threading.Thread(
                target=_drain_stream,
                args=(
                    proc.stdout,
                    stdout_buffer,
                    half_cap,
                    overflow,
                    buffer_lock,
                    "stdout",
                    batcher,
                    stdout_total_bytes,
                ),
                daemon=True,
            )
            t_err = threading.Thread(
                target=_drain_stream,
                args=(
                    proc.stderr,
                    stderr_buffer,
                    half_cap,
                    overflow,
                    buffer_lock,
                    "stderr",
                    batcher,
                    stderr_total_bytes,
                ),
                daemon=True,
            )
            t_out.start()
            t_err.start()

            # 5. Wait with wall-clock timeout & process-group cleanup
            is_timeout = False
            try:
                proc.wait(timeout=request.timeout_seconds)
            except subprocess.TimeoutExpired:
                is_timeout = True
                self._terminate_execution_once(request.execution_id, proc, pgid=pgid)

            # Reap child process before joining reader threads so OS closes pipe write ends
            if proc.poll() is None:
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass

            # Wait for reader threads to observe EOF and close their own BufferedReaders safely
            t_out.join(timeout=1.0)
            t_err.join(timeout=1.0)
            if t_out.is_alive():
                logger.warning(
                    "Stdout reader thread for %s did not exit within 1.0s "
                    "(a detached descendant may still hold the pipe open).",
                    request.execution_id,
                )
            if t_err.is_alive():
                logger.warning(
                    "Stderr reader thread for %s did not exit within 1.0s "
                    "(a detached descendant may still hold the pipe open).",
                    request.execution_id,
                )

            # Final batcher flush to guarantee all output is delivered
            if batcher is not None:
                batcher.close()

            completed_at = utc_now()
            duration_ms = (time.monotonic() - start_mono) * 1000.0

            out_text = stdout_buffer.decode("utf-8", errors="replace")
            err_text = stderr_buffer.decode("utf-8", errors="replace")
            truncated = overflow[0]
            out_bytes_count = stdout_total_bytes[0]
            err_bytes_count = stderr_total_bytes[0]

            # Check if cancellation was requested for this execution
            with self._concurrency_lock:
                was_cancelled = (
                    self._active_execution_id == request.execution_id
                    and self._active_cancellation_requested
                )

            if was_cancelled:
                self._record_event(
                    "tool.execution.cancelled",
                    request.execution_id,
                    {
                        "command": request.command,
                        "duration_ms": duration_ms,
                        "truncated": truncated,
                        "stdout_bytes": out_bytes_count,
                        "stderr_bytes": err_bytes_count,
                    },
                )
                self._emit_lifecycle(
                    request.execution_id,
                    "cancelled",
                    {
                        "exit_code": proc.returncode,
                        "duration_ms": duration_ms,
                        "truncated": truncated,
                        "error_message": "Execution was cancelled.",
                    },
                )
                return ExecutionOutput(
                    execution_id=request.execution_id,
                    command=request.command,
                    exit_code=proc.returncode,
                    status=ExecutionStatus.CANCELLED,
                    stdout=out_text,
                    stderr=err_text,
                    truncated=truncated,
                    duration_ms=duration_ms,
                    started_at=started_at,
                    completed_at=completed_at,
                    error_message="Execution was cancelled.",
                    stdout_bytes=out_bytes_count,
                    stderr_bytes=err_bytes_count,
                )

            if is_timeout:
                self._record_event(
                    "tool.execution.timed_out",
                    request.execution_id,
                    {
                        "command": request.command,
                        "timeout_seconds": request.timeout_seconds,
                        "duration_ms": duration_ms,
                        "truncated": truncated,
                        "stdout_bytes": out_bytes_count,
                        "stderr_bytes": err_bytes_count,
                    },
                )
                self._emit_lifecycle(
                    request.execution_id,
                    "timed_out",
                    {
                        "exit_code": proc.returncode,
                        "duration_ms": duration_ms,
                        "truncated": truncated,
                        "timeout_seconds": request.timeout_seconds,
                        "error_message": f"Command '{request.command}' timed out after {request.timeout_seconds:.1f}s.",
                    },
                )
                return ExecutionOutput(
                    execution_id=request.execution_id,
                    command=request.command,
                    exit_code=proc.returncode,
                    status=ExecutionStatus.TIMED_OUT,
                    stdout=out_text,
                    stderr=err_text,
                    truncated=truncated,
                    duration_ms=duration_ms,
                    started_at=started_at,
                    completed_at=completed_at,
                    error_message=f"Command '{request.command}' timed out after {request.timeout_seconds:.1f}s.",
                    stdout_bytes=out_bytes_count,
                    stderr_bytes=err_bytes_count,
                )

            # 6. Normal completion
            exit_code = proc.returncode
            status = ExecutionStatus.COMPLETED if exit_code == 0 else ExecutionStatus.FAILED
            event_type = "tool.execution.completed" if exit_code == 0 else "tool.execution.failed"

            self._record_event(
                event_type,
                request.execution_id,
                {
                    "command": request.command,
                    "exit_code": exit_code,
                    "status": status.value,
                    "duration_ms": duration_ms,
                    "truncated": truncated,
                    "stdout_bytes": out_bytes_count,
                    "stderr_bytes": err_bytes_count,
                },
            )
            self._emit_lifecycle(
                request.execution_id,
                status.value,
                {
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                    "truncated": truncated,
                    "error_message": None
                    if exit_code == 0
                    else f"Process exited with code {exit_code}",
                },
            )

            return ExecutionOutput(
                execution_id=request.execution_id,
                command=request.command,
                exit_code=exit_code,
                status=status,
                stdout=out_text,
                stderr=err_text,
                truncated=truncated,
                duration_ms=duration_ms,
                started_at=started_at,
                completed_at=completed_at,
                error_message=None if exit_code == 0 else f"Process exited with code {exit_code}",
                stdout_bytes=out_bytes_count,
                stderr_bytes=err_bytes_count,
            )

        except Exception as exc:  # noqa: BLE001
            completed_at = utc_now()
            duration_ms = (time.monotonic() - start_mono) * 1000.0
            logger.error("ExecutionManager error for %s: %s", request.execution_id, exc)

            if proc is not None:
                self._terminate_process_group(proc, pgid=pgid)

            self._record_event(
                "tool.execution.failed",
                request.execution_id,
                {
                    "command": request.command,
                    "status": "failed",
                    "error": str(exc),
                    "duration_ms": duration_ms,
                },
            )
            self._emit_lifecycle(
                request.execution_id,
                "failed",
                {
                    "exit_code": -1,
                    "duration_ms": duration_ms,
                    "truncated": False,
                    "error_message": str(exc),
                },
            )

            return ExecutionOutput(
                execution_id=request.execution_id,
                command=request.command,
                exit_code=-1,
                status=ExecutionStatus.FAILED,
                stdout="",
                stderr="",
                truncated=False,
                duration_ms=duration_ms,
                started_at=started_at,
                completed_at=completed_at,
                error_message=str(exc),
            )

        finally:
            if dir_fd >= 0:
                try:
                    os.close(dir_fd)
                except OSError:
                    pass
            with self._concurrency_lock:
                self._is_executing = False
                self._active_execution_id = None
                self._active_proc = None
                self._active_pgid = None
                self._active_cancellation_requested = False
                self._active_terminated = False

    @staticmethod
    def _is_process_group_alive(pgid: int) -> bool:
        """Check if any process belonging to process group pgid is alive."""
        if pgid <= 0 or pgid == os.getpid():
            return False
        try:
            os.killpg(pgid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _terminate_process_group(
        self, proc: subprocess.Popen, pgid: int | None = None, *, close_pipes: bool = True
    ) -> None:
        """Safely terminate child's process group, escalating from SIGTERM to SIGKILL.

        Security boundary and containment limitation:
        Standard POSIX process-group management tracks processes sharing the leader's PGID
        (created via `start_new_session=True`). However, if an untrusted child process explicitly
        escapes its process group by creating a new session (`os.setsid()`) or migrating to a new
        process group (`os.setpgid(0, 0)`), standard `os.killpg(pgid)` signals CANNOT reach
        those detached descendants without OS-level namespace or cgroup containment.

        Future OS-level containment will require:
        - Linux cgroups v2 (`cgroup.kill` or freezer controller) to guarantee tree containment.
        - PID namespaces (via bubblewrap, user namespaces, or systemd scopes) to ensure
          descendants cannot escape the container process hierarchy.
        In this milestone, subprocess execution is strictly restricted to allowlisted commands
        and requires human-in-the-loop approval.
        """
        effective_pgid = pgid if pgid is not None and pgid > 0 else proc.pid

        # 1. First attempt: SIGTERM to entire process group
        try:
            if effective_pgid > 0 and effective_pgid != os.getpid():
                os.killpg(effective_pgid, signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError):
            pass

        # 2. Poll process group liveness for up to 0.5s
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if not self._is_process_group_alive(effective_pgid) and proc.poll() is not None:
                break
            time.sleep(0.02)

        # 3. Escalate to SIGKILL if process group is still alive or leader hasn't reaped
        if self._is_process_group_alive(effective_pgid) or proc.poll() is None:
            try:
                if effective_pgid > 0 and effective_pgid != os.getpid():
                    os.killpg(effective_pgid, signal.SIGKILL)
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError):
                pass

            try:
                proc.wait(timeout=0.5)
            except (subprocess.TimeoutExpired, Exception) as exc:  # noqa: BLE001
                logger.debug("Process wait error during SIGKILL cleanup: %s", exc)

        if close_pipes:
            try:
                if proc.stdout and not proc.stdout.closed:
                    proc.stdout.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed closing stdout pipe during termination: %s", exc)
            try:
                if proc.stderr and not proc.stderr.closed:
                    proc.stderr.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed closing stderr pipe during termination: %s", exc)

    def _terminate_execution_once(
        self,
        execution_id: str,
        proc: subprocess.Popen,
        pgid: int | None = None,
    ) -> None:
        """Idempotently terminate an execution's process group once across cancel/shutdown/timeout."""
        with self._concurrency_lock:
            if self._active_execution_id != execution_id or self._active_terminated:
                return
            self._active_terminated = True
        self._terminate_process_group(proc, pgid=pgid, close_pipes=False)

    def shutdown(self, wait: bool = True, timeout: float = 5.0) -> None:
        """Shut down the isolated worker pool cleanly.

        Cancels any active execution before shutting down the worker pool.
        """
        self._is_shut_down = True

        # Cancel any active execution during shutdown
        with self._concurrency_lock:
            active_id = self._active_execution_id
            proc = self._active_proc
            pgid = self._active_pgid
            if active_id is not None:
                self._active_cancellation_requested = True

        if proc is not None and active_id is not None:
            logger.info("Shutdown: cancelling active execution %s", active_id)
            self._terminate_execution_once(active_id, proc, pgid=pgid)

        self._executor_pool.shutdown(wait=wait, cancel_futures=True)

    def _record_event(self, event_type: str, execution_id: str, payload: dict[str, Any]) -> None:
        if self._event_recorder is not None:
            try:
                self._event_recorder(event_type, "tool_execution", execution_id, payload)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to record execution audit event: %s", event_type)

    def _emit_lifecycle(
        self,
        execution_id: str,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit a lifecycle event to the lifecycle listener if configured."""
        if self._lifecycle_listener is not None:
            try:
                self._lifecycle_listener(execution_id, event, payload)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to emit lifecycle event '%s' for %s", event, execution_id)
