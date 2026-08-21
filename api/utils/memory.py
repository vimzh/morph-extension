"""Rule extraction from conversations — agent memory."""

import json
import logging
import re

from utils.config import get_secondary_client, get_secondary_model

logger = logging.getLogger(__name__)

PERSISTENCE_SIGNAL = re.compile(
    r"(?:^\s*(?:always|never)\b|\b(?:from now on|going forward|in future|for future|"
    r"remember (?:that|to)|i prefer|my preference|do not ask me again|"
    r"don't ask me again|stop asking)\b)",
    re.IGNORECASE,
)

EXTRACTION_PROMPT = """\
You are a meta-analysis assistant. Your job is to review a conversation between \
a user and a coding agent, then extract **rules** — concise behavioural directives \
that the agent should follow in future interactions with this user.

Focus on:
- Explicit user preferences or instructions (e.g. "always use TypeScript", "don't add comments")
- Corrections the user made to the agent's behaviour
- Style or workflow preferences (e.g. "keep responses short", "use tabs not spaces")
- Domain-specific constraints the user mentioned

Do NOT extract:
- Facts about the specific task at hand (those are ephemeral)
- Instructions that only apply to the current request, even when phrased imperatively
- Behaviour suggested by the assistant rather than explicitly requested by the user
- Rules that are already covered by the existing rules listed below
- Obvious or generic best practices that any agent would follow

Return a JSON array of strings. Each string is one rule — a single actionable sentence.
If nothing noteworthy was expressed, return an empty array: []

## Existing Rules (do not duplicate these)
{existing_rules}

Respond ONLY with the JSON array. No explanation, no markdown fences.\
"""


async def extract_rules(
    history: list[dict],
    existing_rules: list[str],
) -> list[str]:
    """Analyse a conversation and return new rules to remember.

    Args:
        history: The conversation as [{role, content}, ...].
        existing_rules: Rules already stored for this project.
    Returns:
        A (possibly empty) list of new rule strings.
    """
    if not history:
        return []

    latest_user_message = next(
        (msg["content"] for msg in reversed(history) if msg.get("role") == "user"),
        "",
    )
    if not PERSISTENCE_SIGNAL.search(latest_user_message):
        return []

    # Build the existing-rules block
    if existing_rules:
        rules_block = "\n".join(f"- {r}" for r in existing_rules)
    else:
        rules_block = "(none yet)"

    # Only the latest explicit preference is eligible. Re-processing the full
    # conversation caused one-off task instructions and agent wording to become rules.
    conversation_text = f"User: {latest_user_message}"

    try:
        client = get_secondary_client()
        model = get_secondary_model()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": EXTRACTION_PROMPT.format(existing_rules=rules_block),
                },
                {
                    "role": "user",
                    "content": conversation_text,
                },
            ],
            max_completion_tokens=2048,
            reasoning_effort="low",
        )
        raw = response.choices[0].message.content.strip()
        rules = json.loads(raw)
        if not isinstance(rules, list):
            return []
        return [str(r) for r in rules if isinstance(r, str) and r.strip()]
    except Exception:
        logger.exception("Failed to extract rules from conversation")
        return []
