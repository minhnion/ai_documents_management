from __future__ import annotations

import re
import unicodedata

NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
VIETNAMESE_NORMALIZATION_GROUPS: tuple[tuple[str, str], ...] = (
    ("a", "àáạảãâầấậẩẫăằắặẳẵ"),
    ("e", "èéẹẻẽêềếệểễ"),
    ("i", "ìíịỉĩ"),
    ("o", "òóọỏõôồốộổỗơờớợởỡ"),
    ("u", "ùúụủũưừứựửữ"),
    ("y", "ỳýỵỷỹ"),
    ("d", "đ"),
)
VIETNAMESE_TRANSLATION_SOURCE = "".join(
    chars for _, chars in VIETNAMESE_NORMALIZATION_GROUPS
)
VIETNAMESE_TRANSLATION_TARGET = "".join(
    replacement * len(chars)
    for replacement, chars in VIETNAMESE_NORMALIZATION_GROUPS
)


def normalize_search_text(value: str | None) -> str:
    if value is None:
        return ""

    text = value.strip().lower()
    if not text:
        return ""

    text = text.replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(
        char for char in text if unicodedata.category(char) != "Mn"
    )
    return NON_ALNUM_RE.sub("", text)
