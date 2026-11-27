"""Runtime CapabilityRegistry reflecting actual initialized ORION subsystems."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapabilityStatus:
    """Authoritative status of a single runtime capability."""

    key: str
    label: str
    available: bool
    detail: str


class CapabilityRegistry:
    """Inspects live ORION services to report real runtime capabilities."""

    def __init__(
        self,
        *,
        model_provider: Any = None,
        knowledge_service: Any = None,
        workspace_enabled: bool = False,
        task_service: Any = None,
        habit_service: Any = None,
        voice_service: Any = None,
        web_search_service: Any = None,
        memory_service: Any = None,
    ) -> None:
        self._model_provider = model_provider
        self._knowledge_service = knowledge_service
        self._workspace_enabled = workspace_enabled
        self._task_service = task_service
        self._habit_service = habit_service
        self._voice_service = voice_service
        self._web_search_service = web_search_service
        self._memory_service = memory_service

    def list_capabilities(self) -> list[CapabilityStatus]:
        chat_ok = self._model_provider is not None
        think_ok = bool(
            self._model_provider is not None
            and hasattr(self._model_provider, "supports_thinking")
            and self._model_provider.supports_thinking()
        )
        kb_ok = self._knowledge_service is not None
        ws_ok = bool(self._workspace_enabled)
        prod_ok = self._task_service is not None and self._habit_service is not None
        mem_ok = self._memory_service is not None

        stt_ok = False
        tts_ok = False
        voice_detail_stt = "Offline speech-to-text is not initialized."
        voice_detail_tts = "Offline text-to-speech is not initialized."
        if self._voice_service is not None:
            st = self._voice_service.get_asset_status()
            en = bool(self._voice_service.settings.enabled)
            stt_ok = en and st.stt_ready and st.mic_available
            tts_ok = en and st.tts_ready
            if not en:
                voice_detail_stt = "Voice is disabled in Settings."
                voice_detail_tts = "Voice is disabled in Settings."
            else:
                voice_detail_stt = (
                    "Available offline via local whisper.cpp."
                    if stt_ok
                    else (st.fix_hint or "STT model or microphone unavailable.")
                )
                voice_detail_tts = (
                    "Available offline via local Piper TTS."
                    if tts_ok
                    else (st.fix_hint or "TTS voice model unavailable.")
                )

        web_ok = bool(
            self._web_search_service is not None and self._web_search_service.is_available()
        )
        web_detail = (
            "Available via isolated local Chrome/Playwright browser search (no paid APIs)."
            if web_ok
            else "Browser web search is currently unavailable."
        )

        return [
            CapabilityStatus(
                key="chat",
                label="Local Chat",
                available=chat_ok,
                detail="Available via local Ollama model."
                if chat_ok
                else "Model provider unavailable.",
            ),
            CapabilityStatus(
                key="thinking",
                label="Thinking Mode",
                available=think_ok,
                detail="Supported (optional, off by default)."
                if think_ok
                else "Not supported by active model.",
            ),
            CapabilityStatus(
                key="knowledge",
                label="Local Knowledge Base (RAG)",
                available=kb_ok,
                detail="Available (PDF, EPUB, DOCX, Markdown, text with FTS5 citations)."
                if kb_ok
                else "Knowledge Base unavailable.",
            ),
            CapabilityStatus(
                key="productivity",
                label="Tasks & Habits",
                available=prod_ok,
                detail="Available (natural-language tasks, habits, Today overview, streaks)."
                if prod_ok
                else "Productivity services unavailable.",
            ),
            CapabilityStatus(
                key="memory",
                label="Personal Memory & Profile",
                available=mem_ok,
                detail="Available (local reviewable profile, projects, goals, and facts)."
                if mem_ok
                else "Personal memory unavailable.",
            ),
            CapabilityStatus(
                key="workspace",
                label="Workspace Browser",
                available=ws_ok,
                detail="Available (read-only confined workspace inspection)."
                if ws_ok
                else "Workspace browser unavailable.",
            ),
            CapabilityStatus(
                key="voice_stt",
                label="Voice Input (Offline STT)",
                available=stt_ok,
                detail=voice_detail_stt,
            ),
            CapabilityStatus(
                key="voice_tts",
                label="Voice Output (Offline TTS)",
                available=tts_ok,
                detail=voice_detail_tts,
            ),
            CapabilityStatus(
                key="web_search",
                label="Public Web Search",
                available=web_ok,
                detail=web_detail,
            ),
        ]
