import re

from app.services.geocoding_attempts import GeocodingAttempt


LOCATION_STOPWORDS = {
    "г",
    "город",
    "деревня",
    "жк",
    "мкр",
    "набережная",
    "область",
    "пер",
    "переулок",
    "пос",
    "поселок",
    "посёлок",
    "пр",
    "проспект",
    "р",
    "район",
    "село",
    "станция",
    "ул",
    "улица",
    "ш",
    "шоссе",
}

ABBREVIATIONS = {
    "б-р": "бульвар",
    "г.": "город",
    "мкр.": "микрорайон",
    "наб.": "набережная",
    "пер.": "переулок",
    "пос.": "поселок",
    "пр-т": "проспект",
    "пр.": "проспект",
    "р-н": "район",
    "ул.": "улица",
    "ш.": "шоссе",
}

CONTEXT_ADDRESS_KEYS = {
    "area",
    "borough",
    "city",
    "city_district",
    "county",
    "district",
    "hamlet",
    "locality",
    "municipality",
    "province",
    "region",
    "state",
    "suburb",
    "town",
    "village",
}


class GeocodingResultValidator:
    def is_valid(
        self,
        *,
        attempt: GeocodingAttempt,
        formatted_address: str | None,
        address_details: dict | None = None,
    ) -> bool:
        if not formatted_address:
            return False

        haystack = self._searchable_text(formatted_address, address_details)
        if not self._contains_significant_tokens(attempt.location_text, haystack):
            return False
        context_haystack = self._context_text(formatted_address, address_details)
        if attempt.required_context and not self._contains_significant_tokens(
            attempt.required_context,
            context_haystack,
            drop_location_words=False,
        ):
            return False
        return True

    def _searchable_text(self, formatted_address: str, address_details: dict | None) -> str:
        parts = [formatted_address]
        if address_details:
            parts.extend(str(value) for value in address_details.values() if value)
        return self._normalize(" ".join(parts))

    def _context_text(self, formatted_address: str, address_details: dict | None) -> str:
        if not address_details:
            return self._normalize(formatted_address)

        parts = [
            str(value)
            for key, value in address_details.items()
            if value and str(key).lower() in CONTEXT_ADDRESS_KEYS
        ]
        return self._normalize(" ".join(parts))

    def _contains_significant_tokens(
        self,
        needle: str,
        haystack: str,
        *,
        drop_location_words: bool = True,
    ) -> bool:
        tokens = self._tokens(needle, drop_location_words=drop_location_words)
        if not tokens:
            return True
        return all(token in haystack for token in tokens)

    def _tokens(self, value: str, *, drop_location_words: bool) -> list[str]:
        normalized = self._normalize(value)
        tokens = re.findall(r"[a-zа-я]{2,}|\d+", normalized)
        if drop_location_words:
            tokens = [token for token in tokens if token not in LOCATION_STOPWORDS]
        return tokens

    def _normalize(self, value: str) -> str:
        normalized = value.casefold().replace("ё", "е")
        for source, replacement in ABBREVIATIONS.items():
            normalized = normalized.replace(source, replacement)
        normalized = re.sub(r"[^a-zа-я0-9]+", " ", normalized)
        return " ".join(normalized.split())
