"""Deterministic, zero-LLM follow-up query context resolver for local RAG."""

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")
_PRONOUN_OR_ELLIPSIS_RE = re.compile(
    r"\b(it|its|they|them|their|this|that|these|those|he|him|his|she|her|"
    r"former|latter|above|previous|second|third|first|example|disadvantages?|"
    r"advantages?|pros|cons|more|elaborate|clarify|why)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_ONLY_RE = re.compile(
    r"^\s*(?:explain|describe|clarify|elaborate\s+on|what\s+is|tell\s+me\s+about)?\s*"
    r"(?:the\s+(?:first|second|third|last|previous|next)\s+(?:point|item|one|part)|"
    r"it|this|that|why|how|give\s+(?:me\s+)?an?\s+example)\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_ANCHOR_STOPWORDS: frozenset[str] = frozenset(
    {
        "what",
        "which",
        "who",
        "when",
        "where",
        "why",
        "how",
        "is",
        "are",
        "was",
        "were",
        "be",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "on",
        "to",
        "for",
        "with",
        "from",
        "about",
        "explain",
        "describe",
        "summarize",
        "tell",
        "me",
        "please",
        "give",
        "example",
        "its",
        "it",
        "their",
        "they",
        "this",
        "that",
    }
)


class ResolvedQueryContext(BaseModel):
    """Result of deterministic follow-up query resolution."""

    model_config = ConfigDict(frozen=True)

    resolved_query: str
    is_follow_up: bool = False
    needs_clarification: bool = False
    clarification_message: str | None = None
    topic_anchor: str | None = None


class KnowledgeQueryContextResolver:
    """Resolve elliptical or pronoun-based follow-up questions using recent USER turns.

    Never invokes an LLM and never uses assistant answers as authoritative evidence.
    """

    def resolve(
        self,
        current_question: str,
        recent_user_messages: Sequence[str] = (),
        last_knowledge_question: str | None = None,
    ) -> ResolvedQueryContext:
        q = current_question.strip()
        if not q:
            return ResolvedQueryContext(
                resolved_query="",
                needs_clarification=True,
                clarification_message="Please enter a question.",
            )

        # Find most recent prior user question (excluding current_question if already in list)
        prior_candidates = [
            m.strip() for m in recent_user_messages if m and m.strip() and m.strip() != q
        ]
        prior_q = (prior_candidates[-1] if prior_candidates else None) or (
            last_knowledge_question.strip()
            if last_knowledge_question and last_knowledge_question.strip() != q
            else None
        )

        is_short_or_pronoun = bool(_PRONOUN_OR_ELLIPSIS_RE.search(q)) and len(q.split()) <= 14

        # Ambiguous bare reference with no prior user topic
        if _AMBIGUOUS_ONLY_RE.match(q) and not prior_q:
            return ResolvedQueryContext(
                resolved_query=q,
                is_follow_up=True,
                needs_clarification=True,
                clarification_message=(
                    "Your follow-up question refers to a prior topic or point, but there is no "
                    "previous question in this conversation. Please specify the topic or document section."
                ),
            )

        if not is_short_or_pronoun or not prior_q:
            return ResolvedQueryContext(resolved_query=q, is_follow_up=False)

        # Extract meaningful topic anchor tokens from prior user question (max 6)
        anchor_terms: list[str] = []
        seen: set[str] = set()
        current_low = {w.lower() for w in _WORD_RE.findall(q)}
        for w in _WORD_RE.findall(prior_q):
            low = w.lower()
            if low in _ANCHOR_STOPWORDS or low in current_low or low in seen:
                continue
            seen.add(low)
            anchor_terms.append(w)
            if len(anchor_terms) >= 6:
                break

        if not anchor_terms:
            return ResolvedQueryContext(resolved_query=q, is_follow_up=False)

        anchor = " ".join(anchor_terms)
        return ResolvedQueryContext(
            resolved_query=f"{anchor} {q}",
            is_follow_up=True,
            topic_anchor=anchor,
        )
