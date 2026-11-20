"""Rule-based CV parser (English and Arabic).

Turns extracted CV text into *candidate* career facts. Nothing here is treated
as true: every fact starts as ``extracted`` and must be approved by the user.
Each candidate carries the source line(s) it came from and a confidence score
that reflects how sure the rule was, so the review screen can show both.

The CV text is untrusted data. It is only pattern-matched here, never executed
or interpreted as instructions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

PARSER_VERSION = "rules-1.0.0"
MAX_SKILLS = 100
MAX_EXCERPT = 300

# ---------------------------------------------------------------- headings

SECTION_HEADINGS: dict[str, tuple[str, ...]] = {
    "summary": (
        "summary", "professional summary", "profile", "professional profile", "about", "about me",
        "objective", "career objective", "personal statement",
        "الملخص", "ملخص", "نبذة", "نبذة عني", "نبذة شخصية", "الملف الشخصي", "الهدف الوظيفي", "الملخص المهني",
    ),
    "experience": (
        "experience", "work experience", "professional experience", "employment", "employment history",
        "work history", "career history", "relevant experience",
        "الخبرات", "الخبرة", "الخبرات العملية", "الخبرة العملية", "الخبرات المهنية", "الخبرة المهنية",
        "الخبرات الوظيفية", "سجل العمل",
    ),
    "education": (
        "education", "academic background", "education and training", "academic qualifications",
        "qualifications", "education & training",
        "التعليم", "المؤهلات", "المؤهلات العلمية", "المؤهل العلمي", "التعليم الأكاديمي", "المؤهلات الأكاديمية",
    ),
    "skills": (
        "skills", "technical skills", "core skills", "key skills", "competencies", "core competencies",
        "areas of expertise", "expertise", "technologies", "tools", "skills & tools", "tools & technologies",
        "المهارات", "المهارات التقنية", "المهارات الفنية", "الكفاءات", "المهارات الأساسية",
    ),
    "certifications": (
        "certifications", "certificates", "certification", "licenses", "licenses & certifications",
        "licenses and certifications", "courses", "training", "courses & certifications",
        "الشهادات", "الشهادات المهنية", "الدورات", "الدورات التدريبية", "الرخص", "الشهادات والدورات",
    ),
    "languages": ("languages", "language skills", "اللغات", "المهارات اللغوية"),
    "other": (
        "projects", "references", "interests", "hobbies", "volunteering", "volunteer experience", "awards",
        "honors", "publications", "activities", "achievements",
        "المشاريع", "الهوايات", "المراجع", "التطوع", "العمل التطوعي", "الجوائز", "الإنجازات", "الأنشطة",
    ),
}  # fmt: skip
_HEADING_LOOKUP = {h: section for section, headings in SECTION_HEADINGS.items() for h in headings}
_HEADING_DECORATION = re.compile(r"^[\s#*=_•·\-–—|:]+|[\s#*=_•·\-–—|:]+$")

# -------------------------------------------------------------- patterns

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
_URL = re.compile(
    r"(?:https?://)?(?:www\.)?(?:linkedin\.com/in/|github\.com/|gitlab\.com/)[A-Za-z0-9_./%-]+|https?://[^\s,;]+"
)
_PHONE = re.compile(r"(?<![\w])(?:\+|00)?\d[\d\s().-]{6,18}\d(?![\w])")

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7,
    "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}  # fmt: skip
_MONTH_RE = "|".join(sorted((re.escape(m) for m in _MONTHS), key=len, reverse=True))
_DATE = (
    rf"(?:(?:{_MONTH_RE})\.?,?\s+(?:19|20)\d{{2}}"  # Mar 2021, March, 2021
    r"|\d{1,2}[/.-](?:19|20)\d{2}"  # 03/2021
    r"|(?:19|20)\d{2}[/.-]\d{1,2}"  # 2021-03
    r"|(?:19|20)\d{2})"  # 2021
)
_PRESENT = (
    r"(?:present|current|currently|now|today|ongoing|date|حتى الآن|حتى الان|الآن|الان|حاليا|حاليًا|حالياً|حتى تاريخه)"
)
_RANGE = re.compile(
    rf"(?P<start>{_DATE})\s*(?:-|–|—|to|until|till|إلى|الى|حتى|~)\s*(?P<end>{_DATE}|{_PRESENT})",
    re.IGNORECASE,
)
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_BULLET = re.compile(r"^\s*(?:[•·▪◦●○■□➢➤►\-*–—]|\d{1,2}[.)])\s+")

_TITLE_WORDS = (
    "engineer", "developer", "manager", "analyst", "consultant", "designer", "intern", "lead", "director",
    "specialist", "officer", "administrator", "architect", "scientist", "coordinator", "assistant", "head",
    "teacher", "accountant", "programmer", "technician", "supervisor", "executive", "associate", "advisor",
    "researcher", "representative", "owner", "founder", "president", "chief", "trainee",
    "مهندس", "مطور", "مدير", "محلل", "مستشار", "مصمم", "متدرب", "أخصائي", "اخصائي", "مسؤول", "مسئول",
    "رئيس", "منسق", "مساعد", "معلم", "محاسب", "مبرمج", "فني", "مشرف", "باحث", "ممثل", "مؤسس",
)  # fmt: skip
_ORG_WORDS = (
    "inc", "ltd", "llc", "corp", "corporation", "company", "co.", "group", "bank", "university", "ministry",
    "authority", "agency", "technologies", "solutions", "systems", "labs", "consulting", "holding", "gmbh",
    "plc", "foundation", "hospital", "institute",
    "شركة", "بنك", "مصرف", "وزارة", "هيئة", "جامعة", "مجموعة", "مؤسسة", "مستشفى", "معهد",
)  # fmt: skip
_DEGREE_WORDS = (
    "bachelor", "b.sc", "bsc", "b.s.", "b.a.", "b.eng", "beng", "master", "m.sc", "msc", "m.s.", "mba", "m.a.",
    "phd", "ph.d", "doctorate", "diploma", "associate degree", "high school",
    "بكالوريوس", "ماجستير", "دكتوراه", "دكتوراة", "دبلوم", "الثانوية", "شهادة الثانوية",
)  # fmt: skip
_INSTITUTION_WORDS = (
    "university", "college", "institute", "school", "academy", "polytechnic",
    "جامعة", "كلية", "معهد", "أكاديمية", "اكاديمية", "مدرسة",
)  # fmt: skip
_SEPARATORS = re.compile(r"\s+(?:at|@|في|لدى)\s+|\s*[|]\s*|\s+[–—-]\s+|\s*,\s*|\s*،\s*")


@dataclass(frozen=True)
class Candidate:
    fact_type: str
    value: dict[str, object]
    excerpt: str
    confidence: float
    valid_from: date | None = None
    valid_to: date | None = None


@dataclass
class _Section:
    name: str
    lines: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers


def _excerpt(*lines: str) -> str:
    text = " / ".join(line.strip() for line in lines if line.strip())
    return text[:MAX_EXCERPT]


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    for word in words:
        if word.isascii() and word.replace(".", "").isalpha() and "." not in word:
            if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
                return True
        elif word in lowered:
            return True
    return False


def _strip_bullet(line: str) -> str:
    return _BULLET.sub("", line).strip()


def _heading(line: str) -> str | None:
    candidate = _HEADING_DECORATION.sub("", line).casefold()
    if not candidate or len(candidate) > 40:
        return None
    return _HEADING_LOOKUP.get(candidate)


def parse_date(text: str, *, end: bool = False) -> tuple[str | None, date | None, bool]:
    """``(iso_text, date, is_present)``: "2020-03"/"2020", a date, and whether it means "now"."""
    lowered = text.casefold().strip().rstrip(".")
    if re.fullmatch(_PRESENT, lowered, re.IGNORECASE):
        return None, None, True
    month_name = re.match(rf"({_MONTH_RE})\.?,?\s+((?:19|20)\d{{2}})", lowered)
    if month_name:
        year, month = int(month_name.group(2)), _MONTHS[month_name.group(1)]
        return f"{year:04d}-{month:02d}", date(year, month, 1), False
    numeric = re.fullmatch(r"(\d{1,2})[/.-]((?:19|20)\d{2})", lowered) or re.fullmatch(
        r"((?:19|20)\d{2})[/.-](\d{1,2})", lowered
    )
    if numeric:
        a, b = numeric.groups()
        year, month = (int(b), int(a)) if len(a) <= 2 else (int(a), int(b))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}", date(year, month, 1), False
    year_only = _YEAR.search(lowered)
    if year_only:
        year = int(year_only.group())
        return f"{year:04d}", date(year, 12 if end else 1, 1), False
    return None, None, False


def _split_parts(text: str) -> list[str]:
    return [p.strip(" .:;") for p in _SEPARATORS.split(text) if p and p.strip(" .:;")]


def _is_header_line(line: str) -> bool:
    if not line or _BULLET.match(line):
        return False
    words = line.split()
    return 0 < len(words) <= 12 and not line.rstrip().endswith(".")


# ------------------------------------------------------------- sections


def split_sections(text: str) -> tuple[list[str], list[_Section]]:
    """``(header_lines, sections)``: lines before the first heading, then each section."""
    header: list[str] = []
    sections: list[_Section] = []
    for line in text.split("\n"):
        name = _heading(line)
        if name is not None:
            sections.append(_Section(name))
        elif sections:
            sections[-1].lines.append(line)
        else:
            header.append(line)
    return header, sections


# -------------------------------------------------------------- contact


def parse_contact(text: str, header: list[str]) -> list[Candidate]:
    facts: list[Candidate] = []
    seen: set[tuple[str, str]] = set()

    def add(field_name: str, value: str, line: str, confidence: float) -> None:
        key = (field_name, value.casefold())
        if key not in seen:
            seen.add(key)
            facts.append(Candidate("personal", {"field": field_name, "value": value}, _excerpt(line), confidence))

    lines = text.split("\n")

    # Name first: an early, short line of letters only.
    for line in (header or lines)[:6]:
        stripped = line.strip()
        words = stripped.split()
        if (
            2 <= len(words) <= 5
            and not re.search(r"[\d@/:|]", stripped)
            and all(re.fullmatch(r"[^\W\d_][^\W\d_'.-]*", w) for w in words)
            and _heading(stripped) is None
            and not _has_word(stripped, _TITLE_WORDS)
        ):
            add("full_name", stripped, stripped, 0.6)
            break

    for line in lines:
        for match in _EMAIL.finditer(line):
            add("email", match.group().lower(), line, 0.95)
        for match in _URL.finditer(line):
            url = match.group().rstrip(".,;)")
            if "@" not in url:
                add("link", url, line, 0.9)
    for line in header or lines[:8]:
        if _RANGE.search(line):
            continue
        for match in _PHONE.finditer(line):
            digits = re.sub(r"\D", "", match.group())
            if 8 <= len(digits) <= 15:
                add("phone", re.sub(r"\s+", " ", match.group().strip()), line, 0.85)
    return facts


# ----------------------------------------------------------- experience


def _entry_title_org(parts: list[str]) -> tuple[str | None, str | None, float]:
    title = next((p for p in parts if _has_word(p, _TITLE_WORDS)), None)
    rest = [p for p in parts if p != title]
    org = next((p for p in rest if _has_word(p, _ORG_WORDS)), None)
    keyword_hits = (title is not None) + (org is not None)
    if title is None and rest:
        title = rest.pop(0)
    if org is None:
        org = next((p for p in rest if p != title), None)
    confidence = {2: 0.75, 1: 0.6}.get(keyword_hits, 0.45)
    if title is None or org is None:
        confidence = min(confidence, 0.45)
    return title, org, confidence


def parse_experience(lines: list[str]) -> list[Candidate]:
    lines = [line.strip() for line in lines]
    range_rows = [i for i, line in enumerate(lines) if _RANGE.search(line)]
    facts: list[Candidate] = []
    claimed: set[int] = set()
    starts: list[int] = []
    headers_by_entry: list[list[int]] = []

    for i in range_rows:
        header_rows = [i]
        j = i - 1
        while (
            j >= 0 and len(header_rows) < 3 and j not in claimed and _is_header_line(lines[j]) and j not in range_rows
        ):
            header_rows.insert(0, j)
            j -= 1
        k = i + 1
        while k < len(lines) and len(header_rows) < 4 and _is_header_line(lines[k]) and k not in range_rows:
            # Only take a following line as header if the range line had little else on it.
            remainder = _RANGE.sub("", lines[i]).strip(" |,-–—")
            if remainder and len(header_rows) >= 2:
                break
            header_rows.append(k)
            k += 1
        claimed.update(header_rows)
        starts.append(min(header_rows))
        headers_by_entry.append(header_rows)

    for n, header_rows in enumerate(headers_by_entry):
        range_line = next(lines[r] for r in header_rows if _RANGE.search(lines[r]))
        match = _RANGE.search(range_line)
        assert match is not None  # noqa: S101 - selected because it matches
        start_text, start_date, _ = parse_date(match.group("start"))
        end_text, end_date, current = parse_date(match.group("end"), end=True)

        parts: list[str] = []
        for r in header_rows:
            parts.extend(_split_parts(_RANGE.sub(" ", lines[r]).strip(" |,()")))
        title, org, confidence = _entry_title_org(parts)
        if title is None:
            continue

        body_end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        body = [_strip_bullet(lines[r]) for r in range(max(header_rows) + 1, body_end) if lines[r] and r not in claimed]
        facts.append(
            Candidate(
                "employment",
                {
                    "title": title,
                    "organization": org,
                    "start": start_text,
                    "end": end_text,
                    "current": current,
                    "description": "\n".join(body)[:1000] or None,
                },
                _excerpt(*(lines[r] for r in header_rows)),
                confidence,
                valid_from=start_date,
                valid_to=None if current else end_date,
            )
        )
    return facts


# ------------------------------------------------------------ education


def parse_education(lines: list[str]) -> list[Candidate]:
    lines = [_strip_bullet(line) for line in lines if line.strip()]
    facts: list[Candidate] = []
    used: set[int] = set()
    for i, line in enumerate(lines):
        if i in used or not (_has_word(line, _DEGREE_WORDS) or _has_word(line, _INSTITUTION_WORDS)):
            continue
        group = [i] + [j for j in (i + 1, i + 2) if j < len(lines) and j not in used and len(lines[j].split()) <= 14]
        # Stop the group at the next line that starts another degree.
        trimmed = [group[0]]
        for j in group[1:]:
            if _has_word(lines[j], _DEGREE_WORDS) and _has_word(lines[group[0]], _DEGREE_WORDS):
                break
            trimmed.append(j)
        used.update(trimmed)
        text = " | ".join(lines[j] for j in trimmed)
        parts = _split_parts(_RANGE.sub(" ", text))
        parts = [p for p in parts if not _YEAR.fullmatch(p)]
        degree = next((p for p in parts if _has_word(p, _DEGREE_WORDS)), None)
        institution = next((p for p in parts if _has_word(p, _INSTITUTION_WORDS) and p != degree), None)
        if degree is None and institution is None:
            continue
        match = _RANGE.search(text)
        if match:
            start_text, start_date, _ = parse_date(match.group("start"))
            end_text, end_date, current = parse_date(match.group("end"), end=True)
        else:
            years = _YEAR.findall(text)
            start_text = start_date = None
            current = False
            end_text, end_date, _ = parse_date(years[-1], end=True) if years else (None, None, False)
        facts.append(
            Candidate(
                "education",
                {"degree": degree, "institution": institution, "start": start_text, "end": end_text},
                _excerpt(*(lines[j] for j in trimmed)),
                0.75 if degree and institution else 0.5,
                valid_from=start_date,
                valid_to=None if current else end_date,
            )
        )
    return facts


# --------------------------------------------------------- simple lists

_LIST_SPLIT = re.compile(r"\s*[,;|•·▪،؛/]\s*|\s{3,}")


def parse_skills(lines: list[str]) -> list[Candidate]:
    facts: list[Candidate] = []
    seen: set[str] = set()
    for line in lines:
        content = _strip_bullet(line)
        if not content:
            continue
        # "Languages: Python, Go" -> drop the category label.
        if ":" in content and len(content.split(":", 1)[0].split()) <= 4:
            content = content.split(":", 1)[1]
        for token in _LIST_SPLIT.split(content):
            token = token.strip(" .()")
            if not token or len(token) > 50 or len(token.split()) > 5 or token.casefold() in seen:
                continue
            seen.add(token.casefold())
            facts.append(Candidate("skill", {"name": token}, _excerpt(line), 0.8))
            if len(facts) >= MAX_SKILLS:
                return facts
    return facts


_LANGUAGE_LEVEL = re.compile(r"^(?P<name>[^()\-–:]+?)\s*(?:\((?P<p>[^)]+)\)|[-–:]\s*(?P<d>.+))?$")


def parse_languages(lines: list[str]) -> list[Candidate]:
    facts: list[Candidate] = []
    seen: set[str] = set()
    for line in lines:
        for token in re.split(r"\s*[,;|•·،؛]\s*", _strip_bullet(line)):
            match = _LANGUAGE_LEVEL.match(token.strip())
            if not match or not match.group("name").strip():
                continue
            name = match.group("name").strip()
            if len(name.split()) > 3 or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            level = (match.group("p") or match.group("d") or "").strip() or None
            facts.append(Candidate("language", {"name": name, "level": level}, _excerpt(line), 0.8))
    return facts


def parse_certifications(lines: list[str]) -> list[Candidate]:
    facts: list[Candidate] = []
    for line in lines:
        content = _strip_bullet(line)
        if not content or len(content.split()) > 15:
            continue
        years = _YEAR.findall(content)
        name = _YEAR.sub("", content).strip(" ,-–—|()")
        if not name:
            continue
        facts.append(
            Candidate(
                "certification",
                {"name": name, "year": int(years[-1]) if years else None},
                _excerpt(line),
                0.7,
            )
        )
    return facts


def parse_summary(lines: list[str]) -> list[Candidate]:
    text = " ".join(line.strip() for line in lines if line.strip())
    if len(text) < 20:
        return []
    return [Candidate("summary", {"text": text[:1000]}, _excerpt(text), 0.7)]


# ---------------------------------------------------------------- entry


def parse_cv(text: str) -> list[Candidate]:
    header, sections = split_sections(text)
    facts = parse_contact(text, header)
    parsers = {
        "summary": parse_summary,
        "experience": parse_experience,
        "education": parse_education,
        "skills": parse_skills,
        "certifications": parse_certifications,
        "languages": parse_languages,
    }
    for section in sections:
        parser = parsers.get(section.name)
        if parser is not None:
            facts.extend(parser(section.lines))
    return facts
