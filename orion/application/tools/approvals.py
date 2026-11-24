"""Approval broker for human-in-the-loop tool execution authorization.

Manages the lifecycle of single-use, time-bounded approval requests that bind
a cryptographic nonce to an exact ToolInvocation. No approval can authorize
execution of a different invocation, a replayed nonce, or an expired request.

Security invariants:
- Nonces are generated via ``secrets.token_hex(16)`` (128-bit random).
- Each nonce is burned on first resolution (approve or reject).
- Expiration is checked against a monotonic clock, not wall-clock time.
- The LLM never sees or supplies nonces, approval IDs, or decisions.
"""

import hashlib
import json
import logging
import secrets
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orion.application.tools.contracts import (
    ToolDefinition,
    ToolInvocation,
    utc_now,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ApprovalStatus(StrEnum):
    """Lifecycle states of a tool approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


class ApprovalRequest(BaseModel):
    """Immutable record of a pending human-approval request.

    Created exclusively by ``ApprovalBroker`` — never by the LLM.
    """

    model_config = ConfigDict(frozen=True)

    approval_id: str
    nonce: str
    execution_id: str
    client_request_id: str
    conversation_id: str
    tool_id: str
    tool_name: str
    tool_description: str
    arguments: dict[str, Any]
    action_summary: str
    is_mutating: bool
    created_at: Any
    expires_at: Any
    monotonic_deadline: float
    invocation_hash: str
    status: ApprovalStatus = ApprovalStatus.PENDING

    def to_ui_dict(self) -> dict[str, Any]:
        """Convert to plain Python dictionary for safe presentation to UI."""
        created_str = (
            self.created_at.isoformat()
            if hasattr(self.created_at, "isoformat")
            else str(self.created_at)
        )
        expires_str = (
            self.expires_at.isoformat()
            if hasattr(self.expires_at, "isoformat")
            else str(self.expires_at)
        )
        return {
            "approval_id": self.approval_id,
            "nonce": self.nonce,
            "execution_id": self.execution_id,
            "client_request_id": self.client_request_id,
            "conversation_id": self.conversation_id,
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "tool_description": self.tool_description,
            "arguments": self.arguments,
            "action_summary": self.action_summary,
            "is_mutating": self.is_mutating,
            "created_at": created_str,
            "expires_at": expires_str,
            "status": self.status.value,
        }


class ApprovalDecision(BaseModel):
    """Human decision submitted through the trusted QML UI."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    nonce: str
    approved: bool
    decided_at: Any = Field(default_factory=utc_now)


class ApprovalResolution(BaseModel):
    """Outcome of resolving an approval decision against the broker state."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    status: ApprovalStatus
    invocation: ToolInvocation | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Event recorder protocol
# ---------------------------------------------------------------------------

EventRecorder = Callable[[str, str, str | None, dict[str, Any]], None]
"""Signature: (event_type, aggregate_type, aggregate_id, payload) -> None."""


# ---------------------------------------------------------------------------
# Canonical invocation hashing
# ---------------------------------------------------------------------------


def _canonical_invocation_hash(invocation: ToolInvocation) -> str:
    """Compute a deterministic SHA-256 digest binding the exact invocation identity.

    Fields included: tool_id, arguments (JSON-sorted), execution_id,
    client_request_id, conversation_id.
    """
    canonical = json.dumps(
        {
            "arguments": invocation.arguments,
            "client_request_id": invocation.client_request_id,
            "conversation_id": invocation.conversation_id,
            "execution_id": invocation.execution_id,
            "tool_id": invocation.tool_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# ApprovalBroker
# ---------------------------------------------------------------------------


class ApprovalBroker:
    """In-memory broker managing single-use, time-bounded tool approval requests.

    Thread-safety: designed to be called from the Qt main thread (signal/slot
    dispatch).

    Parameters
    ----------
    clock:
        Monotonic clock callable (injectable for deterministic tests).
    ttl_seconds:
        Time-to-live for each approval request (default 60.0).
    event_recorder:
        Optional callback for audit logging. Receives
        ``(event_type, aggregate_type, aggregate_id, payload)``.
    request_listener:
        Optional callback notified whenever a new ApprovalRequest is created.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = 60.0,
        event_recorder: EventRecorder | None = None,
        request_listener: Callable[[ApprovalRequest], None] | None = None,
    ) -> None:
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._event_recorder = event_recorder
        self._request_listener = request_listener

        # approval_id -> (ApprovalRequest, ToolInvocation)
        self._pending: dict[str, tuple[ApprovalRequest, ToolInvocation]] = {}

        # Nonces that have been consumed (burned after first use)
        self._burned_nonces: set[str] = set()

    def set_request_listener(self, listener: Callable[[ApprovalRequest], None] | None) -> None:
        """Register a callback for new approval request creations."""
        self._request_listener = listener

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create_request(
        self,
        tool: ToolDefinition,
        invocation: ToolInvocation,
    ) -> ApprovalRequest:
        """Create a pending approval request bound to an exact invocation.

        Returns the ``ApprovalRequest`` containing a fresh nonce. The nonce
        must be forwarded to the QML dialog exclusively — never to the LLM.
        """
        now_wall = utc_now()
        now_mono = self._clock()

        approval_id = f"appr_{uuid.uuid4().hex}"
        nonce = secrets.token_hex(16)
        inv_hash = _canonical_invocation_hash(invocation)

        request = ApprovalRequest(
            approval_id=approval_id,
            nonce=nonce,
            execution_id=invocation.execution_id,
            client_request_id=invocation.client_request_id,
            conversation_id=invocation.conversation_id,
            tool_id=tool.tool_id,
            tool_name=tool.name,
            tool_description=tool.description,
            arguments=invocation.arguments,
            action_summary=f"{tool.name}: {tool.description}",
            is_mutating=tool.is_mutating,
            created_at=now_wall,
            expires_at=now_wall + timedelta(seconds=self._ttl_seconds),
            monotonic_deadline=now_mono + self._ttl_seconds,
            invocation_hash=inv_hash,
        )

        self._pending[approval_id] = (request, invocation)
        self._record_event(
            "tool.approval_requested",
            approval_id,
            {
                "tool_id": tool.tool_id,
                "tool_name": tool.name,
                "execution_id": invocation.execution_id,
                "client_request_id": invocation.client_request_id,
                "conversation_id": invocation.conversation_id,
                "is_mutating": tool.is_mutating,
            },
        )

        logger.info(
            "Approval request %s created for tool '%s' (execution %s)",
            approval_id,
            tool.tool_id,
            invocation.execution_id,
        )

        if self._request_listener is not None:
            try:
                self._request_listener(request)
            except Exception:  # noqa: BLE001
                logger.warning("Request listener error for approval %s", approval_id)

        return request

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        approval_id: str,
        nonce: str,
        approved: bool,
        invocation: ToolInvocation | None = None,
    ) -> ApprovalResolution:
        """Submit a human decision for a pending approval request.

        Validates nonce, expiration, and exact invocation binding before
        allowing execution.

        Returns an ``ApprovalResolution`` with the resulting status and,
        on approval, the original trusted ``ToolInvocation``.
        """
        # 1. Check if nonce was already used (replay prevention)
        if nonce in self._burned_nonces:
            logger.warning("Replayed nonce for approval %s", approval_id)
            return ApprovalResolution(
                approval_id=approval_id,
                status=ApprovalStatus.REJECTED,
                reason="Nonce has already been used (replay detected).",
            )

        # 2. Lookup pending request
        entry = self._pending.get(approval_id)
        if entry is None:
            logger.warning("Approval %s not found in pending store", approval_id)
            return ApprovalResolution(
                approval_id=approval_id,
                status=ApprovalStatus.REJECTED,
                reason="Approval request not found or already resolved.",
            )

        request, stored_invocation = entry

        # 3. Verify status is still PENDING
        if request.status != ApprovalStatus.PENDING:
            logger.warning("Approval %s already resolved: %s", approval_id, request.status)
            return ApprovalResolution(
                approval_id=approval_id,
                status=request.status,
                reason=f"Approval already resolved as {request.status}.",
            )

        # 4. Verify nonce matches
        if not secrets.compare_digest(nonce, request.nonce):
            logger.warning("Invalid nonce for approval %s", approval_id)
            return ApprovalResolution(
                approval_id=approval_id,
                status=ApprovalStatus.REJECTED,
                reason="Invalid nonce.",
            )

        # 5. Check monotonic expiration
        if self._clock() >= request.monotonic_deadline:
            self._transition(approval_id, ApprovalStatus.EXPIRED)
            self._record_event(
                "tool.approval_expired",
                approval_id,
                {
                    "tool_id": request.tool_id,
                    "execution_id": request.execution_id,
                },
            )
            return ApprovalResolution(
                approval_id=approval_id,
                status=ApprovalStatus.EXPIRED,
                reason="Approval has expired.",
            )

        # 6. Check invocation binding if invocation is provided
        if invocation is not None:
            provided_hash = _canonical_invocation_hash(invocation)
            if not secrets.compare_digest(request.invocation_hash, provided_hash):
                logger.warning("Altered invocation detected for approval %s", approval_id)
                self._burned_nonces.add(nonce)
                self._transition(approval_id, ApprovalStatus.REJECTED)
                self._record_event(
                    "tool.denied",
                    approval_id,
                    {
                        "tool_id": request.tool_id,
                        "execution_id": request.execution_id,
                        "client_request_id": request.client_request_id,
                        "reason": "altered_arguments",
                    },
                )
                return ApprovalResolution(
                    approval_id=approval_id,
                    status=ApprovalStatus.REJECTED,
                    reason="Invocation arguments do not match the approved request (tampering detected).",
                )

        # 7. Burn nonce (single-use guarantee)
        self._burned_nonces.add(nonce)

        # 8. Apply decision
        if approved:
            self._transition(approval_id, ApprovalStatus.APPROVED)
            self._record_event(
                "tool.approved",
                approval_id,
                {
                    "tool_id": request.tool_id,
                    "execution_id": request.execution_id,
                    "client_request_id": request.client_request_id,
                },
            )
            logger.info("Approval %s APPROVED for tool '%s'", approval_id, request.tool_id)
            return ApprovalResolution(
                approval_id=approval_id,
                status=ApprovalStatus.APPROVED,
                invocation=stored_invocation,
                reason="Approved by user.",
            )
        else:
            self._transition(approval_id, ApprovalStatus.REJECTED)
            self._record_event(
                "tool.denied",
                approval_id,
                {
                    "tool_id": request.tool_id,
                    "execution_id": request.execution_id,
                    "client_request_id": request.client_request_id,
                },
            )
            logger.info("Approval %s REJECTED for tool '%s'", approval_id, request.tool_id)
            return ApprovalResolution(
                approval_id=approval_id,
                status=ApprovalStatus.REJECTED,
                reason="Rejected by user.",
            )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel_for_request(self, client_request_id: str) -> list[str]:
        """Cancel all pending approvals associated with a client request ID.

        Returns the list of cancelled approval IDs.
        """
        cancelled: list[str] = []
        for approval_id, (request, _) in list(self._pending.items()):
            if (
                request.client_request_id == client_request_id
                and request.status == ApprovalStatus.PENDING
            ):
                self._transition(approval_id, ApprovalStatus.CANCELLED)
                self._record_event(
                    "tool.approval_cancelled",
                    approval_id,
                    {
                        "tool_id": request.tool_id,
                        "execution_id": request.execution_id,
                        "client_request_id": client_request_id,
                        "reason": "parent_request_cancelled",
                    },
                )
                cancelled.append(approval_id)
        return cancelled

    def cancel_all(self) -> list[str]:
        """Cancel all pending approvals (e.g. during application shutdown).

        Returns the list of cancelled approval IDs.
        """
        cancelled: list[str] = []
        for approval_id, (request, _) in list(self._pending.items()):
            if request.status == ApprovalStatus.PENDING:
                self._transition(approval_id, ApprovalStatus.CANCELLED)
                self._record_event(
                    "tool.approval_cancelled",
                    approval_id,
                    {
                        "tool_id": request.tool_id,
                        "execution_id": request.execution_id,
                        "reason": "shutdown",
                    },
                )
                cancelled.append(approval_id)
        return cancelled

    # ------------------------------------------------------------------
    # Expiration sweep
    # ------------------------------------------------------------------

    def check_expirations(self) -> list[str]:
        """Transition any expired pending approvals and return their IDs."""
        expired: list[str] = []
        now = self._clock()
        for approval_id, (request, _) in list(self._pending.items()):
            if request.status == ApprovalStatus.PENDING and now >= request.monotonic_deadline:
                self._transition(approval_id, ApprovalStatus.EXPIRED)
                self._record_event(
                    "tool.approval_expired",
                    approval_id,
                    {
                        "tool_id": request.tool_id,
                        "execution_id": request.execution_id,
                    },
                )
                expired.append(approval_id)
        return expired

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_pending(self, approval_id: str) -> ApprovalRequest | None:
        """Return the approval request if it exists, regardless of status."""
        entry = self._pending.get(approval_id)
        return entry[0] if entry is not None else None

    def list_pending(self) -> list[ApprovalRequest]:
        """Return all approval requests that are still PENDING."""
        return [req for req, _ in self._pending.values() if req.status == ApprovalStatus.PENDING]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _transition(self, approval_id: str, new_status: ApprovalStatus) -> None:
        """Transition a pending request to a terminal status."""
        entry = self._pending.get(approval_id)
        if entry is None:
            return
        old_request, invocation = entry
        updated = old_request.model_copy(update={"status": new_status})
        self._pending[approval_id] = (updated, invocation)

    def _record_event(
        self,
        event_type: str,
        approval_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Dispatch an audit event if a recorder is configured."""
        if self._event_recorder is not None:
            try:
                self._event_recorder(event_type, "tool_approval", approval_id, payload)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to record audit event %s for %s", event_type, approval_id)
