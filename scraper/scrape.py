"""Architecture job monitor — daily scraper.

Sources (UK / London for now):
  - Dezeen Jobs      (WP Job Manager AJAX endpoint, RSS fallback)
  - RIBA Jobs        (jobs.architecture.com, Madgex listing pages)
  - Bespoke Careers  (London board, JobPosting JSON-LD on each job page)

Writes data/jobs.json. Jobs keep `first_seen` / `last_seen`, and are marked
inactive (not deleted) when they disappear from a source, so the site can show
what's new and what has closed.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "jobs.json"
TODAY = datetime.now(timezone.utc).date().isoformat()

S = requests.Session()
S.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept-Language": "en-GB,en;q=0.9",
})


def get(url, **kw):
    for attempt in range(3):
        try:
            r = S.get(url, timeout=30, **kw)
            if r.status_code == 200:
                return r
            print(f"  ! {r.status_code} {url}", file=sys.stderr)
        except requests.RequestException as e:
            print(f"  ! {e} {url}", file=sys.stderr)
        time.sleep(2 + attempt * 3)
    return None


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------- helpers
SAL_NUM = re.compile(r"£\s?(\d[\d,\.]*)\s*(k)?", re.I)


def parse_salary(text):
    """'£35,000 - £45,000 per annum' -> (35000, 45000). Ignores day rates."""
    if not text:
        return None, None
    if re.search(r"per (day|hour)|/\s?(day|hr|hour)|daily|hourly", text, re.I):
        return None, None
    nums = []
    for m in SAL_NUM.finditer(text):
        v = float(m.group(1).replace(",", ""))
        if m.group(2) and v < 1000:  # '£45k'
            v *= 1000
        nums.append(int(v))
    nums = [n for n in nums if 10_000 <= n <= 400_000]
    if not nums:
        return None, None
    return min(nums), max(nums)


ROLE_RULES = [
    ("Director", r"\b(director|principal|partner|head of)\b"),
    ("Associate", r"\bassociate\b"),
    ("Senior", r"\bsenior\b|\blead\b|\bproject lead\b"),
    ("Project Architect", r"project architect"),
    ("Technologist", r"technolog|technician|bim|revit"),
    ("Part 1/2", r"part\s?(1|2|i|ii)\b|architectural assistant|graduate|post-part"),
    ("Interior", r"interior|ff&e|ffe"),
    ("Urban / Landscape", r"urban|masterplan|landscape"),
    ("Computational", r"computational|parametric|design technology"),
    ("Architect", r"architect"),
]


def classify_role(title):
    t = title.lower()
    for name, rx in ROLE_RULES:
        if re.search(rx, t):
            return name
    return "Other"


ARCH_RX = re.compile(r"architect|architectur|part\s?[12i]|technolog|technician|bim|"
                     r"urban|masterplan|landscape|computational|parametric", re.I)


def is_architecture(job):
    blob = " ".join([job["title"], " ".join(job.get("categories", []))])
    return bool(ARCH_RX.search(blob))


def mk(source, sid, **f):
    job = {
        "id": f"{source}:{sid}",
        "source": source,
        "title": clean(f.get("title")),
        "company": clean(f.get("company")) or "—",
        "location": clean(f.get("location")),
        "country": f.get("country", "UK"),
        "salary_text": clean(f.get("salary_text")) or "Undisclosed",
        "categories": f.get("categories", []),
        "posted": f.get("posted"),
        "url": f.get("url"),
        "recruiter": bool(f.get("recruiter")),
    }
    lo, hi = f.get("salary_min"), f.get("salary_max")
    if lo is None and hi is None:
        lo, hi = parse_salary(job["salary_text"])
    job["salary_min"], job["salary_max"] = lo, hi
    job["role"] = classify_role(job["title"])
    job["architecture"] = is_architecture(job)
    return job


# ---------------------------------------------------------------- Dezeen
def dezeen():
    out = {}
    for loc in ("London", "UK"):
        page = 1
        while True:
            r = get("https://www.dezeenjobs.com/jm-ajax/get_listings/",
                    params={"search_location": loc, "per_page": 50, "page": page})
            if not r:
                break
            try:
                j = r.json()
            except ValueError:
                break
            soup = BeautifulSoup(j.get("html", ""), "lxml")
            for li in soup.select("li.job_listing"):
                job = dezeen_card(li)
                if job:
                    out[job["id"]] = job
            if page >= int(j.get("max_num_pages") or 1):
                break
            page += 1
            time.sleep(1)
    if not out:
        print("  dezeen: AJAX gave nothing, trying RSS", file=sys.stderr)
        out = dezeen_rss()
    return list(out.values())


def dezeen_card(li):
    a = li.select_one("h1.entry-title a")
    if not a:
        return None
    url = a["href"]
    m = re.search(r"-(\d+)/?$", url)
    comp = li.select_one("h1.entry-title a[href*='/company/']")
    cats = [c.replace("job_listing_category-", "") for c in li.get("class", [])
            if c.startswith("job_listing_category-")]
    sal = clean((li.select_one(".salary-range") or {}).get_text() if li.select_one(".salary-range") else "")
    sal = re.sub(r"^Salary:\s*", "", sal)
    posted = None
    t = li.select_one("time.entry-date")
    if t:
        try:
            posted = datetime.strptime(clean(t.get_text()), "%d %B %Y").date().isoformat()
        except ValueError:
            pass
    loc = clean(li.select_one(".location-tag-list").get_text(" ")) if li.select_one(".location-tag-list") else ""
    loc = re.sub(r"\s+,", ",", loc)
    return mk("dezeen", m.group(1) if m else url, title=a.get_text(), url=url,
              company=comp.get_text() if comp else li.select_one("a[title]").get("title", ""),
              location=loc, salary_text=sal, categories=cats, posted=posted)


def dezeen_rss():
    out = {}
    r = get("https://www.dezeenjobs.com/", params={"feed": "job_feed", "search_location": "London"})
    if not r:
        return out
    ns = {"job_listing": "https://www.dezeenjobs.com"}
    root = ET.fromstring(r.content)
    for it in root.iter("item"):
        title = clean(BeautifulSoup(it.findtext("title") or "", "lxml").get_text())
        role, _, company = title.rpartition(" at ")
        link = it.findtext("link")
        m = re.search(r"-(\d+)/?$", link or "")
        pub = it.findtext("pubDate")
        posted = datetime.strptime(pub[:16], "%a, %d %b %Y").date().isoformat() if pub else None
        loc = it.findtext("job_listing:location", namespaces=ns) or "London"
        job = mk("dezeen", m.group(1) if m else link, title=role or title, company=company,
                 url=link, location=loc, posted=posted)
        out[job["id"]] = job
    return out


# ---------------------------------------------------------------- RIBA Jobs
def riba_relative(txt):
    txt = (txt or "").lower()
    d = datetime.now(timezone.utc).date()
    m = re.search(r"(\d+)\s+(day|week|month)", txt)
    if "today" in txt or "hour" in txt or "new" == txt.strip():
        return d.isoformat()
    if m:
        n = int(m.group(1))
        mult = {"day": 1, "week": 7, "month": 30}[m.group(2)]
        return (d - timedelta(days=n * mult)).isoformat()
    return None


def riba():
    out = {}
    base = "https://jobs.architecture.com/jobs/london-greater-/"
    for page in range(1, 11):
        url = base if page == 1 else f"{base}{page}/"
        r = get(url)
        if not r:
            break
        soup = BeautifulSoup(r.text, "lxml")
        items = soup.select("li.lister__item")
        if not items:
            break
        for li in items:
            a = li.select_one("h3.lister__header a")
            if not a:
                continue
            href = "https://jobs.architecture.com" + clean(a["href"]).split("?")[0]
            jid = re.search(r"/job/(\d+)/", href)
            meta = lambda k: clean(li.select_one(f".lister__meta-item--{k}").get_text()) \
                if li.select_one(f".lister__meta-item--{k}") else ""
            when = li.select_one(".job-actions__action.pipe")
            recruiter = meta("recruiter")
            job = mk("riba", jid.group(1) if jid else href, title=a.get_text(), url=href,
                     company=recruiter, location=meta("location"), salary_text=meta("salary"),
                     posted=riba_relative(when.get_text() if when else ""),
                     recruiter=bool(re.search(r"recruit|search|talent|people|resourc", recruiter, re.I)))
            out[job["id"]] = job
        if not soup.select_one(f'a[href*="/london-greater-/{page + 1}/"]'):
            break
        time.sleep(1)
    return list(out.values())


# ---------------------------------------------------------------- Bespoke Careers
def ld_blocks(html):
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            yield json.loads(m.group(1))
        except ValueError:
            continue


def bespoke():
    out = {}
    r = get("https://www.bespokecareers.com/london/jobs/")
    if not r:
        return []
    urls = []
    for b in ld_blocks(r.text):
        if isinstance(b, dict) and b.get("@type") == "ItemList":
            urls += [e.get("url") for e in b.get("itemListElement", []) if e.get("url")]
    for u in urls:
        jr = get(u)
        if not jr:
            continue
        jp = next((b for b in ld_blocks(jr.text)
                   if isinstance(b, dict) and b.get("@type") == "JobPosting"), None)
        if not jp:
            continue
        loc = jp.get("jobLocation") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        addr = loc.get("address", {}) if isinstance(loc, dict) else {}
        locality = addr.get("addressLocality") or addr.get("addressRegion") or "London"
        country = addr.get("addressCountry") or "UK"
        if isinstance(country, dict):
            country = country.get("name", "UK")
        if country in ("GB", "United Kingdom", "UK"):
            country = "UK"
        sal = jp.get("baseSalary") or {}
        val = sal.get("value", {}) if isinstance(sal, dict) else {}
        lo = val.get("minValue") or val.get("value")
        hi = val.get("maxValue") or lo
        unit = (val.get("unitText") or "YEAR").upper()
        cur = sal.get("currency", "GBP") if isinstance(sal, dict) else "GBP"
        lo = int(float(lo)) if lo and unit == "YEAR" else None
        hi = int(float(hi)) if hi and unit == "YEAR" else None
        sym = "£" if cur == "GBP" else f"{cur} "
        sal_text = f"{sym}{lo:,} - {sym}{hi:,}" if lo else "Undisclosed"
        jid = u.rstrip("/").split("/")[-1]
        job = mk("bespoke", jid, title=jp.get("title"), url=u, company="(via Bespoke Careers)",
                 location=locality, country=country, salary_text=sal_text,
                 salary_min=lo if cur == "GBP" else None, salary_max=hi if cur == "GBP" else None,
                 posted=(jp.get("datePosted") or "")[:10] or None, recruiter=True,
                 categories=[jp.get("occupationalCategory") or ""])
        out[job["id"]] = job
        time.sleep(0.5)
    return [j for j in out.values() if j["country"] == "UK"]


# ---------------------------------------------------------------- UK visa sponsor register
def norm_company(n):
    n = n.lower().replace("&", " and ")
    n = re.sub(r"\b(ltd|limited|llp|plc|uk|the|architects?|architecture|studio|design|"
               r"partnership|group|international|associates)\b", " ", n)
    return re.sub(r"[^a-z0-9]", "", n)


def sponsor_set():
    page = get("https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers")
    if not page:
        return None
    m = re.search(r'href="(https://assets\.publishing\.service\.gov\.uk/[^"]+\.csv)"', page.text)
    if not m:
        return None
    r = get(m.group(1))
    if not r:
        return None
    names = set()
    for row in csv.DictReader(io.StringIO(r.content.decode("utf-8-sig", "replace"))):
        route = row.get("Route", "")
        if "Skilled Worker" in route or not route:
            k = norm_company(row.get("Organisation Name", ""))
            if len(k) >= 3:
                names.add(k)
    print(f"  sponsor register: {len(names)} orgs", file=sys.stderr)
    return names


# ---------------------------------------------------------------- merge
def main():
    old = {}
    if DATA.exists():
        prev = json.loads(DATA.read_text())
        old = {j["id"]: j for j in prev.get("jobs", [])}

    scraped, status = [], {}
    for name, fn in (("dezeen", dezeen), ("riba", riba), ("bespoke", bespoke)):
        print(f"→ {name}", file=sys.stderr)
        try:
            jobs = fn()
        except Exception as e:  # one broken source must not kill the run
            print(f"  ✗ {name}: {e!r}", file=sys.stderr)
            jobs = []
        status[name] = len(jobs)
        print(f"  {len(jobs)} jobs", file=sys.stderr)
        scraped += jobs

    sponsors = sponsor_set()
    ok_sources = {k for k, v in status.items() if v > 0}
    seen = set()
    merged = {}
    for j in scraped:
        prev = old.get(j["id"], {})
        j["first_seen"] = prev.get("first_seen", TODAY)
        j["last_seen"] = TODAY
        j["active"] = True
        if sponsors is not None and not j["recruiter"]:
            k = norm_company(j["company"])
            j["sponsor"] = bool(k) and k in sponsors
        else:
            j["sponsor"] = prev.get("sponsor")
        merged[j["id"]] = j
        seen.add(j["id"])
    for jid, j in old.items():
        if jid in seen:
            continue
        # only close jobs from sources that actually answered today
        if j["source"] in ok_sources:
            j["active"] = False
        merged[jid] = j
    # drop closed jobs after 60 days
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=60)).isoformat()
    jobs = [j for j in merged.values() if j["active"] or j["last_seen"] >= cutoff]
    jobs.sort(key=lambda j: (j["first_seen"], j.get("posted") or ""), reverse=True)

    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "sources": status,
        "jobs": jobs,
    }, ensure_ascii=False, indent=1))
    print(f"saved {len(jobs)} jobs ({sum(j['active'] for j in jobs)} active)", file=sys.stderr)


if __name__ == "__main__":
    main()
