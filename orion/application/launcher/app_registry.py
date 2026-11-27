"""Allowlisted desktop application registry and safe subprocess launcher."""

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import sessionmaker

from orion.application.jobs.ports import BackgroundJobStorePort
from orion.infrastructure.persistence.models import ApprovedApplicationModel, utc_now

logger = logging.getLogger(__name__)


class ApplicationApprovalError(PermissionError):
    """Raised when launching an unapproved, disabled, or disallowed-argument app."""


class ApplicationLaunchError(RuntimeError):
    """Raised when an approved application fails to launch."""


@dataclass(frozen=True, slots=True)
class ApprovedAppRecord:
    """Allowlisted application entry."""

    id: str
    display_name: str
    executable_path: str
    allowed_args: tuple[str, ...]
    enabled: bool
    approved_by_user: bool


@dataclass(frozen=True, slots=True)
class AppLaunchResult:
    """Result of launching an allowlisted desktop application."""

    app_id: str
    pid: int
    executable_path: str
    argv: tuple[str, ...]


class ApplicationRegistryService:
    """Manages allowlisted desktop applications and launches them with shell=False."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        audit_store: BackgroundJobStorePort | None = None,
        popen_factory: Any = subprocess.Popen,
    ) -> None:
        self._session_factory = session_factory
        self._audit_store = audit_store
        self._popen_factory = popen_factory

    def register_application(
        self,
        app_id: str,
        display_name: str,
        executable_path: str,
        allowed_args: tuple[str, ...] | list[str] = (),
        *,
        approved_by_user: bool = False,
        enabled: bool = True,
    ) -> ApprovedAppRecord:
        resolved = Path(executable_path).expanduser().resolve()
        args_list = [str(a) for a in allowed_args]
        with self._session_factory() as session:
            row = session.get(ApprovedApplicationModel, app_id)
            if row is None:
                row = ApprovedApplicationModel(
                    id=app_id,
                    display_name=display_name,
                    executable_path=str(resolved),
                    allowed_args_json=json.dumps(args_list),
                    enabled=enabled,
                    approved_by_user=approved_by_user,
                )
                session.add(row)
            else:
                row.display_name = display_name
                row.executable_path = str(resolved)
                row.allowed_args_json = json.dumps(args_list)
                row.enabled = enabled
                row.approved_by_user = approved_by_user
                row.updated_at = utc_now()
            session.commit()
            return self._to_record(row)

    def approve_application(self, app_id: str, *, approved: bool = True) -> ApprovedAppRecord:
        with self._session_factory() as session:
            row = session.get(ApprovedApplicationModel, app_id)
            if row is None:
                raise KeyError(f"Unknown application id: {app_id}")
            row.approved_by_user = approved
            row.updated_at = utc_now()
            session.commit()
            return self._to_record(row)

    def list_applications(self) -> list[ApprovedAppRecord]:
        with self._session_factory() as session:
            rows = (
                session.query(ApprovedApplicationModel)
                .order_by(ApprovedApplicationModel.display_name.asc())
                .all()
            )
            return [self._to_record(r) for r in rows]

    def detect_default_apps(self) -> list[ApprovedAppRecord]:
        """Detect known desktop apps (e.g. Google Chrome) without auto-approving them."""
        detected: list[ApprovedAppRecord] = []
        chrome = shutil.which("google-chrome") or shutil.which("chromium")
        if chrome:
            with self._session_factory() as session:
                existing = session.get(ApprovedApplicationModel, "google-chrome")
            if existing is None:
                detected.append(
                    self.register_application(
                        "google-chrome",
                        "Google Chrome",
                        chrome,
                        allowed_args=("--new-window",),
                        approved_by_user=False,
                        enabled=True,
                    )
                )
        return detected

    def launch_application(
        self,
        app_id: str,
        args: tuple[str, ...] | list[str] = (),
        *,
        job_id: str | None = None,
        request_origin: str = "user",
    ) -> AppLaunchResult:
        with self._session_factory() as session:
            row = session.get(ApprovedApplicationModel, app_id)
            record = self._to_record(row) if row is not None else None

        if record is None:
            self._record_audit(
                job_id=job_id,
                capability_name=f"launch:{app_id}",
                request_origin=request_origin,
                approval_state="denied",
                status="failed",
                error_summary=f"Application {app_id!r} is not registered.",
            )
            raise ApplicationApprovalError(f"Application {app_id!r} is not registered.")

        if not record.enabled or not record.approved_by_user:
            self._record_audit(
                job_id=job_id,
                capability_name=f"launch:{app_id}",
                request_origin=request_origin,
                approval_state="requires_approval" if not record.approved_by_user else "disabled",
                status="denied",
                error_summary=f"Application {app_id!r} is not approved or is disabled.",
            )
            raise ApplicationApprovalError(
                f"Application {app_id!r} requires explicit user approval and must be enabled."
            )

        requested_args = tuple(str(a) for a in args)
        for arg in requested_args:
            if arg not in record.allowed_args:
                self._record_audit(
                    job_id=job_id,
                    capability_name=f"launch:{app_id}",
                    request_origin=request_origin,
                    approval_state="denied",
                    status="failed",
                    error_summary=f"Disallowed argument: {arg!r}",
                )
                raise ApplicationApprovalError(
                    f"Argument {arg!r} is not in allowlist for {app_id!r}."
                )

        exe_path = Path(record.executable_path)
        if not exe_path.is_file():
            self._record_audit(
                job_id=job_id,
                capability_name=f"launch:{app_id}",
                request_origin=request_origin,
                approval_state="approved",
                status="failed",
                error_summary=f"Executable not found: {exe_path}",
            )
            raise ApplicationLaunchError(f"Executable not found: {exe_path}")

        argv = (str(exe_path), *requested_args)
        try:
            proc = self._popen_factory(
                list(argv),
                shell=False,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self._record_audit(
                job_id=job_id,
                capability_name=f"launch:{app_id}",
                request_origin=request_origin,
                approval_state="approved",
                status="failed",
                error_summary=str(exc),
            )
            raise ApplicationLaunchError(f"Failed launching {app_id!r}: {exc}") from exc

        self._record_audit(
            job_id=job_id,
            capability_name=f"launch:{app_id}",
            request_origin=request_origin,
            approval_state="approved",
            status="succeeded",
        )
        return AppLaunchResult(
            app_id=app_id,
            pid=int(proc.pid),
            executable_path=str(exe_path),
            argv=argv,
        )

    def _record_audit(
        self,
        *,
        job_id: str | None,
        capability_name: str,
        request_origin: str,
        approval_state: str,
        status: str,
        error_summary: str | None = None,
    ) -> None:
        if self._audit_store is None:
            return
        try:
            self._audit_store.record_audit_event(
                job_id=job_id,
                capability_name=capability_name,
                risk_level="high",
                request_origin=request_origin,
                approval_state=approval_state,
                status=status,
                error_summary=error_summary,
            )
        except Exception:
            logger.debug("Failed recording launcher audit event", exc_info=True)

    @staticmethod
    def _to_record(row: ApprovedApplicationModel) -> ApprovedAppRecord:
        try:
            raw_args = json.loads(row.allowed_args_json or "[]")
            allowed_args = tuple(str(x) for x in raw_args)
        except json.JSONDecodeError:
            allowed_args = ()
        return ApprovedAppRecord(
            id=row.id,
            display_name=row.display_name,
            executable_path=row.executable_path,
            allowed_args=allowed_args,
            enabled=row.enabled,
            approved_by_user=row.approved_by_user,
        )
