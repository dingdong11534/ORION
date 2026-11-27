"""Deterministic ActionPolicy and ProductivityActionCoordinator."""

from typing import Any

from orion.application.productivity.contracts import (
    ActionRiskLevel,
    HabitFrequency,
    ProductivityActionResult,
    ProductivityIntent,
    ProductivityProposal,
    TargetResolutionStatus,
    TaskFilter,
    TaskPriority,
)
from orion.application.productivity.date_parser import parse_natural_date
from orion.application.productivity.habit_service import HabitService
from orion.application.productivity.ports import ClockPort, ProductivityActionStorePort
from orion.application.productivity.productivity_context import (
    ProductivityContextPort,
    ProductivityContextStore,
)
from orion.application.productivity.productivity_intent import ProductivityIntentInterpreter
from orion.application.productivity.target_resolver import (
    resolve_habit_target,
    resolve_task_target,
)
from orion.application.productivity.task_service import TaskService
from orion.application.productivity.today_query import (
    build_today_overview,
    format_today_chat_summary,
)

_LEVEL_3_INTENTS = {
    ProductivityIntent.TASK_ARCHIVE,
    ProductivityIntent.HABIT_ARCHIVE,
    ProductivityIntent.HABIT_UNDO_COMPLETION,
}


class ProductivityActionCoordinator:
    """Enforces deterministic ActionPolicy, target resolution, date validation, and persistence."""

    def __init__(
        self,
        *,
        task_service: TaskService,
        habit_service: HabitService,
        clock: ClockPort,
        action_store: ProductivityActionStorePort | None = None,
        interpreter: ProductivityIntentInterpreter | None = None,
        context_store: ProductivityContextPort | None = None,
    ) -> None:
        self._tasks = task_service
        self._habits = habit_service
        self._clock = clock
        self._actions = action_store
        self._interpreter = interpreter or ProductivityIntentInterpreter()
        self._ctx: ProductivityContextPort = context_store or ProductivityContextStore()

    def handle_message(
        self,
        message: str,
        *,
        conversation_id: str,
        client_request_id: str | None = None,
        explicit_confirm: bool = False,
    ) -> ProductivityActionResult | None:
        """Inspect message; return ProductivityActionResult if handled, or None for normal chat."""
        self._ctx.advance_user_turn(conversation_id)
        proposal = self._interpreter.interpret(message)
        if proposal.intent == ProductivityIntent.NONE:
            return None

        # Idempotency check for retried requests
        if client_request_id and self._actions is not None:
            prev = self._actions.get_by_request(client_request_id, proposal.intent.value)
            if prev is not None:
                return ProductivityActionResult(
                    action_id=prev["id"],
                    intent=ProductivityIntent(prev["intent"]),
                    risk_level=ActionRiskLevel(prev["risk_level"]),
                    status=prev["status"],
                    entity_type=prev["entity_type"],
                    entity_id=prev["entity_id"],
                    summary=prev["summary"],
                    undo_supported=prev["undo_payload"] is not None,
                )

        return self.execute_proposal(
            proposal,
            conversation_id=conversation_id,
            client_request_id=client_request_id,
            explicit_confirm=explicit_confirm,
        )

    def execute_proposal(
        self,
        proposal: ProductivityProposal,
        *,
        conversation_id: str,
        client_request_id: str | None = None,
        explicit_confirm: bool = False,
    ) -> ProductivityActionResult:
        intent = proposal.intent
        risk = ActionRiskLevel.LEVEL_3 if intent in _LEVEL_3_INTENTS else ActionRiskLevel.LEVEL_1

        # 1) READ-ONLY QUERIES (TASK_LIST, HABIT_LIST, HABIT_STATISTICS)
        if intent == ProductivityIntent.TASK_LIST:
            bucket = proposal.task_bucket
            if bucket == "today":
                overview = build_today_overview(self._tasks, self._habits, self._clock)
                summary = format_today_chat_summary(overview, self._clock.timezone)
                shown_ids = [
                    t.id
                    for t in (overview.overdue + overview.due_today + overview.unscheduled)[:10]
                ]
                if shown_ids:
                    self._ctx.set_last_list(conversation_id, "task", shown_ids)
                return ProductivityActionResult(
                    intent=intent,
                    risk_level=ActionRiskLevel.LEVEL_1,
                    status="executed",
                    summary=summary,
                )
            rows = self._tasks.list_tasks(TaskFilter(bucket=bucket))
            if not rows:
                label = f" for {bucket}" if bucket else ""
                summary = f"You have no active tasks{label}."
            else:
                self._ctx.set_last_list(conversation_id, "task", [t.id for t in rows[:10]])
                lines = []
                for idx, t in enumerate(rows[:10], start=1):
                    due_s = (
                        f" · Due {t.due_at.astimezone(self._clock.timezone).strftime('%a, %b %-d')}"
                        if t.due_at
                        else ""
                    )
                    lines.append(
                        f"{idx}. [{t.priority.value.upper()}] {t.title} ({t.status.value}){due_s}"
                    )
                header = f"Tasks ({bucket}):" if bucket else "Active tasks:"
                summary = header + "\n" + "\n".join(lines)
            return ProductivityActionResult(
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_1,
                status="executed",
                summary=summary,
            )

        if intent == ProductivityIntent.HABIT_LIST:
            habits = self._habits.list_habits()
            if not habits:
                return ProductivityActionResult(
                    intent=intent,
                    risk_level=ActionRiskLevel.LEVEL_1,
                    status="executed",
                    summary="You have no active habits yet.",
                )
            self._ctx.set_last_list(conversation_id, "habit", [h.id for h in habits[:10]])
            lines = []
            for idx, h in enumerate(habits, start=1):
                st = self._habits.get_habit_statistics(h.id)
                done_mark = "Done today" if st.completed_today else "Pending today"
                lines.append(
                    f"{idx}. {h.name} ({h.frequency.value}, target {h.target_count}) · Streak: {st.current_streak} · {done_mark}"
                )
            return ProductivityActionResult(
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_1,
                status="executed",
                summary="Your habits:\n" + "\n".join(lines),
            )

        if intent == ProductivityIntent.HABIT_STATISTICS:
            habits = self._habits.list_habits()
            if proposal.target_query:
                res = resolve_habit_target(proposal.target_query, habits)
                if res.status != TargetResolutionStatus.MATCHED or not res.matched_id:
                    return ProductivityActionResult(
                        intent=intent,
                        risk_level=ActionRiskLevel.LEVEL_2,
                        status="clarify",
                        entity_type="habit",
                        candidates=res.candidates,
                        summary=res.message,
                    )
                targets = [self._habits.get_habit(res.matched_id)]
            else:
                targets = list(habits)
            targets = [h for h in targets if h is not None]
            if not targets:
                return ProductivityActionResult(
                    intent=intent,
                    risk_level=ActionRiskLevel.LEVEL_1,
                    status="executed",
                    summary="No active habits found to compute statistics.",
                )
            lines = []
            for h in targets:
                st = self._habits.get_habit_statistics(h.id)
                lines.append(
                    f"- {h.name}: current streak {st.current_streak}, longest {st.longest_streak}, "
                    f"this week {st.weekly_completed}/{st.weekly_target}, "
                    f"this month {st.monthly_completed}/{st.monthly_target}, "
                    f"rate {round(st.completion_rate * 100)}%"
                )
            return ProductivityActionResult(
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_1,
                status="executed",
                summary="Habit statistics:\n" + "\n".join(lines),
            )

        # 2) TASK CREATE
        if intent == ProductivityIntent.TASK_CREATE:
            title = (proposal.title or "").strip()
            if not title:
                return ProductivityActionResult(
                    intent=intent,
                    risk_level=ActionRiskLevel.LEVEL_2,
                    status="clarify",
                    summary="What should the task title be?",
                )
            title = title[0].upper() + title[1:]
            due_at = None
            due_is_date_only = True
            due_display = ""
            if proposal.due_expression:
                parsed_d = parse_natural_date(proposal.due_expression, self._clock)
                if parsed_d.ambiguous or parsed_d.resolved_at is None:
                    return ProductivityActionResult(
                        intent=intent,
                        risk_level=ActionRiskLevel.LEVEL_2,
                        status="clarify",
                        summary=parsed_d.error or "Please clarify the due date.",
                    )
                due_at = parsed_d.resolved_at
                due_is_date_only = parsed_d.is_date_only
                due_display = parsed_d.display_text

            pr = proposal.priority or TaskPriority.MEDIUM
            created_task = self._tasks.create_task(
                title,
                description=proposal.description,
                priority=pr,
                due_at=due_at,
                due_is_date_only=due_is_date_only,
            )
            task = self._tasks.get_task(created_task.id) or created_task
            self._ctx.set_last_entity(conversation_id, "task", task.id, task.title)
            due_part = f" Due {due_display}." if due_display else ""
            summary = f"Created task: {task.title}.{due_part} Priority: {task.priority.value.capitalize()}."
            changed = {"title": task.title, "priority": task.priority.value}
            if due_display:
                changed["due"] = due_display
            action_id = self._persist_action(
                client_request_id=client_request_id,
                conversation_id=conversation_id,
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_1,
                status="executed",
                entity_type="task",
                entity_id=task.id,
                summary=summary,
                payload=changed,
                undo_payload={"type": "archive_task", "task_id": task.id},
            )
            return ProductivityActionResult(
                action_id=action_id,
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_1,
                status="executed",
                entity_type="task",
                entity_id=task.id,
                entity_label=task.title,
                summary=summary,
                changed_fields=changed,
                undo_supported=True,
            )

        # 3) HABIT CREATE
        if intent == ProductivityIntent.HABIT_CREATE:
            name = (proposal.title or "").strip()
            if not name:
                return ProductivityActionResult(
                    intent=intent,
                    risk_level=ActionRiskLevel.LEVEL_2,
                    status="clarify",
                    summary="What habit would you like to create?",
                )
            freq = proposal.frequency or HabitFrequency.DAILY
            tc = proposal.target_count or 1
            created_habit = self._habits.create_habit(name, frequency=freq, target_count=tc)
            habit = self._habits.get_habit(created_habit.id) or created_habit
            self._ctx.set_last_entity(conversation_id, "habit", habit.id, habit.name)
            summary = f"Created habit: {habit.name} ({habit.frequency.value}, target {habit.target_count})."
            changed = {
                "name": habit.name,
                "frequency": habit.frequency.value,
                "target_count": habit.target_count,
            }
            action_id = self._persist_action(
                client_request_id=client_request_id,
                conversation_id=conversation_id,
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_1,
                status="executed",
                entity_type="habit",
                entity_id=habit.id,
                summary=summary,
                payload=changed,
                undo_payload={"type": "archive_habit", "habit_id": habit.id},
            )
            return ProductivityActionResult(
                action_id=action_id,
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_1,
                status="executed",
                entity_type="habit",
                entity_id=habit.id,
                entity_label=habit.name,
                summary=summary,
                changed_fields=changed,
                undo_supported=True,
            )

        # 4) TASK MUTATIONS (UPDATE / COMPLETE / REOPEN / CANCEL / ARCHIVE)
        if intent in (
            ProductivityIntent.TASK_UPDATE,
            ProductivityIntent.TASK_COMPLETE,
            ProductivityIntent.TASK_REOPEN,
            ProductivityIntent.TASK_CANCEL,
            ProductivityIntent.TASK_ARCHIVE,
        ):
            target_id, target_label, clarify_res = self._resolve_task_or_ctx(
                conversation_id, proposal
            )
            if clarify_res is not None:
                return clarify_res
            assert target_id is not None and target_label is not None
            before = self._tasks.get_task(target_id)
            assert before is not None

            if risk == ActionRiskLevel.LEVEL_3 and not explicit_confirm:
                summary = f"Confirm archiving task '{before.title}'?"
                aid = self._persist_action(
                    client_request_id=client_request_id,
                    conversation_id=conversation_id,
                    intent=intent,
                    risk_level=ActionRiskLevel.LEVEL_3,
                    status="pending_confirmation",
                    entity_type="task",
                    entity_id=before.id,
                    summary=summary,
                    payload={"task_id": before.id},
                    undo_payload=None,
                )
                return ProductivityActionResult(
                    action_id=aid,
                    intent=intent,
                    risk_level=ActionRiskLevel.LEVEL_3,
                    status="pending_confirmation",
                    entity_type="task",
                    entity_id=before.id,
                    entity_label=before.title,
                    summary=summary,
                )

            if intent == ProductivityIntent.TASK_COMPLETE:
                updated = self._tasks.complete_task(target_id)
                summary = f"Completed task: {updated.title}."
                changed: dict[str, Any] = {"status": "done"}
                undo = {
                    "type": "restore_task_status",
                    "task_id": updated.id,
                    "status": before.status.value,
                }
            elif intent == ProductivityIntent.TASK_REOPEN:
                updated = self._tasks.reopen_task(target_id)
                summary = f"Reopened task: {updated.title}."
                changed = {"status": "todo"}
                undo = {
                    "type": "restore_task_status",
                    "task_id": updated.id,
                    "status": before.status.value,
                }
            elif intent == ProductivityIntent.TASK_CANCEL:
                updated = self._tasks.cancel_task(target_id)
                summary = f"Cancelled task: {updated.title}."
                changed = {"status": "cancelled"}
                undo = {
                    "type": "restore_task_status",
                    "task_id": updated.id,
                    "status": before.status.value,
                }
            elif intent == ProductivityIntent.TASK_ARCHIVE:
                updated = self._tasks.archive_task(target_id)
                summary = f"Archived task: {updated.title}."
                changed = {"archived": True}
                undo = None
            else:
                kwargs: dict[str, Any] = {}
                changed = {}
                parts = []
                if proposal.priority is not None:
                    kwargs["priority"] = proposal.priority
                    changed["priority"] = proposal.priority.value
                    parts.append(f"priority is now {proposal.priority.value.capitalize()}")
                if proposal.due_expression:
                    parsed_d = parse_natural_date(proposal.due_expression, self._clock)
                    if parsed_d.ambiguous or parsed_d.resolved_at is None:
                        return ProductivityActionResult(
                            intent=intent,
                            risk_level=ActionRiskLevel.LEVEL_2,
                            status="clarify",
                            summary=parsed_d.error or "Please clarify the new due date.",
                        )
                    kwargs["due_at"] = parsed_d.resolved_at
                    kwargs["due_is_date_only"] = parsed_d.is_date_only
                    changed["due"] = parsed_d.display_text
                    parts.append(f"due {parsed_d.display_text}")
                updated = self._tasks.update_task(target_id, **kwargs)
                summary = f"Updated {updated.title}: {', '.join(parts)}."
                undo = {
                    "type": "restore_task_fields",
                    "task_id": updated.id,
                    "priority": before.priority.value,
                    "due_at": before.due_at.isoformat() if before.due_at else None,
                    "due_is_date_only": before.due_is_date_only,
                }

            updated = self._tasks.get_task(updated.id) or updated
            self._ctx.set_last_entity(conversation_id, "task", updated.id, updated.title)
            aid = self._persist_action(
                client_request_id=client_request_id,
                conversation_id=conversation_id,
                intent=intent,
                risk_level=risk,
                status="executed",
                entity_type="task",
                entity_id=updated.id,
                summary=summary,
                payload=changed,
                undo_payload=undo,
            )
            return ProductivityActionResult(
                action_id=aid,
                intent=intent,
                risk_level=risk,
                status="executed",
                entity_type="task",
                entity_id=updated.id,
                entity_label=updated.title,
                summary=summary,
                changed_fields=changed,
                undo_supported=undo is not None,
            )

        # 5) HABIT MUTATIONS (COMPLETE / UNDO / UPDATE / ARCHIVE)
        target_id, target_label, clarify_res = self._resolve_habit_or_ctx(conversation_id, proposal)
        if clarify_res is not None:
            return clarify_res
        assert target_id is not None and target_label is not None
        habit = self._habits.get_habit(target_id)
        assert habit is not None

        if intent == ProductivityIntent.HABIT_COMPLETE:
            try:
                comp = self._habits.complete_habit(habit.id)
            except ValueError as exc:
                return ProductivityActionResult(
                    intent=intent,
                    risk_level=ActionRiskLevel.LEVEL_1,
                    status="failed",
                    entity_type="habit",
                    entity_id=habit.id,
                    entity_label=habit.name,
                    summary=str(exc),
                    error=str(exc),
                )
            self._ctx.set_last_entity(conversation_id, "habit", habit.id, habit.name)
            summary = f"Recorded today's {habit.name} habit completion ({comp.completed_date.isoformat()})."
            undo = {
                "type": "undo_habit_completion",
                "habit_id": habit.id,
                "completed_date": comp.completed_date.isoformat(),
            }
            aid = self._persist_action(
                client_request_id=client_request_id,
                conversation_id=conversation_id,
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_1,
                status="executed",
                entity_type="habit",
                entity_id=habit.id,
                summary=summary,
                payload={"completed_date": comp.completed_date.isoformat()},
                undo_payload=undo,
            )
            return ProductivityActionResult(
                action_id=aid,
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_1,
                status="executed",
                entity_type="habit",
                entity_id=habit.id,
                entity_label=habit.name,
                summary=summary,
                changed_fields={"completed_date": comp.completed_date.isoformat()},
                undo_supported=True,
            )

        if intent == ProductivityIntent.HABIT_UNDO_COMPLETION:
            removed = self._habits.undo_completion(habit.id, completed_date=self._clock.today())
            if removed is None:
                # Check if there is a historical completion that requires Level 3 confirmation
                hist = self._habits.get_habit_history(habit.id, limit=1)
                if not hist:
                    return ProductivityActionResult(
                        intent=intent,
                        risk_level=ActionRiskLevel.LEVEL_1,
                        status="failed",
                        entity_type="habit",
                        entity_id=habit.id,
                        entity_label=habit.name,
                        summary=f"No completion found for '{habit.name}' to undo.",
                        error="No completion found.",
                    )
                if not explicit_confirm:
                    latest_d = hist[0].completed_date.isoformat()
                    summary = (
                        f"Confirm undoing historical completion for '{habit.name}' on {latest_d}?"
                    )
                    aid = self._persist_action(
                        client_request_id=client_request_id,
                        conversation_id=conversation_id,
                        intent=intent,
                        risk_level=ActionRiskLevel.LEVEL_3,
                        status="pending_confirmation",
                        entity_type="habit",
                        entity_id=habit.id,
                        summary=summary,
                        payload={"habit_id": habit.id, "completed_date": latest_d},
                        undo_payload=None,
                    )
                    return ProductivityActionResult(
                        action_id=aid,
                        intent=intent,
                        risk_level=ActionRiskLevel.LEVEL_3,
                        status="pending_confirmation",
                        entity_type="habit",
                        entity_id=habit.id,
                        entity_label=habit.name,
                        summary=summary,
                    )
                removed = self._habits.undo_completion(
                    habit.id, completed_date=hist[0].completed_date
                )

            assert removed is not None
            summary = f"Undid {habit.name} completion for {removed.completed_date.isoformat()}."
            return ProductivityActionResult(
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_1,
                status="executed",
                entity_type="habit",
                entity_id=habit.id,
                entity_label=habit.name,
                summary=summary,
                changed_fields={"removed_date": removed.completed_date.isoformat()},
            )

        if intent == ProductivityIntent.HABIT_ARCHIVE:
            if not explicit_confirm:
                summary = f"Confirm archiving habit '{habit.name}'? History will be preserved."
                aid = self._persist_action(
                    client_request_id=client_request_id,
                    conversation_id=conversation_id,
                    intent=intent,
                    risk_level=ActionRiskLevel.LEVEL_3,
                    status="pending_confirmation",
                    entity_type="habit",
                    entity_id=habit.id,
                    summary=summary,
                    payload={"habit_id": habit.id},
                    undo_payload=None,
                )
                return ProductivityActionResult(
                    action_id=aid,
                    intent=intent,
                    risk_level=ActionRiskLevel.LEVEL_3,
                    status="pending_confirmation",
                    entity_type="habit",
                    entity_id=habit.id,
                    entity_label=habit.name,
                    summary=summary,
                )
            archived = self._habits.archive_habit(habit.id)
            return ProductivityActionResult(
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_3,
                status="executed",
                entity_type="habit",
                entity_id=archived.id,
                entity_label=archived.name,
                summary=f"Archived habit: {archived.name}.",
            )

        # HABIT_UPDATE
        kwargs_h: dict[str, Any] = {}
        changed_h: dict[str, Any] = {}
        if proposal.frequency is not None:
            kwargs_h["frequency"] = proposal.frequency
            changed_h["frequency"] = proposal.frequency.value
        if proposal.target_count is not None:
            kwargs_h["target_count"] = proposal.target_count
            changed_h["target_count"] = proposal.target_count
        if not kwargs_h:
            return ProductivityActionResult(
                intent=intent,
                risk_level=ActionRiskLevel.LEVEL_2,
                status="clarify",
                summary=f"What would you like to change on habit '{habit.name}'?",
            )
        updated_h = self._habits.update_habit(habit.id, **kwargs_h)
        self._ctx.set_last_entity(conversation_id, "habit", updated_h.id, updated_h.name)
        summary = (
            f"Updated habit {updated_h.name}: {updated_h.frequency.value}, "
            f"target {updated_h.target_count}."
        )
        aid = self._persist_action(
            client_request_id=client_request_id,
            conversation_id=conversation_id,
            intent=intent,
            risk_level=ActionRiskLevel.LEVEL_1,
            status="executed",
            entity_type="habit",
            entity_id=updated_h.id,
            summary=summary,
            payload=changed_h,
            undo_payload={
                "type": "restore_habit_fields",
                "habit_id": updated_h.id,
                "frequency": habit.frequency.value,
                "target_count": habit.target_count,
            },
        )
        return ProductivityActionResult(
            action_id=aid,
            intent=intent,
            risk_level=ActionRiskLevel.LEVEL_1,
            status="executed",
            entity_type="habit",
            entity_id=updated_h.id,
            entity_label=updated_h.name,
            summary=summary,
            changed_fields=changed_h,
            undo_supported=True,
        )

    def undo_action(self, action_id: str) -> ProductivityActionResult:
        """Undo a previously persisted Level 1 action when supported."""
        if self._actions is None:
            raise ValueError("Action store not configured.")
        rec = self._actions.get_record(action_id)
        if rec is None or not rec.get("undo_payload"):
            raise ValueError("This action cannot be undone.")
        u = rec["undo_payload"]
        utype = u["type"]
        if utype == "archive_task":
            t = self._tasks.archive_task(u["task_id"])
            summary = f"Undid task creation (archived '{t.title}')."
        elif utype == "restore_task_status":
            t = self._tasks.update_task(u["task_id"], status=u["status"])
            summary = f"Restored task '{t.title}' status to {t.status.value}."
        elif utype == "restore_task_fields":
            from datetime import datetime as dt_cls

            due = dt_cls.fromisoformat(u["due_at"]) if u.get("due_at") else None
            t = self._tasks.update_task(
                u["task_id"],
                priority=u["priority"],
                due_at=due,
                due_is_date_only=bool(u.get("due_is_date_only", False)),
            )
            summary = f"Restored previous fields on '{t.title}'."
        elif utype == "archive_habit":
            h = self._habits.archive_habit(u["habit_id"])
            summary = f"Undid habit creation (archived '{h.name}')."
        elif utype == "undo_habit_completion":
            from datetime import date as date_cls

            self._habits.undo_completion(
                u["habit_id"], completed_date=date_cls.fromisoformat(u["completed_date"])
            )
            summary = f"Removed habit completion for {u['completed_date']}."
        elif utype == "restore_habit_fields":
            h = self._habits.update_habit(
                u["habit_id"], frequency=u["frequency"], target_count=u["target_count"]
            )
            summary = f"Restored habit '{h.name}' to {h.frequency.value} (target {h.target_count})."
        else:
            raise ValueError(f"Unsupported undo type: {utype}")
        self._actions.update_status(action_id, "undone")
        return ProductivityActionResult(
            action_id=action_id,
            intent=ProductivityIntent(rec["intent"]),
            risk_level=ActionRiskLevel(rec["risk_level"]),
            status="executed",
            entity_type=rec["entity_type"],
            entity_id=rec["entity_id"],
            summary=summary,
        )

    def _resolve_task_or_ctx(
        self,
        conversation_id: str,
        proposal: ProductivityProposal,
    ) -> tuple[str | None, str | None, ProductivityActionResult | None]:
        if proposal.uses_context_reference:
            resolved_id = self._ctx.resolve_reference(
                conversation_id, "task", ordinal_index=proposal.ordinal_index
            )
            if resolved_id:
                t = self._tasks.get_task(resolved_id)
                if t is not None and t.archived_at is None:
                    return t.id, t.title, None
            return (
                None,
                None,
                ProductivityActionResult(
                    intent=proposal.intent,
                    risk_level=ActionRiskLevel.LEVEL_2,
                    status="clarify",
                    entity_type="task",
                    summary="Which task are you referring to?",
                ),
            )
        res = resolve_task_target(proposal.target_query or "", self._tasks.list_tasks())
        if res.status != TargetResolutionStatus.MATCHED or not res.matched_id:
            if res.status == TargetResolutionStatus.AMBIGUOUS and res.candidates:
                self._ctx.set_last_list(
                    conversation_id,
                    "task",
                    [c["id"] for c in res.candidates if c.get("id")],
                )
            return (
                None,
                None,
                ProductivityActionResult(
                    intent=proposal.intent,
                    risk_level=ActionRiskLevel.LEVEL_2,
                    status="clarify",
                    entity_type="task",
                    candidates=res.candidates,
                    summary=res.message,
                ),
            )
        return res.matched_id, res.matched_label, None

    def _resolve_habit_or_ctx(
        self,
        conversation_id: str,
        proposal: ProductivityProposal,
    ) -> tuple[str | None, str | None, ProductivityActionResult | None]:
        if proposal.uses_context_reference:
            resolved_id = self._ctx.resolve_reference(
                conversation_id, "habit", ordinal_index=proposal.ordinal_index
            )
            if resolved_id:
                h = self._habits.get_habit(resolved_id)
                if h is not None and h.archived_at is None:
                    return h.id, h.name, None
            return (
                None,
                None,
                ProductivityActionResult(
                    intent=proposal.intent,
                    risk_level=ActionRiskLevel.LEVEL_2,
                    status="clarify",
                    entity_type="habit",
                    summary="Which habit are you referring to?",
                ),
            )
        res = resolve_habit_target(proposal.target_query or "", self._habits.list_habits())
        if res.status != TargetResolutionStatus.MATCHED or not res.matched_id:
            if res.status == TargetResolutionStatus.AMBIGUOUS and res.candidates:
                self._ctx.set_last_list(
                    conversation_id,
                    "habit",
                    [c["id"] for c in res.candidates if c.get("id")],
                )
            return (
                None,
                None,
                ProductivityActionResult(
                    intent=proposal.intent,
                    risk_level=ActionRiskLevel.LEVEL_2,
                    status="clarify",
                    entity_type="habit",
                    candidates=res.candidates,
                    summary=res.message,
                ),
            )
        return res.matched_id, res.matched_label, None

    def _persist_action(
        self,
        *,
        client_request_id: str | None,
        conversation_id: str | None,
        intent: ProductivityIntent,
        risk_level: ActionRiskLevel,
        status: str,
        entity_type: str | None,
        entity_id: str | None,
        summary: str,
        payload: dict[str, Any],
        undo_payload: dict[str, Any] | None,
    ) -> str | None:
        if self._actions is None:
            return None
        return self._actions.save_record(
            client_request_id=client_request_id,
            conversation_id=conversation_id,
            intent=intent.value,
            risk_level=risk_level.value,
            status=status,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            payload=payload,
            undo_payload=undo_payload,
            now=self._clock.now(),
        )
