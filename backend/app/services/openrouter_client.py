import json
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.services.admin_settings import DEFAULT_AI_SYSTEM_PROMPT


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = DEFAULT_AI_SYSTEM_PROMPT


class OpenRouterClient:
    def __init__(self) -> None:
        self.api_key = settings.openrouter_api_key
        self.base_url = settings.openrouter_base_url.rstrip("/")
        self.model = settings.openrouter_model

    async def classify(
        self,
        *,
        text: str,
        city: str,
        region: str | None = None,
        system_prompt: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        if not self.api_key:
            return self._fallback_classify(text=text, city=city), None

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "city": city,
                            "region": region,
                            "message": text,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=settings.openrouter_timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
        except Exception:
            logger.warning("OpenRouter request failed; using fallback classifier", exc_info=True)
            return self._fallback_classify(text=text, city=city), None

        raw = response.text
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        try:
            parsed = self._parse_content(content)
        except ValueError:
            logger.warning("OpenRouter returned non-JSON content; using fallback classifier")
            return self._fallback_classify(text=text, city=city), raw
        return self._normalize(parsed), raw

    def _parse_content(self, content: Any) -> dict[str, Any]:
        if isinstance(content, dict):
            return content
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text") or part.get("content") or "")
                if isinstance(part, dict)
                else str(part)
                for part in content
            )
        if not isinstance(content, str):
            raise ValueError("OpenRouter content is not a string")

        normalized = content.strip()
        if normalized.startswith("```"):
            normalized = normalized.strip("`").strip()
            if normalized.lower().startswith("json"):
                normalized = normalized[4:].strip()

        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError as exc:
            start = normalized.find("{")
            end = normalized.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise ValueError("OpenRouter content does not contain JSON") from exc
            parsed = json.loads(normalized[start : end + 1])

        if not isinstance(parsed, dict):
            raise ValueError("OpenRouter JSON content is not an object")
        return parsed

    def _fallback_classify(self, *, text: str, city: str) -> dict[str, Any]:
        lower = text.lower()
        related = any(term in lower for term in ["дрон", "бпла", "беспилот", "жужж", "шахед", "герань"])
        sound_only = any(term in lower for term in ["шум", "гул", "жужж", "слыш"])
        explosion = any(term in lower for term in ["взрыв", "хлоп", "бах", "пво", "сирен", "тревог"])
        location_text = self._extract_simple_location(text)
        precision = self._guess_precision(location_text)
        return {
            "is_drone_related": related or sound_only,
            "confidence": 0.62 if related else 0.55 if explosion else 0.42,
            "event_type": "possible_explosion_report"
            if explosion
            else "possible_drone_sound"
            if sound_only
            else "possible_drone_sighting",
            "risk_level": "medium" if related or explosion else "low",
            "summary": "Message matched safety keywords. OpenRouter key is not configured, so a local heuristic was used.",
            "location_text": location_text,
            "location_precision": precision if location_text else "city",
            "needs_geocoding": True,
            "useful_for_alert": related or sound_only or explosion,
            "comment": f"Base city: {city}. Configure OPENROUTER_API_KEY for stronger classification.",
        }

    def _extract_simple_location(self, text: str) -> str | None:
        markers = [" в ", " на ", " у ", " возле ", " около ", " над "]
        for marker in markers:
            if marker in text.lower():
                index = text.lower().find(marker)
                candidate = text[index + len(marker) :].strip(" .,!?:;")
                candidate = candidate.split(",", maxsplit=1)[0].strip(" .,!?:;")
                candidate = candidate.split(" похоже ", maxsplit=1)[0].strip(" .,!?:;")
                candidate = candidate.split(" слышно ", maxsplit=1)[0].strip(" .,!?:;")
                for prefix in ["районе ", "р-не ", "около ", "возле "]:
                    if candidate.lower().startswith(prefix):
                        candidate = candidate[len(prefix) :].strip(" .,!?:;")
                if candidate:
                    return candidate[:120]
        return None

    def _guess_precision(self, location_text: str | None) -> str:
        if not location_text:
            return "city"
        lower = location_text.lower()
        if any(word in lower for word in ["район", "р-н", "р-нe"]):
            return "district"
        if any(word in lower for word in ["ул.", "улица", "проспект", "пр-т", "переулок", "шоссе"]):
            return "street"
        if any(word in lower for word in ["тц", "парк", "вокзал", "станция", "школа", "жк"]):
            return "landmark"
        if any(char.isdigit() for char in lower):
            return "address"
        return "unknown"

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "is_drone_related": bool(payload.get("is_drone_related", False)),
            "confidence": min(max(float(payload.get("confidence") or 0), 0.0), 1.0),
            "event_type": payload.get("event_type") or "unclear",
            "risk_level": payload.get("risk_level") or "low",
            "summary": payload.get("summary") or "",
            "location_text": payload.get("location_text"),
            "location_precision": payload.get("location_precision") or "unknown",
            "needs_geocoding": bool(payload.get("needs_geocoding", True)),
            "useful_for_alert": bool(payload.get("useful_for_alert", False)),
            "comment": payload.get("comment") or "",
        }
