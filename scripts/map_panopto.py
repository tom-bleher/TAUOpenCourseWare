#!/usr/bin/env python3
"""Add every recording set in Panopto's Open2019 folder to README.md.

Sessions carry a code in their abstract: 0321183601-20122-9227 is course 03211836,
group 01, academic year 2012, semester 2. A recording set is one course, group,
semester and lecturer. Rows already in the README are kept as they are; only sets
they don't cover are added, and every table is rebuilt in the order the catalog
teaches its courses, so it is safe to rerun.

    python3 scripts/map_panopto.py COOKIES.txt ../Coursesearch [--dry-run]

COOKIES.txt is a Netscape cookie file from a logged-in Panopto tab. The crawl is
cached in panopto_sessions.jsonl (never commit it: abstracts can name participants).
"""
import argparse
import collections
import http.cookiejar
import json
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

HOST = "https://tau.cloud.panopto.eu/Panopto"
ROOT = "e68c3124-6817-4380-8492-ae9a00abb772"  # Open2019
KEEP = "DeliveryID SessionName Abstract Duration FolderID FolderName".split()
README = Path(__file__).resolve().parent.parent / "README.md"
CACHE = README.with_name("panopto_sessions.jsonl")

# Single-major and master's programs, whose courses all belong in the mapping.
BSC = 8366, 8371, 8359, 8650, 7606, 7605, 8360
MSC = 2238, 1890, 1712
EXCLUDED = {"03005000"}  # lab safety briefing, not a course
MIN_SESSIONS = 3  # below this a set is a stray recording, not a course
PARTIAL = 8

# README courses with no Open2019 session to infer the course number from.
COURSE_OF_ROW = {
    "ממפיס 1": "03211838",
    'חדו"א 1א': "03661101",
    "לוגיקה": "03662194",
    "פיזיקה סטטיסטית": "03212111",
    "תרמודינמיקה ומצבי צבירה": "03211104",
    "מבוא למצב מוצק": "03213103",
    "גיאומטריה דיפרנציאלית": "03662219",
    "תורת שדות לחומר מעובה": "03214285",
}
# Names the Coursesearch index lacks or gives in English, from the faculty's past catalogs:
# https://exact-sciences.tau.ac.il/yedion_past_years
CATALOG_NAMES = {
    "03211121": "מחשבים לפיזיקאים",
    "03212117": "שיטות נומריות בפיזיקה",
    "03213113": "מצב מוצק ב'",
    "03662105": "אנליזה נומרית",
    "03662132": "תורת החבורות",
    "03682161": "פרויקט תוכנה",
    "03211804": "מבוא לפיזיקה מודרנית",
    "03212830": "אלקטרוניקה",
    "03213153": "מבוא לפיזיקה חישובית",
    "03214116": "פיזיקה קוונטית 2",
    "03214117": "אלקטרומגנטיות מתקדמת",
    "03214860": "גלאי קרינה לאנרגיות גבוהות",
    "03664480": "תהליכים אקראיים ויישומיהם 1",
    "03664553": "שיטות במתמטיקה שימושית 1",
}
# One spelling per lecturer; abstracts vary.
LECTURER_ALIASES = {"דוד גינזבורג": "דוד גינסבורג", "מרק קרלינר": "מארק קרלינר", "צבי מזאה": 'צבי מז"אה', "גלית אשכנזי": "גלית אשכנזי גולן"}

SECTIONS = {
    "math_bsc": "מתמטיקה - תואר ראשון",
    "physics_bsc": "פיזיקה - תואר ראשון",
    "physics_msc": "פיזיקה - תואר שני",
    "math_msc": "מתמטיקה - תואר שני",
    "other": "קורסים נוספים בתוכניות מתמטיקה",
}
# The programs that order each section, the first that places a course winning.
# The integrated program places second- and third-year math the math catalog leaves unordered.
ORDER = {
    "math_bsc": (7606, 8371, 7605, 8359, 8360, 8650),
    "physics_bsc": (8366, 8371),
    "physics_msc": (1712,),
    "math_msc": (2238, 1890),
    "other": (8650, 8360, 8359, 7605, 1890, 2238, 1712),
}
ELECTIVES, ELSEWHERE = "קורסי בחירה", "קורסים שאינם בתוכנית הנוכחית"
HEADER = ["| קורס | מרצה | שנה | הקלטות | רשימות | הערות |", "| ---: | ---: | ---: | ---: | ---: | ---: |"]
CODE = re.compile(r"(?<!\d)(\d{10})-(\d{5})-\d+(?!\d)")
DATE = re.compile(r"\d{4}-\d\d-\d\d")
CODE_PREFIX = re.compile(r"\d{10}-\d{5}-\d+(?:\.mp4)?\s*")
TITLE = re.compile(r"^(?:פרופ['׳]?|ד[\"״]?ר|מר|'?גב['׳]?)\s+")
RECITATION = re.compile(r"exercise|targil|tirgul|תרגול|תירגול|תרגיל|recitation|tutorial|\btar\b|(?:^|[\s_-])(?:ex|t)[\s_-]?\d", re.I)


