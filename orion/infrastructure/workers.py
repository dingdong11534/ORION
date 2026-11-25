from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    """Thread-safe signals emitted during background worker execution."""

    started = Signal()
    finished = Signal()
    result = Signal(object)
    error = Signal(str)
    chunk = Signal(str, str)  # (task_id, chunk_text)
    thinking = Signal(str, str)  # (task_id, thinking_text)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class Worker(QRunnable):
    """QRunnable wrapper to execute a callable off the main GUI thread.

    setAutoDelete is set to False by default so that object lifetime can be
    managed deterministically by OrionBridge without premature C++ deletion.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        signals: WorkerSignals | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = signals if signals is not None else WorkerSignals()
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        """Execute the target function and emit appropriate signals."""
        try:
            self.signals.started.emit()
            res = self.fn(*self.args, **self.kwargs)
            self.signals.result.emit(res)
        except Exception as exc:  # noqa: BLE001 - generic thread worker captures all errors for Qt signal emission
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()
