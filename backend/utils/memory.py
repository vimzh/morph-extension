"""Rule extraction from conversations — agent memory."""

import json
import logging

from utils.config import get_secondary_client, get_secondary_model

logger = logging.getLogger(__name__)

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
    provider: str = "openai",
) -> list[str]:
    """Analyse a conversation and return new rules to remember.

    Args:
        history: The conversation as [{role, content}, ...].
        existing_rules: Rules already stored for this project.
        provider: LLM provider to use ("openai" or "nvidia").

    Returns:
        A (possibly empty) list of new rule strings.
    """
    if not history:
        return []

    # Build the existing-rules block
    if existing_rules:
        rules_block = "\n".join(f"- {r}" for r in existing_rules)
    else:
        rules_block = "(none yet)"

    # Format conversation for the model
    convo_lines: list[str] = []
    for msg in history:
        role = msg["role"].capitalize()
        convo_lines.append(f"{role}: {msg['content']}")
    conversation_text = "\n\n".join(convo_lines)

    try:
        client = get_secondary_client(provider)
        model = get_secondary_model(provider)
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
            max_tokens=512,
            temperature=0.3,
        )
        raw = response.choices[0].message.content.strip()
        rules = json.loads(raw)
        if not isinstance(rules, list):
            return []
        return [str(r) for r in rules if isinstance(r, str) and r.strip()]
    except Exception:
        logger.exception("Failed to extract rules from conversation")
        return []
