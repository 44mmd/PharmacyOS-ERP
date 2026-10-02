"""Arabic text normalisation for search.

MariaDB's utf8mb4_unicode_ci collation ignores harakat but does NOT equate the hamza forms of alef
(أ إ آ ٱ → ا), alef maqsura/ya (ى/ي), ta marbuta/ha (ة/ه) or hamza seats (ؤ/و, ئ/ي), and keeps
tatweel. Pharmacy staff type names without hamzas or with ى/ي interchangeably, so PharmacyOS
stores a normalised search key (`Item.pharma_search_key`) and normalises queries the same way.
The rules are orthographic only; no Arabic words appear in business logic.
"""

import re

_HARAKAT = re.compile("[ً-ٰٟۖ-ۭ]")  # tashkeel, superscript alef, Quranic marks
_TATWEEL = "ـ"
_FOLD = str.maketrans(
	{
		"أ": "ا",  # أ → ا
		"إ": "ا",  # إ → ا
		"آ": "ا",  # آ → ا
		"ٱ": "ا",  # ٱ → ا
		"ى": "ي",  # ى → ي
		"ة": "ه",  # ة → ه
		"ؤ": "و",  # ؤ → و
		"ئ": "ي",  # ئ → ي
		# Arabic-Indic and Persian digits → Latin, so "٥٠٠" finds "500 mg"
		**{chr(0x0660 + i): str(i) for i in range(10)},
		**{chr(0x06F0 + i): str(i) for i in range(10)},
	}
)
_SPACES = re.compile(r"\s+")


def normalize_arabic(text: str | None) -> str:
	"""Lower-case, strip diacritics/tatweel, fold letter variants and digits, collapse spaces."""
	if not text:
		return ""
	text = _HARAKAT.sub("", str(text)).replace(_TATWEEL, "")
	return _SPACES.sub(" ", text.translate(_FOLD).lower()).strip()


def build_search_key(*parts, max_length: int = 1000) -> str:
	"""Join the normalised, de-duplicated parts with " | ", capped at `max_length` characters."""
	seen, out = set(), []
	for part in parts:
		value = normalize_arabic(part)
		if value and value not in seen:
			seen.add(value)
			out.append(value)
	return " | ".join(out)[:max_length]
