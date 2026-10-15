"""Thin wrapper around the Anthropic Messages API used by triage and drafting.

Everything that can go wrong with a model call (refusal, truncation, no text, network)
raises LLMError, so callers can degrade to the rule-based path instead of failing a ticket.
"""
import logging
import os

log = logging.getLogger("support_copilot")

DEFAULT_MODEL = "claude-opus-5-5"  # override with TRIAGE_MODEL, e.g. claude-haiku-5-5 for cheaper bulk triage


class LLMError(Exception):
    """The model call did not produce a usable answer."""


def enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def model_name() -> str:
    return os.getenv("TRIAGE_MODEL", DEFAULT_MODEL)


def complete(system: str, user: str, max_tokens: int = 2048) -> str:
    """Return the first text block of the reply.

    Thinking is always on for current models and its tokens count against max_tokens, so the budget is
    generous and effort is low (these are short, bounded tasks). Reading the first *text* block, not
    content[0], matters because a thinking block can come first.
    """
    import anthropic  # imported lazily so rules mode needs neither the key nor the SDK

    # A webhook handler must not hang for the SDK's 10-minute default.
    client = anthropic.Anthropic(timeout=20.0, max_retries=1)
    msg = client.messages.create(
        model=model_name(),
        max_tokens=max_tokens,
        system=system,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": user}],
    )
    if msg.stop_reason == "refusal":
        category = getattr(getattr(msg, "stop_details", None), "category", None)
        raise LLMError(f"model refused ({category or 'no category'})")
    if msg.stop_reason == "max_tokens":
        raise LLMError("reply truncated at max_tokens")
    text = next((b.text for b in msg.content if b.type == "text" and b.text), None)
    if not text:
        raise LLMError("reply had no text block")
    return text
