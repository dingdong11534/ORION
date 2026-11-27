"""Application-layer allowlisted desktop application launcher."""

from orion.application.launcher.app_registry import (
    AppLaunchResult,
    ApplicationApprovalError,
    ApplicationLaunchError,
    ApplicationRegistryService,
    ApprovedAppRecord,
)

__all__ = [
    "AppLaunchResult",
    "ApplicationApprovalError",
    "ApplicationLaunchError",
    "ApplicationRegistryService",
    "ApprovedAppRecord",
]
