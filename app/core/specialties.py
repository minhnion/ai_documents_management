from app.core.text_normalization import normalize_search_text

HEALTH_STATION_SPECIALTY = "Trạm y tế"
HEALTH_STATION_SPECIALTY_KEY = normalize_search_text(HEALTH_STATION_SPECIALTY)


def normalize_specialty_name(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if normalize_search_text(text) == HEALTH_STATION_SPECIALTY_KEY:
        return HEALTH_STATION_SPECIALTY
    return text


def is_health_station_specialty(value: object | None) -> bool:
    return normalize_search_text(str(value)) == HEALTH_STATION_SPECIALTY_KEY
