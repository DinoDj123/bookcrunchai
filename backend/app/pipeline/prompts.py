"""Prompt files of the LLM pipeline (backend/app/pipeline/prompts/*.md).

A prompt file has a system part and a user part, each starting with a line of its own:

    # System
    ...
    # User
    ...

Placeholders are written {{name}} and filled by render(). str.format() is not used because book
text and model instructions may contain { and }. The replacement is one pass over the template,
so a value is never searched for placeholders: a book whose text happens to contain "{{title}}"
stays as it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"

SYSTEM_HEADER = "# System"
USER_HEADER = "# User"
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class Prompt:
    name: str
    system: str
    user: str

    @property
    def placeholders(self) -> frozenset[str]:
        return frozenset(PLACEHOLDER.findall(self.system + self.user))

    def render(self, **values: str) -> tuple[str, str]:
        """Returns (system, user) with every placeholder filled. Every placeholder needs a value
        and every value a placeholder, so a typo in a prompt file fails in the tests."""
        missing = self.placeholders - values.keys()
        unknown = values.keys() - self.placeholders
        if missing or unknown:
            raise KeyError(
                f"prompt {self.name}: missing {sorted(missing)}, unknown {sorted(unknown)}"
            )

        def fill(template: str) -> str:
            return PLACEHOLDER.sub(lambda match: values[match.group(1)], template)

        return fill(self.system), fill(self.user)


@lru_cache
def load_prompt(name: str) -> Prompt:
    """Reads prompts/<name>.md once per process."""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        system_at = lines.index(SYSTEM_HEADER)
        user_at = lines.index(USER_HEADER)
    except ValueError:
        raise ValueError(
            f"prompt {name}: needs a {SYSTEM_HEADER!r} and a {USER_HEADER!r} line"
        ) from None
    if user_at < system_at:
        raise ValueError(f"prompt {name}: {SYSTEM_HEADER!r} must come before {USER_HEADER!r}")
    return Prompt(
        name=name,
        system="\n".join(lines[system_at + 1 : user_at]).strip(),
        user="\n".join(lines[user_at + 1 :]).strip(),
    )
