"""
llm/router.py
LiteLLM provider router with automatic fallback.

Default priority: Gemini 2.5 Flash → Groq Llama 3.3 → Claude Sonnet 4.6 → GPT-4o Mini → Mistral → Cohere
Override preferred provider with ZETA_PREFERRED_PROVIDER env var.
All LLM calls in the agent engine go through call_llm().
"""

from __future__ import annotations
import os
import json
import logging
from pathlib import Path
from typing import Any

# Load .env before anything else
try:
    from dotenv import load_dotenv
    # Walk up from this file to find .env (works in any working directory)
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=_env_path, override=False)
except ImportError:
    pass  # python-dotenv not installed — rely on environment variables being set externally

import litellm
from litellm import completion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider priority chain
# All providers supported by LiteLLM — just needs the right API key in .env
#
# GEMINI NOTE: LiteLLM 1.8x has a bug where gemini/ prefix still routes to
# Vertex AI in some cases. Forcing api_base to AI Studio URL fixes this.
# Model: gemini-2.5-flash (gemini-3 Flash is not yet stable; Gemini 3 Pro
# Preview was shut down March 9 2026).
#
# ANTHROPIC NOTE: Use anthropic/ prefix so LiteLLM doesn't try OpenAI routing.
#
# OPENAI NOTE: gpt-5 is very new — using gpt-4o-mini as the safe fallback.
# If you have gpt-5 access, set ZETA_PREFERRED_PROVIDER=openai and change
# the model string below to "gpt-5".
# ---------------------------------------------------------------------------

PROVIDERS = [
    {
        "name": "gemini",
        "model": "gemini/gemini-2.5-flash",
        "api_key_env": "GEMINI_API_KEY",
        "use_env_key": True,
    },
    {
        "name": "groq",
        "model": "groq/llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
    },
    {
        "name": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    {
        "name": "openai",
        "model": "gpt-4o-mini",   # change to "gpt-5" if you have access
        "api_key_env": "OPENAI_API_KEY",
    },
    {
        "name": "mistral",
        "model": "mistral/mistral-small-latest",
        "api_key_env": "MISTRAL_API_KEY",
    },
    {
        "name": "cohere",
        "model": "command-r",
        "api_key_env": "COHERE_API_KEY",
    },
]

_PROVIDER_NAMES = [p["name"] for p in PROVIDERS]

# Silence verbose litellm logs unless LITELLM_VERBOSE=true in .env
_verbose = os.getenv("LITELLM_VERBOSE", "false").lower() == "true"
litellm.suppress_debug_info = not _verbose


def _get_available_providers() -> list[dict]:
    """
    Return providers that have an API key set, in priority order.
    If ZETA_PREFERRED_PROVIDER is set, that provider moves to the front.
    """
    preferred = os.getenv("ZETA_PREFERRED_PROVIDER", "").lower().strip()

    # Reorder: preferred first, then rest in default order
    ordered = PROVIDERS[:]
    if preferred and preferred in _PROVIDER_NAMES:
        ordered = sorted(
            ordered,
            key=lambda p: (0 if p["name"] == preferred else 1),
        )

    available = [p for p in ordered if os.getenv(p["api_key_env"])]

    if not available:
        logger.warning(
            "No LLM API keys found. Set at least one in your .env file:\n"
            "  GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, "
            "MISTRAL_API_KEY, or COHERE_API_KEY"
        )
    return available


def call_llm(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    json_mode: bool = False,
) -> str:
    """
    Call the LLM with automatic fallback across providers.

    Returns the raw text response string.
    Raises RuntimeError if all providers fail.
    """
    providers = _get_available_providers()

    if not providers:
        # Dev fallback: return a stub so agents can be tested without any key
        logger.warning("No API keys available — returning stub LLM response.")
        return _stub_response(json_mode)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Exception | None = None

    for provider in providers:
        model = provider["model"]
        api_key = os.getenv(provider["api_key_env"])

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if provider.get("use_env_key"):
            # Inject key as env var — fixes LiteLLM 1.8x Vertex AI misrouting
            # for gemini/ prefix models when key is passed as kwarg
            os.environ[provider["api_key_env"]] = api_key or ""
        else:
            kwargs["api_key"] = api_key

        if provider.get("api_base"):
            kwargs["api_base"] = provider["api_base"]

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            logger.debug(f"Calling LLM: {model}")
            response = completion(**kwargs)
            text = response.choices[0].message.content or ""
            logger.debug(f"LLM response received from {model} ({len(text)} chars)")
            return text

        except Exception as e:
            logger.warning(f"Provider {model} failed: {e}. Trying next provider.")
            last_error = e
            # Retry once on transient errors before moving to next provider
            if "connection" in str(e).lower() or "upstream" in str(e).lower():
                try:
                    import time as _time
                    _time.sleep(2)
                    response = completion(**kwargs)
                    text = response.choices[0].message.content or ""
                    return text
                except Exception:
                    pass
            continue

    raise RuntimeError(
        f"All LLM providers failed. Last error: {last_error}"
    )


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> dict:
    """
    Call the LLM expecting a JSON response.
    Returns a parsed dict. Strips markdown fences if present.
    """
    raw = call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=True,
    )
    return _parse_json(raw)


def _parse_json(text: str) -> dict:
    """Strip markdown code fences and parse JSON."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM JSON response: {e}\nRaw: {text[:500]}")
        return {}


def _stub_response(json_mode: bool) -> str:
    """Minimal stub used when no API keys are configured (local dev)."""
    if json_mode:
        return json.dumps({
            "signal": "Neutral",
            "strength": 50,
            "reasoning": "Stub response — no API key configured.",
            "verdict": "Fairly Valued",
            "quality_score": 50,
            "score": 0,
            "label": "Neutral",
            "analyst_consensus": "No data",
            "level": "Medium",
            "beta": 1.0,
            "var_95": 2.0,
            "decision": "ABSTAIN",
            "confidence": 0,
            "bull_argument": "Stub bull argument.",
            "bear_argument": "Stub bear argument.",
        })
    return "Stub response — no API key configured."
