"""Build the static catalog from the repository's README."""
from argparse import ArgumentParser
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html import escape
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
from shutil import copyfile
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
import sys

ROOT = Path(__file__).resolve().parents[1]
README_URL = "https://github.com/youxufkhan/awesome-ai-coding#readme"
MATURITY = {"🟢": "Stable", "🟡": "Active development", "🔴": "Experimental"}
IDENTITY = re.compile(r"^(?:(?P<badge>[🟢🟡🔴])\s+)?\*\*(?:\[(?P<link_name>.+?)\]\((?P<url>https?://[^\s)]+)\)|(?P<plain>.+?))\*\*")
SOURCE = {"github": "GitHub", "docs": "Docs", "web": "Website", "unlinked": "Unlinked"}


@dataclass
class Entry:
    name: str
    url: str | None
    categories: list[str]
    maturity: str = ""
    creator: str = ""
    description: str = ""
    details: str = ""
    category_rows: int = 0
    comparison: dict[str, str] = field(default_factory=dict)
    added: str = ""

    @property
    def repo(self):
        if self.url and urlsplit(self.url).hostname == "github.com":
            path = urlsplit(self.url).path.strip("/").split("/")
            if len(path) >= 2 and all(re.fullmatch(r"[\w.-]+", part) for part in path[:2]):
                return "/".join(path[:2]).lower()
        return None

    @property
    def id(self):
        if self.repo:
            # Several tools can live under different paths within a shared repository.
            path = urlsplit(self.url).path.strip("/").lower()
            return f"github:{path.removesuffix('.git')}"
        return normalized_url(self.url) if self.url else f"name:{self.name.casefold()}"

    @property
    def source(self):
        if self.repo:
            return "github"
        if not self.url:
            return "unlinked"
        target = urlsplit(self.url)
        if target.hostname.startswith(("docs.", "documentation.")) or target.path.startswith("/docs/"):
            return "docs"
        return "web"


def normalized_url(url):
    target = urlsplit(url)
    path = target.path.rstrip("/")
    if target.hostname == "github.com":
        path = path.lower().removesuffix(".git")
    return target.netloc.lower() + path + (f"?{target.query}" if target.query else "") + (
        f"#{target.fragment}" if target.fragment else ""
    )


def clean(value):
    return re.sub(r"</?br\s*/?>", " ", value).replace(r"\|", "|").replace("`", "").replace("**", "").strip()


def load_catalog(text):
    lines = text.splitlines()
    title = lines[0].removeprefix("# ").removesuffix(" 🚀")
    entries = {}
    comparison = []
    added_dates = {}
    category = ""
    headers = []
    week = ""
    for line in lines:
        if line.startswith("## "):
            category = re.sub(r"^[^\w]+", "", line[3:])
            headers = []
            week = ""
        if category == "Changelog":
            heading = re.match(r"^### (\d{4}-\d{2}-\d{2})", line)
            if heading:
                week = heading[1]
            change = re.match(r"^- \[([^\]]+)\]\((https?://[^ )]+)\)\s*→\s*(.+)$", line)
            if week and change:
                key = normalized_url(change[2])
                if key not in added_dates or added_dates[key][0] < week:
                    added_dates[key] = (week, change[1], change[2], change[3])
        if not line.startswith("|"):
            headers = []
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip()[1:-1])]
        if not headers:
            headers = cells
            continue
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        match = IDENTITY.match(cells[0])
        if not match or len(cells) != len(headers):
            raise ValueError(f"Cannot read row in {category}: {line}")
        name = clean(match["link_name"] or match["plain"])
        url = match["url"]
        values = {key: clean(value) for key, value in zip(headers[1:], cells[1:])}
        if "Licensing" in headers:
            comparison.append((name, url, values))
            continue
        entry = Entry(name, url, [category], MATURITY.get(match["badge"], ""),
                      values.pop("Creator", ""),
                      values.pop("Description", values.pop("Description / Key Features", "")),
                      " · ".join(value for value in values.values() if value), 1)
        if entry.id in entries:
            existing = entries[entry.id]
            if category not in existing.categories:
                existing.categories.append(category)
            existing.category_rows += 1
        else:
            entries[entry.id] = entry

    for name, url, values in comparison:
        entry = Entry(name, url, [values["Category"]], description=values["Best Suited For..."])
        candidates = [item for item in entries.values() if item.name.casefold() == name.casefold()]
        existing = entries.get(entry.id)
        if existing is None and len(candidates) == 1:
            existing = candidates[0]
        if existing is None:
            # Fold comparison-only entries into a corresponding catalog category when possible.
            related = [item for item in comparison if item[2]["Category"].split(" / ")[0] == values["Category"].split(" / ")[0]]
            for related_name, _, _ in related:
                match = next((item for item in entries.values()
                              if item.category_rows and item.name == related_name), None)
                if match:
                    entry.categories = match.categories[:1]
                    break
            entries[entry.id] = entry
            existing = entry
        existing.comparison = {key: value for key, value in values.items() if key != "Category"}

    for week, name, url, category in added_dates.values():
        entry = Entry(name, url, [category])
        if entry.id not in entries:
            entries[entry.id] = entry
        entries[entry.id].added = week
    return title, sorted(entries.values(), key=lambda entry: entry.name.casefold())


