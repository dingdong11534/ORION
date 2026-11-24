"""Typed Pydantic contracts for read-only workspace inspection tools."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# 1. workspace_get_working_directory
# ---------------------------------------------------------------------------


class WorkspaceGetWorkingDirectoryInput(BaseModel):
    """Input for retrieving the configured workspace root (no arguments allowed)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class WorkspaceGetWorkingDirectoryOutput(BaseModel):
    """Output containing the display path of the active workspace root."""

    model_config = ConfigDict(frozen=True)

    workspace_root: str = Field(..., description="Absolute path of the authorized workspace root.")


# ---------------------------------------------------------------------------
# 2. workspace_list_directory
# ---------------------------------------------------------------------------


class DirectoryEntry(BaseModel):
    """Metadata describing a single entry inside a workspace directory."""

    model_config = ConfigDict(frozen=True)

    name: str
    entry_type: Literal["file", "directory", "symlink", "other"]
    size_bytes: int | None = None
    is_excluded: bool = False


class WorkspaceListDirectoryInput(BaseModel):
    """Input parameters for listing entries within a workspace directory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(
        default="",
        description="Relative directory path within workspace. Empty string denotes workspace root.",
    )
    max_entries: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum number of entries to return (capped at 500).",
    )


class WorkspaceListDirectoryOutput(BaseModel):
    """Output containing directory listing results."""

    model_config = ConfigDict(frozen=True)

    relative_path: str
    entries: list[DirectoryEntry]
    total_entries: int
    truncated: bool = False


# ---------------------------------------------------------------------------
# 3. workspace_read_file
# ---------------------------------------------------------------------------

MAX_READ_BYTES = 131072  # 128 KB hard cap


class WorkspaceReadFileInput(BaseModel):
    """Input parameters for reading a text file within the workspace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(..., min_length=1, description="Relative file path within workspace.")
    max_bytes: int = Field(
        default=MAX_READ_BYTES,
        ge=1,
        le=MAX_READ_BYTES,
        description="Maximum bytes to read (capped at 128 KB).",
    )


class WorkspaceReadFileOutput(BaseModel):
    """Output containing the text content of a workspace file."""

    model_config = ConfigDict(frozen=True)

    relative_path: str
    content: str
    size_bytes: int
    truncated: bool = False


# ---------------------------------------------------------------------------
# 4. workspace_search_files
# ---------------------------------------------------------------------------


class SearchMatch(BaseModel):
    """A single literal search match within a workspace file."""

    model_config = ConfigDict(frozen=True)

    relative_path: str
    line_number: int
    line_content: str


class WorkspaceSearchFilesInput(BaseModel):
    """Input parameters for searching files within the workspace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(..., min_length=1, description="Literal text query to search for.")
    path: str = Field(
        default="",
        description="Relative directory path to search within. Empty string denotes workspace root.",
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum matching lines to return (capped at 100).",
    )
    case_sensitive: bool = Field(
        default=True,
        description="Whether the literal query match is case-sensitive.",
    )


class WorkspaceSearchFilesOutput(BaseModel):
    """Output containing search matches across workspace files."""

    model_config = ConfigDict(frozen=True)

    query: str
    matches: list[SearchMatch]
    total_matches: int
    files_inspected: int
    truncated: bool = False


# ---------------------------------------------------------------------------
# 5. workspace_git_status
# ---------------------------------------------------------------------------


class WorkspaceGitStatusInput(BaseModel):
    """Input for inspecting working-tree Git status (no arguments allowed)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class WorkspaceGitStatusOutput(BaseModel):
    """Output containing Git repository working tree and branch status."""

    model_config = ConfigDict(frozen=True)

    is_git_repo: bool
    branch: str | None = None
    is_clean: bool = True
    staged_changes: list[str] = Field(default_factory=list)
    unstaged_changes: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# 6. workspace_git_diff
# ---------------------------------------------------------------------------


class WorkspaceGitDiffInput(BaseModel):
    """Input parameters for inspecting Git diffs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(
        default="",
        description="Optional relative file or directory path to inspect diffs for.",
    )
    staged: bool = Field(
        default=False,
        description="Whether to inspect staged changes (--cached) instead of unstaged.",
    )
    max_bytes: int = Field(
        default=65536,
        ge=1,
        le=131072,
        description="Maximum diff text bytes to return (capped at 128 KB).",
    )


class WorkspaceGitDiffOutput(BaseModel):
    """Output containing bounded read-only Git diff text."""

    model_config = ConfigDict(frozen=True)

    diff_text: str
    truncated: bool = False
    untracked_files: list[str] = Field(default_factory=list)