class Panopto:
    def __init__(self, cookies):
        jar = http.cookiejar.MozillaCookieJar(cookies)
        jar.load(ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def page(self, folder, query, page):
        body = {"queryParameters": {"query": query, "folderID": folder, "sortColumn": 1, "sortAscending": True,
                                    "maxResults": 250, "page": page, "getFolderData": True, "includePlaylists": False}}
        request = urllib.request.Request(f"{HOST}/Services/Data.svc/GetSessions", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
        for wait in (5, 15, 45, 0):
            try:
                with self.opener.open(request, timeout=90) as response:
                    return json.load(response)["d"]
            except OSError as error:
                if not wait:
                    raise
                print(f"retrying: {error}", file=sys.stderr)
                time.sleep(wait)

    def sessions(self, folder=ROOT, query=None):
        """Yield every session of a folder or search, one request at a time."""
        number = 0
        while True:
            data = self.page(folder, query, number)
            yield from data["Results"]
            number += 1
            if not data["Results"] or number * 250 >= data["TotalNumber"]:
                return
            time.sleep(0.2)

    def crawl(self):
        """Every session in the folder and its subfolders, unique by delivery."""
        found, queue = {}, [ROOT]
        while queue:
            folder = queue.pop()
            queue += [sub["ID"] for sub in self.page(folder, None, 0).get("Subfolders") or []]
            for session in self.sessions(folder):
                found[session["DeliveryID"]] = {key: session.get(key) for key in KEEP}
        return list(found.values())


def lecturers(abstract):
    """Lecturers in an abstract, merging spelling variants; {name without spaces: display name}."""
    names = {}
    for raw in abstract.split("הוידאו")[0].split(","):
        name = TITLE.sub("", CODE_PREFIX.sub("", raw).strip())
        key = re.sub(r"^פרופ", "", name.replace(" ", ""))  # a title garbled by stray spaces
        if len(key) > 2 and (key not in names or name.count(" ") < names[key].count(" ")):
            names[key] = name
    return names


def group_sets(sessions):
    """Split sessions into recording sets: {(code, semester, lecturer keys): [sessions]}."""
    by_code = collections.defaultdict(list)
    for session in sessions:
        match = CODE.search(session["Abstract"] or "")
        if match:
            by_code[match[1], match[2]].append(session)
    sets = {}
    for (code, semester), members in by_code.items():
        teams = collections.Counter(frozenset(lecturers(s["Abstract"])) for s in members)
        for session in members:
            team = frozenset(lecturers(session["Abstract"])) or max(teams, key=lambda t: (bool(t), teams[t]))
            # Keep a co-taught variant with the larger team it is part of.
            team = max((t for t in teams if team <= t), key=teams.get)
            sets.setdefault((code, semester, team), []).append(session)
    return sets


def years_of(semester, members):
    """Years a set was taught, from its recording dates: one code can span several offerings."""
    dates = sorted(date.fromisoformat(m[0]) for s in members if (m := DATE.search(s["Abstract"] or "")))
    offerings, current = [], []
    for day in dates:
        if current and (day - current[-1]).days > 120:
            offerings.append(current)
            current = []
        current.append(day)
    years = sorted({o[0].year for o in [*offerings, current] if len(o) >= MIN_SESSIONS})
    return ", ".join(map(str, years)) or str(int(semester[:4]) + (semester[4] in "23"))


def program_courses(coursesearch, programs):
    courses = set()
    for program in programs:
        text = (coursesearch / "data/programs" / f"{program}.json").read_text()
        courses |= set(re.findall(r'"(\d{8})"', text))
    return courses


def section_of(course, bsc, msc):
    if course[:4] not in ("0321", "0366"):
        return "other"
    subject = "physics" if course[:4] == "0321" else "math"
    graduate = course not in bsc and (course in msc or int(course[4]) >= 4)
    return f"{subject}_{'msc' if graduate else 'bsc'}"


def placements(coursesearch, section):
    """Where the catalog teaches each course of a section: {course: (rank, heading, position)}."""
    places = {}
    for order, program in enumerate(ORDER[section]):
        categories = json.loads((coursesearch / "data/programs" / f"{program}.json").read_text())["categories"]
        for number, category in enumerate(categories):
            semester, year = category.get("sem"), category.get("year")
            if semester and (category.get("required") or not year):
                term = (year - 1) * 2 + semester if year else semester
                place = term, f"סמסטר {term}"
            elif category.get("required"):
                place = 90, "שנים ב'-ג' - חובה"
            else:
                place = 91, ELECTIVES
            for index, course in enumerate(category["courses"]):
                candidate = (*place, order, number, index)
                # A semester from any program beats an unordered slot from an earlier one.
                if course not in places or candidate[0] < places[course][0] and places[course][0] >= 90:
                    places[course] = candidate
    return places


def rebuild(lines, records, coursesearch):
    """README with its tables in catalog order; records are (section, course, cells)."""
    start = next(i for i, line in enumerate(lines) if line.startswith('<a id="group-'))
    end = next(i for i, line in enumerate(lines) if line.startswith("## **"))
    body = []
    for group, (section, title) in enumerate(SECTIONS.items(), 1):
        places = placements(coursesearch, section)
        tables = collections.defaultdict(list)
        for key, course, cells in records:
            if key == section:
                place = places.get(course, (99, ELSEWHERE))
                recitation = "תרגול" in cells[5].split(", ")
                year = max(map(int, re.findall(r"\d{4}", cells[2])))
                tables[place[:2]].append((place[2:] or (course or "",), course or "", recitation, -year, cells))
        body += [f'<a id="group-{group}"></a>', "", f"## {title}", ""]
        for (_, heading), rows in sorted(tables.items()):
            body += [f"### {heading}", "", *HEADER]
            for row in sorted(rows, key=lambda row: row[:4]):
                body.append(f"| {' | '.join(row[4])} |")
            body.append("")
        body.append("")
    return [*lines[:start], *body, *lines[end:]]


def link(query):
    return (f"{HOST}/Pages/Sessions/List.aspx#folderID=%22{ROOT}%22&query=%22{urllib.parse.quote(query)}%22"
            "&sortColumn=0&sortAscending=true&maxResults=250")


def parse_rows(lines):
    """Table rows of the README: {line index: (course name, lecturer, year, url)}."""
    rows = {}
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        url = re.search(r"\((http[^)]+)\)", cells[3]) if line.startswith("| ") and len(cells) == 6 else None
        if url:
            rows[index] = cells[0], cells[1], cells[2], url[1]
    return rows


def resolve(api, url):
    """Delivery IDs behind a README link; YouTube and other links have none."""
    if "panopto" not in url:
        return set()
    fragment = urllib.parse.parse_qs(urllib.parse.urlparse(url).fragment)
    folder = fragment["folderID"][0].strip('"')
    query = fragment["query"][0][1:-1].replace('\\"', '"') if "query" in fragment else None
    return {s["DeliveryID"] for s in api.sessions(folder, query)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cookies")
    parser.add_argument("coursesearch", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="report without writing the README")
    args = parser.parse_args()
    api = Panopto(args.cookies)

    if not CACHE.exists():
        CACHE.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in api.crawl()))
    sessions = [json.loads(line) for line in CACHE.read_text().splitlines()]

    index = json.loads((args.coursesearch / "data/index.json").read_text())
    names = {**index["external"], **{code: course["name"] for code, course in index["courses"].items()}, **CATALOG_NAMES}
    bsc, msc = program_courses(args.coursesearch, BSC), program_courses(args.coursesearch, MSC)
    sets = {key: members for key, members in group_sets(sessions).items()
            if key[0][:8] not in EXCLUDED and (key[0][:8] in bsc | msc or key[0][:4] in ("0321", "0366"))}
    ids = {key: {s["DeliveryID"] for s in members} for key, members in sets.items()}
    # Subfolders hold copies, so compare rows and sets by recording code, not delivery.
    recording = {s["DeliveryID"]: m[0] for s in sessions if (m := CODE.search(s["Abstract"] or ""))}
    recordings = {key: {recording[d] for d in found} for key, found in ids.items()}
    delivery_course = {s["DeliveryID"]: key[0][:8] for key, members in sets.items() for s in members}

    lines = README.read_text().splitlines()
    rows = parse_rows(lines)
    resolved = {line: resolve(api, row[3]) for line, row in rows.items()}

    # Which course each README row is about, by its sessions or its course name.
    row_course, name_course = {}, dict(COURSE_OF_ROW)
    for line, deliveries in resolved.items():
        found = collections.Counter(delivery_course[d] for d in deliveries if d in delivery_course)
        if found:
            row_course[line] = found.most_common(1)[0][0]
            name_course[rows[line][0]] = row_course[line]
    for line, row in rows.items():
        row_course.setdefault(line, name_course.get(row[0]))

    covered, strays, added = set(), [], []
    for key, members in sets.items():
        for line, deliveries in resolved.items():
            if len({recording.get(d) for d in deliveries} & recordings[key]) * 2 >= len(recordings[key]):
                covered.add(key)
            # A YouTube row is the same recording if course, lecturer and year agree.
            elif not deliveries and row_course[line] == key[0][:8]:
                name, lecturer, year, _ = rows[line]
                surnames = {lecturers(s["Abstract"]).get(k, "").split()[-1] for s in members[:1] for k in key[2]}
                if any(abs(int(year) - int(y)) <= 1 for y in years_of(key[1], members).split(", ")) and any(n in lecturer for n in surnames if n):
                    covered.add(key)
    for key, members in sorted(sets.items(), key=lambda item: (item[0][0][:8], -int(item[0][1][:4]), item[0][0])):
        if key in covered:
            continue
        if len(members) < MIN_SESSIONS:
            strays.append(key)
            continue
        code, semester, team = key
        display = {}
        for session in members:
            display.update(lecturers(session["Abstract"]))
        people = ", ".join(display[person] for person in sorted(team)) or "—"
        query = f"{code} {semester}"
        rivals = [other for other in sets if other[:2] == key[:2] and other != key]
        if rivals and team:
            query += " " + display[sorted(team)[0]]
        found = {s["DeliveryID"] for s in api.sessions(ROOT, query)}
        time.sleep(0.1)
        if found != ids[key]:
            print(f"LINK MISMATCH {code} {semester} {people}: expected {len(ids[key])}, query gives {len(found)}")
        recitation = sum(bool(RECITATION.search(s["SessionName"] or "")) for s in members) * 2 > len(members)
        notes = ["תרגול"] if recitation else []
        if len(members) < PARTIAL:
            notes.append("חלקי")
        course = code[:8]
        name = next((row[0] for line, row in rows.items() if row_course[line] == course), names.get(course, f"קורס {course}"))
        added.append((course, [" ".join(name.split()), people, years_of(semester, members), f"[צפייה]({link(query)})", "—",
                               ", ".join(notes) or "—"]))

    records = []
    section = None
    for number, line in enumerate(lines):
        title = line[3:].strip() if line.startswith("## ") else None
        section = next((key for key, value in SECTIONS.items() if value == title), section)
        if number in rows:
            cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
            course = row_course[number]
            records.append((section_of(course, bsc, msc) if course else section, course, cells))
    records += [(section_of(course, bsc, msc), course, cells) for course, cells in added]
    for _, course, cells in records:
        if re.fullmatch(r"קורס \d{8}", cells[0]) and course in names:
            cells[0] = " ".join(names[course].split())
        for old, new in LECTURER_ALIASES.items():
            cells[1] = re.sub(rf"{old}(?!\s*גולן)", new, cells[1])
    new_lines = rebuild(lines, records, args.coursesearch)

    print(f"{len(sets)} sets; {len(covered)} already in the README; {len(added)} rows added; "
          f"{len(strays)} stray sets skipped: {sorted((k[0], k[1]) for k in strays)}")
    print("Courses without a catalog name:", sorted({k[0][:8] for k in sets if k[0][:8] not in names}))
    if not args.dry_run:
        README.write_text("\n".join(new_lines) + "\n")


if __name__ == "__main__":
    main()
