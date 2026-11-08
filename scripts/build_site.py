#!/usr/bin/env python3
"""Build docs/index.html, served by GitHub Pages, from open_courses.json and the curated rows of README.md.

    python3 scripts/map_panopto.py COOKIES.txt ../Coursesearch --all open_courses.md
    python3 scripts/build_site.py ../Coursesearch
"""
import json
import re
import sys
import urllib.parse
from pathlib import Path

from map_panopto import COURSE_OF_ROW, parse_rows

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/tom-bleher/TAUOpenCourseWare/blob/main"
# README rows whose course number appears only in their Panopto folder's name.
ROW_COURSES = {**COURSE_OF_ROW, "פונקציות L": "03664188"}
TAGS = {"זום": "zoom", "חלקי": "partial", "תרגול": "recitation", "כולל תרגולים": "mixed"}


def curated_rows():
    """README rows with what they know beyond the crawl."""
    lines = (ROOT / "README.md").read_text().splitlines()
    rows = []
    for number, (name, who, years, url) in parse_rows(lines).items():
        cells = [cell.strip() for cell in lines[number].strip().strip("|").split(" | ")]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).fragment).get("query", [""])[0].strip('"').replace('\\"', '"')
        code = re.search(r"(?<!\d)\d{10}(?!\d)", query)
        notes = cells[5]
        anchor = re.search(r'<a href="([^"]+)"', notes)
        text = re.sub(r"<[^>]+>", "", notes).strip()
        tags = [tag.strip() for tag in text.split(",")]
        known = [] if text == "—" or not all(tag in TAGS for tag in tags) else tags
        pdf = re.search(r"\((assets/[^)]+)\)", cells[4])
        rows.append({"name": name, "who": who, "years": years, "url": url, "query": query, "course": code[0][:8] if code else None,
                     "tags": known, "note": "" if known or text == "—" else text, "noteUrl": anchor[1] if anchor else None,
                     "pdf": f"{REPO}/{pdf[1]}" if pdf else None})
    named = {row["name"]: row["course"] for row in rows if row["course"]}
    for row in rows:
        row["course"] = row["course"] or ROW_COURSES.get(row["name"]) or named.get(row["name"])
    return rows


def apply_row(item, row, exact):
    """Let a README row correct the set it describes."""
    if not exact and item["years"] != row["years"]:
        item["sessions"] = item["hours"] = None  # the link no longer shows the whole set
    item["years"] = row["years"]
    if "youtube" in row["url"]:
        item["youtube"] = row["url"]
    elif not exact:
        item["href"] = row["url"]
    item["note"], item["noteUrl"], item["pdf"] = row["note"], row["noteUrl"], row["pdf"]
    item["mixed"] = "כולל תרגולים" in row["tags"]
    item["zoom"] = "זום" in row["tags"]
    item["partial"] = item["partial"] or "חלקי" in row["tags"]
    item["recitation"] = item["recitation"] or "תרגול" in row["tags"]


def merge(courses, rows, index, all_time):
    by_code = {course["code"]: course for course in courses}
    report = {"exact": 0, "matched": 0, "added": 0}
    taken = set()

    def attach(row, course):
        surnames = [person.split()[-1] for person in row["who"].split(", ")]
        for item in course["sets"]:
            if id(item) in taken or not any(name in item["who"] for name in surnames):
                continue
            if row["years"] in re.findall(r"\d{4}", item["years"]):
                taken.add(id(item))
                apply_row(item, row, exact=False)
                return True
        return False

    pending = []
    for row in rows:
        course = by_code.get(row["course"])
        match = course and next((s for s in course["sets"] if s["query"] == row["query"] and id(s) not in taken), None)
        if match:
            taken.add(id(match))
            apply_row(match, row, exact=True)
            report["exact"] += 1
        else:
            pending.append(row)
    for row in pending[:]:
        course = by_code.get(row["course"])
        if course and attach(row, course):
            pending.remove(row)
            report["matched"] += 1
    for row in pending:
        code = row["course"]
        course = by_code.get(code)
        if not course:
            past = all_time.get(code, {})
            known = index["courses"].get(code, {})
            faculties = {dept: faculty for faculty, depts in index["meta"]["faculties"].items() for dept in depts}
            dept = known.get("dept") or past.get("faculty", "/").split("/", 1)[1] or "—"
            course = by_code[code] = {"code": code, "name": known.get("name") or past.get("name") or row["name"], "dept": " ".join(dept.split()),
                                      "faculty": faculties.get(known.get("dept")) or past.get("faculty", "לא מסווג/").split("/")[0],
                                      "last": max([known.get("last") or "", *past.get("semesters", [])]), "credits": known.get("credits"),
                                      "indexed": code in index["courses"], "sets": []}
            courses.append(course)
        item = {"who": row["who"], "years": row["years"], "recitation": False, "partial": False, "sessions": None, "hours": None, "query": ""}
        apply_row(item, row, exact=False)
        course["sets"].append(item)
        report["added"] += 1
    for row in rows:
        course = by_code[row["course"]]
        if row["name"] != course["name"] and row["name"] not in course.setdefault("aliases", []):
            course["aliases"].append(row["name"])
    for course in courses:
        course["sets"].sort(key=lambda s: (s["recitation"], -max(map(int, re.findall(r"\d{4}", s["years"])))))
    return report


def main():
    coursesearch = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "Coursesearch"
    data = json.loads((ROOT / "open_courses.json").read_text())
    index = json.loads((coursesearch / "data/index.json").read_text())
    all_time = json.loads((ROOT / "arazim_courses.json").read_text())
    report = merge(data["courses"], curated_rows(), index, all_time)
    template = (ROOT / "scripts/site_template.html").read_text()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    output = ROOT / "docs/index.html"
    output.parent.mkdir(exist_ok=True)
    output.write_text(template.replace("/*DATA*/null", payload))
    courses = data["courses"]
    print(f"{output}: {len(courses)} courses, {sum(len(c['sets']) for c in courses)} sets, README rows {report}, {output.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
