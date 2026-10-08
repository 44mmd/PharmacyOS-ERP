#!/usr/bin/env python3
"""Arabic terminology guard: a cash / POS shift is «شِفت» (plural «شِفتات», masculine) in everything PharmacyOS
shows or documents — never the older word this product used before 1.0.0-rc.3.

Word-level rule: an Arabic word whose letters (harakat and tatweel removed) contain WAW-REH-DAL-YEH is the
old shift word, unless it is a supplier word (MEEM-WAW-REH-DAL: مورد / الموردين / موردي…). Display text only:
English msgids, code identifiers (shift, Shift, shift_id, POS Opening Entry) and data are not Arabic words
and are never touched.

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
from pathlib import Path

# The rule is spelled with code points so this file never contains the word it looks for.
OLD_SHIFT = "\u0648\u0631\u062f\u064a"  # WAW REH DAL YEH
SUPPLIER = "\u0645\u0648\u0631\u062f"  # MEEM WAW REH DAL (a supplier word)
NEW_SHIFT = "\u0634\u0650\u0641\u062a"  # SHEEN KASRA FEH TEH
ARABIC_WORD = re.compile(r"[\u0600-\u06ff\u0750-\u077f]+")
MARKS = re.compile(r"[\u064b-\u0652\u0640]")  # harakat, tatweel

REPO = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = ("pharmacyos", "erpnext/locale/ar.po")
TEXT_SUFFIXES = {
	".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".html", ".htm", ".jinja", ".j2", ".css",
	".json", ".md", ".txt", ".csv", ".po", ".pot", ".ps1", ".psm1", ".sh", ".nsh", ".nsi", ".iss", ".yml",
	".yaml", ".toml", ".ini", ".cfg", ".svg", ".xml", ".conf", ".template",
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


def is_old_shift_word(word: str) -> bool:
	plain = MARKS.sub("", word)
	return OLD_SHIFT in plain and SUPPLIER not in plain


def old_shift_words(text: str) -> list[str]:
	return [m.group() for m in ARABIC_WORD.finditer(text) if is_old_shift_word(m.group())]


def _unquote(line: str) -> str:
	line = line.strip()
	if line.startswith('"') and line.endswith('"'):
		line = line[1:-1]
	escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
	return re.sub(r"\\(.)", lambda m: escapes.get(m.group(1), "\\" + m.group(1)), line)


PO_KEYWORD = re.compile(r"^(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+(\".*\")$")


def po_entries(path: Path) -> list[tuple[int, str, str]]:
	"""(line of the msgstr, msgid, msgstr) for every entry of a .po file (plural forms included)."""
	entries: list[tuple[int, str, str]] = []
	current: dict[str, list] = {}  # keyword → [line, text]
	field = None

	def done():
		if "msgid" in current:
			entries.extend((line, current["msgid"][1], text) for key, (line, text) in current.items() if key.startswith("msgstr"))

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
	for line_no, msgid, msgstr in po_entries(path):
		words = old_shift_words(msgstr)
		if words and msgid not in allowed_msgids:
			out.append(f"{path}:{line_no}: {' '.join(words)} — msgid {msgid[:80]!r}")
	return out


def check_text(path: Path) -> list[str]:
	out = []
	try:
		with open(path, encoding="utf-8") as f:
			for line_no, line in enumerate(f, 1):
				words = old_shift_words(line)
				if words:
					out.append(f"{path}:{line_no}: {' '.join(words)} — {line.strip()[:100]}")
	except (UnicodeDecodeError, OSError):
		pass
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
