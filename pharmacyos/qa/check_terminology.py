#!/usr/bin/env python3
"""Arabic terminology guard: a cash / POS shift is «شِفت» (plural «شِفتات», masculine) in everything PharmacyOS
shows or documents — never the older word this product used before 1.0.0-rc.3.

Word-level rule: an Arabic word whose letters contain WAW-REH-DAL-YEH followed by TEH MARBUTA, HEH, TEH or
ALEF-TEH is the old shift word (the shift noun is feminine: singular, the common HEH spelling, a pronoun
suffix such as "your shift", the plural), unless it is a supplier word (MEEM-WAW-REH-DAL: مورد / الموردين /
موردي…). Before matching, text is normalised the way it can reach the repository from other keyboards and
documents: NFKC (Arabic presentation forms), the Persian / Kurdish YEH (U+06CC) and ALEF MAKSURA (U+0649)
read as the Arabic YEH, and invisible joiners inside a word (ZWNJ, ZWJ, word joiner, soft hyphen) are
dropped; harakat and tatweel are ignored. The same letters are also the colour pink: the masculine colour
word (no feminine ending) never matches, and a feminine one is allowed only as a colour phrase — followed by
the word for "colour" (pink-coloured tablets) or preceded by it (the colour pink); write other colour
sentences that way. Display text only: English msgids, code identifiers (shift, Shift, shift_id, POS
Opening Entry) and data are not Arabic words and are never touched.

Scanned (from the repository root):
* pharmacyos/ — the app (sources, translations/ar.csv, locale/ar.po msgstr), the desktop app (src, build,
  scripts, tests), deploy/ (server, Windows installer, POS host), dev/, docs/ (including the QA report and
  results) and qa/. Generated output is skipped: node_modules, desktop/dist, desktop/server-bundle,
  __pycache__, qa/local/shots-wip. Also skipped: docs/qa/local/screenshots — historical 1.0.0-rc.2 captures
  (images and what they showed, screens.json); screenshots are no longer maintained (requirement withdrawn
  by the product owner), so that record keeps the wording of the release it was taken on.
* erpnext/locale/ar.po msgstr — ERPNext's own Arabic. Its word for an asset-depreciation shift or a
  workstation shift is not a cash shift: those msgids are listed in ERPNEXT_NOT_A_CASH_SHIFT. A new upstream
  string with the word fails here until someone decides which kind of shift it is.

In .po files only msgstr text is checked (msgid is English, comments are references).

    python3 pharmacyos/qa/check_terminology.py            # exit 0: clean; exit 1: offenders listed
    python3 pharmacyos/qa/check_terminology.py <path>...  # only these files / folders

The app's suite runs the same check (pharmacyos_erp/tests/test_terminology.py).
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path

# The rule is spelled with code points so this file never contains the word it looks for.
OLD_SHIFT = "\u0648\u0631\u062f\u064a"  # WAW REH DAL YEH
# ... + TEH MARBUTA | HEH | TEH (pronoun suffix, dual) | ALEF TEH (plural)
OLD_SHIFT_FORM = re.compile(OLD_SHIFT + "(?:[\u0629\u0647\u062a]|\u0627\u062a)")
SUPPLIER = "\u0645\u0648\u0631\u062f"  # MEEM WAW REH DAL (a supplier word)
NEW_SHIFT = "\u0634\u0650\u0641\u062a"  # SHEEN KASRA FEH TEH
COLOUR = "\u0644\u0648\u0646"  # LAM WAW NOON: colour
COLOUR_WORDS = {COLOUR, "\u0627\u0644" + COLOUR, "\u0628" + COLOUR, "\u0628\u0627\u0644" + COLOUR, "\u0644\u0644" + COLOUR}  # لون اللون بلون باللون للون
ARABIC_WORD = re.compile(r"[\u0600-\u06ff\u0750-\u077f]+")
MARKS = re.compile(r"[\u064b-\u0652\u0640]")  # harakat, tatweel
# other keyboards and documents: Persian/Kurdish YEH and ALEF MAKSURA read as YEH; invisible joiners dropped
LETTER_VARIANTS = str.maketrans({"\u06cc": "\u064a", "\u0649": "\u064a", "\u200c": None, "\u200d": None, "\u2060": None, "\u00ad": None})

REPO = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = ("pharmacyos", "erpnext/locale/ar.po")
TEXT_SUFFIXES = {
	".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".html", ".htm", ".jinja", ".j2", ".css",
	".json", ".md", ".txt", ".csv", ".po", ".pot", ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".sh", ".nsh",
	".nsi", ".iss", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".svg", ".xml", ".conf", ".template",
}
TEXT_NAMES = {"pharmacyos-server", "VERSION", "BUILD_ID"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "dist", "server-bundle", "shots-wip", ".venv", "venv"}
# folders kept as recorded history (see above), relative to the repository root
SKIP_PATHS = {"pharmacyos/docs/qa/local/screenshots"}

# erpnext/locale/ar.po: msgids whose shift is an asset-depreciation shift (Asset Shift Allocation / Factor)
# or a workstation / Item Lead Time shift — not a cash shift; ERPNext's wording stays.
ERPNEXT_NOT_A_CASH_SHIFT = {
	"Asset Depreciation Schedule for Asset {0} and Finance Book {1} is not using shift based depreciation",
	"Depreciate based on shifts",
	"Shift Name",
	"No of Shift",
	"Shift Time (In Hours)",
	"Per Day\nShift Time (In Hours) * No of Workstations * No of Shift",
	"Under Working Hours table, you can add start and end times for a Workstation. For example, a Workstation "
	"may be active from 9 am to 1 pm, then 2 pm to 5 pm. You can also specify the working hours based on "
	"shifts. While scheduling a Work Order, the system will check for the availability of the Workstation "
	"based on the working hours specified.",
}


def normalise(text: str) -> str:
	"""The letters as they are matched: NFKC, YEH variants, no invisible joiners, no harakat or tatweel."""
	return MARKS.sub("", unicodedata.normalize("NFKC", text).translate(LETTER_VARIANTS))


def is_old_shift_word(word: str) -> bool:
	plain = normalise(word)
	return bool(OLD_SHIFT_FORM.search(plain)) and SUPPLIER not in plain


def old_shift_words(text: str) -> list[str]:
	words = ARABIC_WORD.findall(normalise(text))
	return [
		w
		for i, w in enumerate(words)
		if is_old_shift_word(w)
		# a colour phrase: "<pink> colour" (pink-coloured) or "colour <pink>" (the colour pink)
		and not (i + 1 < len(words) and words[i + 1] in COLOUR_WORDS)
		and not (i > 0 and words[i - 1] in COLOUR_WORDS)
	]


def _unquote(line: str) -> str:
	line = line.strip()
	if line.startswith('"') and line.endswith('"'):
		line = line[1:-1]
	escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
	return re.sub(r"\\(.)", lambda m: escapes.get(m.group(1), "\\" + m.group(1)), line)


PO_KEYWORD = re.compile(r"^(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+(\".*\")$")


def po_entries(path: Path) -> list[tuple[int, str, str, str | None]]:
	"""(line of the msgstr, msgid, msgstr, msgctxt or None) for every entry of a .po file (plural forms
	included)."""
	entries: list[tuple[int, str, str, str | None]] = []
	current: dict[str, list] = {}  # keyword → [line, text]
	field = None

	def done():
		if "msgid" in current:
			context = current["msgctxt"][1] if "msgctxt" in current else None
			entries.extend(
				(line, current["msgid"][1], text, context) for key, (line, text) in current.items() if key.startswith("msgstr")
			)

	with open(path, encoding="utf-8") as f:
		for line_no, line in enumerate(f, 1):
			s = line.strip()
			if not s:
				done()
				current, field = {}, None
				continue
			if s.startswith("#"):  # references, flags, obsolete (#~) entries
				continue
			m = PO_KEYWORD.match(s)
			if m:
				if m.group(1) in ("msgctxt", "msgid") and any(k.startswith("msgstr") for k in current):
					done()  # a new entry without a blank line before it
					current = {}
				field = m.group(1)
				current[field] = [line_no, _unquote(m.group(2))]
			elif s.startswith('"') and field:
				current[field][1] += _unquote(s)
	done()
	return entries


def check_po(path: Path, allowed_msgids: set[str] | frozenset = frozenset()) -> list[str]:
	out = []
	for line_no, msgid, msgstr, _context in po_entries(path):
		words = old_shift_words(msgstr)
		if words and msgid not in allowed_msgids:
			out.append(f"{path}:{line_no}: {' '.join(words)} — msgid {msgid[:80]!r}")
	return out


def read_text(path: Path) -> str | None:
	"""UTF-8 (with or without a BOM), UTF-16 with a BOM, or else the Windows Arabic code page (a .bat / .cmd
	saved by Notepad on an Arabic Windows)."""
	try:
		data = path.read_bytes()
	except OSError:
		return None
	if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
		return data.decode("utf-16", "replace")
	try:
		return data.decode("utf-8-sig")
	except UnicodeDecodeError:
		return data.decode("cp1256", "replace")


def check_text(path: Path) -> list[str]:
	text = read_text(path)
	if text is None:
		return []
	out = []
	for line_no, line in enumerate(text.splitlines(), 1):
		words = old_shift_words(line)
		if words:
			out.append(f"{path}:{line_no}: {' '.join(words)} — {line.strip()[:100]}")
	return out


def is_text(path: Path) -> bool:
	return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def check_file(path: Path) -> list[str]:
	if path.suffix.lower() in (".po", ".pot"):
		allowed = ERPNEXT_NOT_A_CASH_SHIFT if path.resolve() == (REPO / "erpnext/locale/ar.po").resolve() else frozenset()
		return check_po(path, allowed)
	return check_text(path)


def skipped(path: Path) -> bool:
	try:
		rel = path.resolve().relative_to(REPO).as_posix()
	except ValueError:
		return False
	return any(rel == s or rel.startswith(s + "/") for s in SKIP_PATHS)


def scan(paths) -> list[str]:
	offenders = []
	for p in map(Path, paths):
		if p.is_file():
			offenders += check_file(p)
			continue
		if skipped(p):
			continue
		for root, dirs, files in os.walk(p):
			dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not skipped(Path(root) / d))
			for name in sorted(files):
				f = Path(root) / name
				if is_text(f):
					offenders += check_file(f)
	return offenders


def main(argv=None) -> int:
	argv = sys.argv[1:] if argv is None else argv
	paths = argv or [str(REPO / p) for p in DEFAULT_PATHS]
	offenders = scan(paths)
	if offenders:
		print(f"Arabic terminology: a cash/POS shift is «{NEW_SHIFT}» (plural «{NEW_SHIFT}ات», masculine). "
			f"{len(offenders)} line(s) still use the old word:")
		for line in offenders:
			print("  " + line)
		print("Change the display text (msgstr, docs, UI) — never msgids, identifiers or recorded data. A new "
			"ERPNext string about an asset or workstation shift goes into ERPNEXT_NOT_A_CASH_SHIFT.")
		return 1
	print(f"Arabic terminology: OK (no old shift word in {', '.join(os.path.relpath(p, REPO) for p in paths)})")
	return 0


if __name__ == "__main__":
	sys.exit(main())