def recent(entry, today):
    return bool(entry.added) and 0 <= (today - date.fromisoformat(entry.added)).days < 7


def fetch_github(repo):
    token = os.environ["GITHUB_TOKEN"]
    request = Request(f"https://api.github.com/repos/{quote(repo, safe='/')}", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "awesome-ai-coding-catalog",
    })
    with urlopen(request, timeout=8) as response:
        return json.load(response)


def refresh_stats(entries, cached, fetch):
    repos = sorted({entry.repo for entry in entries if entry.repo})
    snapshot = {repo: cached[repo] for repo in repos if repo in cached}
    fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
    consecutive_errors = 0
    for repo in repos:
        try:
            data = fetch(repo)
            snapshot[repo] = {
                "stars": data["stargazers_count"], "forks": data["forks_count"],
                "pushed": data["pushed_at"], "fetched": fetched,
            }
        except HTTPError as error:
            print(f"{repo}: GitHub HTTP {error.code}", file=sys.stderr)
            if (error.code in (401, 429) or error.headers.get("Retry-After")
                    or error.headers.get("X-RateLimit-Remaining") == "0"):
                break
            consecutive_errors = 0
        except (OSError, HTTPException, ValueError, KeyError) as error:
            print(f"{repo}: {error}", file=sys.stderr)
            consecutive_errors += 1
            if consecutive_errors == 3:
                break
        else:
            consecutive_errors = 0
    return snapshot


def short_category(category):
    return {
        "AI Coding CLI Agents & SWE Systems": "CLI agents",
        "AI-Native IDEs & Cloud Workspaces": "IDEs & workspaces",
        "AI Coding Extensions & Plugins": "Editor extensions",
        "Custom Agent Skills & Plugins": "Agent skills",
        "AI Gateways, Proxies & Agent Security": "Gateways & security",
        "AI Agent Frameworks & Multi-Agent Platforms": "Agent frameworks",
        "LLM DevTools & Orchestration Infrastructure": "LLM developer tools",
        "Model Context Protocol (MCP) Servers": "MCP servers",
        "LLM Inference & Local Serving Engines": "Inference engines",
        "Vector Databases & Vector Stores": "Vector databases",
        "Vibe Coding & Generative UI Tools": "Generative UI",
        "AI Learning & Educational Resources": "Learning resources",
    }.get(category, category)


def day_label(value):
    day = date.fromisoformat(value[:10])
    return f"{day.day} {day:%b %Y}"


def render_link(entry):
    name = escape(entry.name)
    return f'<a href="{escape(entry.url)}">{name}</a>' if entry.url else name


