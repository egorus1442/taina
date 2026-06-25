import re
from dataclasses import dataclass

from app.services.admin_settings import DEFAULT_KEYWORD_PATTERNS


@dataclass(frozen=True)
class KeywordMatch:
    matched: bool
    terms: list[str]


class KeywordFilter:
    def __init__(self, patterns: list[str] | None = None) -> None:
        self.patterns = [
            re.compile(pattern, re.IGNORECASE | re.UNICODE)
            for pattern in (DEFAULT_KEYWORD_PATTERNS if patterns is None else patterns)
        ]

    def analyze(self, text: str) -> KeywordMatch:
        normalized = " ".join(text.lower().split())
        terms: list[str] = []
        for pattern in self.patterns:
            match = pattern.search(normalized)
            if match:
                terms.append(match.group(0))
        return KeywordMatch(matched=bool(terms), terms=terms)
