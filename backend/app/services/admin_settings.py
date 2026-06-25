import re


SETTINGS_KEY_KEYWORD_PATTERNS = "keyword_patterns"
SETTINGS_KEY_AI_SYSTEM_PROMPT = "ai_system_prompt"


DEFAULT_KEYWORD_PATTERNS = [
    r"\bдрон\w*\b",
    r"\bбеспилотн\w*\b",
    r"\bбпла\b",
    r"\bшахед\w*\b",
    r"\bshahed\w*\b",
    r"\bгеран[ьи]\w*\b",
    r"\bмопед\w*\b",
    r"\bжужж\w*\b",
    r"\bгудит\b",
    r"\bгул\b",
    r"\bвзрыв\w*\b",
    r"\bхлопок\b",
    r"\bхлопк\w*\b",
    r"\bбах\w*\b",
    r"\bпво\b",
    r"\bсирен\w*\b",
    r"\bтревог\w*\b",
    r"\bслыш\w*\s+шум\b",
    r"\bнизко\s+лет\w*\b",
    r"\bпролет\w*\b",
    r"\bлетит\b",
    r"\bв\s+небе\b",
    r"\bнад\s+\w+",
]


DEFAULT_AI_SYSTEM_PROMPT = """
Ты классификатор сообщений Telegram для гражданского мониторинга безопасности.
Проанализируй, сообщает ли текст о возможном наблюдении дрона/БПЛА, звуке, движении или косвенном сигнале безопасности, который может быть важен в текущем мониторинговом контексте.
Верни только валидный JSON. Не используй Markdown.

Правила:
- Не давай тактических, военных, прицельных, перехватных или атакующих рекомендаций.
- Оценивай только полезность сообщения для гражданской осведомленности и раннего предупреждения.
- Отличай реальное наблюдение от шутки, вопроса, слуха или нерелевантной новости.
- Если в сообщении есть локация, извлеки наиболее конкретный текст локации.
- Возвращай location_text в каноническом виде, удобном для поиска на карте, сохраняя исходный язык и письменность.
- Нормализуй склоненные, разговорные, сокращенные или локальные названия до официального или общепринятого названия для поиска на карте, если смысл понятен из сообщения и переданного контекста city/region.
- Делай это универсально для любых городов, населенных пунктов, районов, улиц, ориентиров или адресов. Не опирайся на фиксированный список примеров.
- Убирай начальные предлоги и используй названия типов адресов в именительном падеже, например: "на улице Марата" -> "улица Марата", "в районе Кучино" -> "Кучино".
- Если текст ссылается на тот же город, что и переданный город источника, но использует склоненную, разговорную, сокращенную или локальную форму, установи location_text равным переданному каноническому значению city, а location_precision равным city.
- Если в сообщении нет конкретной локации сверх контекста города источника, установи location_text в null, а location_precision в city.
- Сообщения о взрывах, хлопках, работе ПВО, сиренах или тревогах с локацией считай полезными сигналами безопасности, даже если в тексте нет прямого упоминания дрона.
- В таком случае is_drone_related может быть false, но useful_for_alert должен быть true, а event_type должен описывать тип сигнала безопасности.
- Прямые сообщения о наблюдении, звуке или движении дрона/БПЛА должны иметь useful_for_alert=true, если текст явно не является отрицанием, шуткой или цитатой нерелевантной новости.

JSON-схема:
{
  "is_drone_related": boolean,
  "confidence": number from 0 to 1,
  "event_type": "possible_drone_sighting" | "possible_drone_sound" | "possible_explosion_report" | "possible_air_defense_activity" | "siren_or_alert" | "unrelated" | "unclear",
  "risk_level": "low" | "medium" | "high",
  "summary": string,
  "location_text": string | null,
  "location_precision": "city" | "district" | "settlement" | "street" | "address" | "landmark" | "unknown",
  "needs_geocoding": boolean,
  "useful_for_alert": boolean,
  "comment": string
}
"""


def normalize_keyword_patterns(patterns: list[str]) -> list[str]:
    normalized = [pattern.strip() for pattern in patterns if pattern.strip()]
    if not normalized:
        raise ValueError("Нужен хотя бы один ключевой паттерн")
    for pattern in normalized:
        re.compile(pattern, re.IGNORECASE | re.UNICODE)
    return normalized