def render_entry(entry, stats, today):
    measured = stats.get(entry.repo, {})
    stars, forks, pushed = (measured.get(key) for key in ("stars", "forks", "pushed"))
    fetched = measured.get("fetched")
    search = " ".join([entry.name, entry.repo or "", entry.creator, entry.description,
                       entry.details, *entry.categories, *(short_category(c) for c in entry.categories),
                       *entry.comparison.values()])
    attributes = {
        "name": entry.name, "category": json.dumps(entry.categories), "source": entry.source,
        "maturity": entry.maturity, "stars": "" if stars is None else stars,
        "forks": "" if forks is None else forks, "pushed": pushed or "", "search": search,
    }
    dataset = " ".join(f'data-{key}="{escape(str(value))}"' for key, value in attributes.items())
    badges = []
    if entry.maturity:
        badges.append(f'<span class="badge maturity" data-label="{escape(entry.maturity)}">{escape(entry.maturity)}</span>')
    if entry.source != "unlinked":
        badges.append(f'<span class="badge">{SOURCE[entry.source]}</span>')
    if recent(entry, today):
        badges.append(f'<span class="badge new"><time datetime="{entry.added}">New this week</time></span>')
    extra = [escape(value) for value in (entry.creator, entry.details) if value]
    extra.extend(f"{escape(key)}: {escape(value)}" for key, value in entry.comparison.items() if value)
    details = f'<details><summary>Tool details</summary><p>{"<br>".join(extra)}</p></details>' if extra else ""
    stats_title = f' title="Stats fetched {day_label(fetched)}"' if fetched else ""
    return f'''<article class="entry" {dataset}>
  <div class="entry-body"><h3>{render_link(entry)}</h3><p>{escape(entry.description)}</p>
    <div class="badges">{"".join(badges)}</div>{details}
  </div>
  <div class="entry-category">{escape(" / ".join(short_category(c) for c in entry.categories))}</div>
  <dl class="metrics"{stats_title}>
    <div><dt>Stars</dt><dd>{f"{stars:,}" if stars is not None else "—"}</dd></div>
    <div><dt>Forks</dt><dd>{f"{forks:,}" if forks is not None else "—"}</dd></div>
    <div><dt>Last push</dt><dd>{f'<time datetime="{escape(pushed)}">{day_label(pushed)}</time>' if pushed else "—"}</dd></div>
  </dl>
</article>'''


def render_page(title, entries, stats, featured_ids, today):
    by_id = {entry.id: entry for entry in entries}
    featured = []
    for entry_id in featured_ids:
        if entry_id not in by_id:
            print(f"Featured entry is missing: {entry_id}", file=sys.stderr)
            continue
        entry = by_id[entry_id]
        category = entry.categories[0]
        browse = "?" + urlencode({"category": category}) + "#catalog"
        class_name = "featured-pick" if featured else "featured-main"
        featured.append(f'''<article class="{class_name}">
          <a class="feature-category" href="{escape(browse)}">{escape(short_category(category))}</a>
          <h3>{render_link(entry)}</h3><p>{escape(entry.description)}</p>
        </article>''')

    categories = sorted({category for entry in entries for category in entry.categories}, key=short_category)
    def options(pairs):
        return "".join(f'<option value="{escape(value)}">{escape(label)}</option>' for value, label in pairs)

    rows = "\n".join(render_entry(entry, stats, today) for entry in entries)
    stamps = [item["fetched"] for item in stats.values() if item.get("fetched")]
    status = f"Stats last refreshed {day_label(max(stamps))}" if stamps else "Repository stats are unavailable"
    template = (ROOT / "catalog/index.html").read_text(encoding="utf-8")
    featured_section = (
        '<section class="featured" aria-labelledby="featured-heading"><h2 id="featured-heading">Featured picks</h2>'
        f'<div class="featured-grid">{"".join(featured)}</div></section>'
        if featured else ""
    )
    replacements = {
        "TITLE": escape(title), "README": escape(README_URL), "FEATURED": featured_section,
        "CATEGORIES": options((category, short_category(category)) for category in categories),
        "MATURITIES": options((label, label) for label in MATURITY.values()),
        "SOURCES": options(SOURCE.items()), "ROWS": rows, "COUNT": f"{len(entries):,}", "STATS": status,
    }
    return re.sub(r"\{\{([A-Z]+)\}\}", lambda match: replacements[match[1]], template)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--snapshot", type=Path, default=ROOT / ".catalog-stats.json")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    title, entries = load_catalog((ROOT / "README.md").read_text(encoding="utf-8"))
    stats = json.loads(args.snapshot.read_text()) if args.snapshot.exists() else {}
    if args.refresh:
        if not os.environ.get("GITHUB_TOKEN"):
            parser.error("--refresh requires GITHUB_TOKEN")
        stats = refresh_stats(entries, stats, fetch_github)
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.snapshot.with_suffix(".tmp")
        temporary.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.snapshot)
    featured_ids = json.loads((ROOT / "catalog/featured.json").read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date()
    (args.output / "index.html").write_text(render_page(title, entries, stats, featured_ids, today), encoding="utf-8")
    for asset in ("catalog.css", "catalog.js"):
        copyfile(ROOT / "catalog" / asset, args.output / asset)
    print(f"Built {len(entries)} entries with {len(stats)} GitHub snapshots in {args.output}")


if __name__ == "__main__":
    main()
