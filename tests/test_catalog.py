from datetime import date
from pathlib import Path
import re
import unittest
from http.client import IncompleteRead

from catalog.build import load_catalog, recent, refresh_stats, render_page

ROOT = Path(__file__).resolve().parents[1]


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = (ROOT / "README.md").read_text()
        cls.title, cls.entries = load_catalog(cls.readme)
        cls.by_name = {entry.name: entry for entry in cls.entries}

    def test_every_source_row_is_represented(self):
        expected = sum(line.startswith(("| 🟢 ", "| 🟡 ", "| 🔴 "))
                       for line in self.readme.splitlines())
        covered = sum(entry.category_rows for entry in self.entries)
        self.assertEqual(covered, expected)
        self.assertEqual(len({entry.id for entry in self.entries}), len(self.entries))
        self.assertIsNone(self.by_name["OpenHands"].url)
        self.assertEqual(self.by_name["OpenHands"].categories, self.by_name["Aider"].categories)
        self.assertEqual(self.by_name["MiMo Code"].repo, "xiaomimimo/mimo-code")
        self.assertEqual(self.by_name["Cursor"].comparison["Licensing"], "Proprietary")

    def test_table_schemas_and_changelog(self):
        self.assertIn("VS Code", self.by_name["Tabby"].details)
        self.assertEqual(self.by_name["Aider"].repo, "aider-ai/aider")
        self.assertIsNone(self.by_name["GitHub Copilot"].repo)
        self.assertEqual(self.by_name["GitHub Copilot Workspace"].source, "web")
        self.assertEqual(self.by_name["SolidCraft"].added, "2026-11-16")
        self.assertTrue(recent(self.by_name["SolidCraft"], date(2026, 11, 17)))
        self.assertFalse(recent(self.by_name["SolidCraft"], date(2026, 11, 24)))

    def test_snapshot_retains_verified_values_and_preserves_zero(self):
        avatar = "https://avatars.githubusercontent.com/u/123?v=4"

        def fetch(repo):
            if repo == "aider-ai/aider":
                return {"stargazers_count": 0, "forks_count": 0, "pushed_at": None,
                        "owner": {"avatar_url": avatar}}
            raise IncompleteRead(b"incomplete GitHub response")

        cached = {"tangency-labs/solidcraft": {"stars": 4, "fetched": "2026-11-16T00:00:00Z"}}
        entries = [self.by_name["Aider"], self.by_name["SolidCraft"], self.by_name["Cursor"]]
        snapshot = refresh_stats(entries, cached, fetch)
        self.assertEqual(snapshot["aider-ai/aider"]["stars"], 0)
        self.assertEqual(snapshot["aider-ai/aider"]["avatar"], avatar)
        self.assertEqual(snapshot["tangency-labs/solidcraft"], cached["tangency-labs/solidcraft"])
        self.assertEqual(len(snapshot), 2)

    def test_static_page_contains_all_entries_and_escapes_text(self):
        page = render_page("<Example>", self.entries, {}, ["github:aider-ai/aider"], date(2026, 11, 17))
        self.assertIn("&lt;Example&gt;", page)
        self.assertEqual(len(re.findall(r'<article class="entry"', page)), len(self.entries))
        self.assertIn('class="featured-main"', page)
        self.assertIn('data-source="docs"', page)
        self.assertIn('data-source="unlinked"', page)

    def test_cards_prefer_project_icons_and_use_publisher_avatars_as_fallback(self):
        avatar = "https://avatars.githubusercontent.com/u/123?v=4"
        stats = {"aider-ai/aider": {"avatar": avatar, "stars": 0}, "microsoft/autogen": {"avatar": avatar}}
        page = render_page("Example", [self.by_name[name] for name in ("Aider", "AutoGen", "OpenHands")],
                           stats, [], date(2026, 11, 17))
        self.assertIn('src="https://aider.chat/assets/icons/favicon-32x32.png"', page)
        self.assertEqual(page.count(f'src="{avatar}&amp;s=96"'), 1)
        self.assertIn('class="star-icon" aria-hidden="true"', page)
        self.assertIn('data-stars="0"', page)
        self.assertNotIn('class="entry-columns"', page)

    def test_category_icons_are_visible_on_cards_with_accessible_labels(self):
        page = render_page("Example", self.entries, {}, ["github:aider-ai/aider"], date(2026, 11, 17))
        for icon in ("terminal", "editor", "extension", "skills", "shield", "network",
                     "tools", "plug", "chip", "database", "layout", "book"):
            self.assertIn(f'data-category-icon="{icon}"', page)
        self.assertRegex(page, r'data-category-icon="terminal" aria-hidden="true".*?</svg>CLI agents')
