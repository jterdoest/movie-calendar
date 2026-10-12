import copy
import datetime as dt
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_calendar as bc  # noqa: E402

CFG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
TODAY = dt.date(2026, 10, 12)
NOW = dt.datetime(2026, 10, 12, 15, 0, tzinfo=dt.timezone.utc)


def rd(date, type_, cert="", note=""):
    return {"release_date": f"{date}T00:00:00.000Z", "type": type_, "certification": cert, "note": note, "iso_639_1": ""}


MOVIES = {
    101: {
        "id": 101, "title": "Starfall: A Very Long Title, With Commas; and Semicolons — plus émojis 🚀", "release_date": "2026-11-06",
        "runtime": 128, "overview": "A crew races to stop a dying star.\nSecond line.", "poster_path": "/abc.jpg",
        "genres": [{"name": "Science Fiction"}, {"name": "Adventure"}],
        "release_dates": {"results": [
            {"iso_3166_1": "GB", "release_dates": [rd("2026-11-01", 3, "12A")]},
            {"iso_3166_1": "US", "release_dates": [rd("2026-10-20", 1), rd("2026-11-06", 3, "PG-13")]},
        ]},
        "credits": {"cast": [{"name": n} for n in ["A One", "B Two", "C Three", "D Four", "E Five"]],
                    "crew": [{"name": "Dee Rector", "job": "Director"}, {"name": "X", "job": "Writer"}]},
        "videos": {"results": [
            {"site": "YouTube", "type": "Teaser", "key": "teaser1", "official": True, "iso_639_1": "en"},
            {"site": "YouTube", "type": "Trailer", "key": "trailerXYZ", "official": True, "iso_639_1": "en"},
        ]},
        "external_ids": {"imdb_id": "tt1234567"},
    },
    102: {  # re-release of an old film
        "id": 102, "title": "The Fellowship", "release_date": "2001-12-19", "runtime": 178, "overview": "Classic.",
        "genres": [{"name": "Fantasy"}],
        "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [rd("2001-12-19", 3, "PG-13"), rd("2026-12-04", 3, "", "Re-release")]}]},
        "credits": {"cast": [], "crew": []}, "videos": {"results": []}, "external_ids": {},
    },
    103: {  # limited only
        "id": 103, "title": "Small Indie", "release_date": "2026-10-23", "runtime": 92, "overview": "",
        "genres": [], "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [rd("2026-10-23", 2, "R")]}]},
        "credits": {"cast": [], "crew": []}, "videos": {"results": []}, "external_ids": {},
    },
    104: {  # US theatrical outside the window
        "id": 104, "title": "Way Later", "release_date": "2028-05-01", "runtime": None, "overview": "",
        "genres": [], "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [rd("2028-05-01", 3)]}]},
        "credits": {"cast": [], "crew": []}, "videos": {"results": []}, "external_ids": {},
    },
}


class FakeAPI:
    def __init__(self):
        self.calls = []

    def get(self, path, **params):
        self.calls.append((path, params))
        if path == "/discover/movie":
            if params["page"] == 1:
                return {"total_pages": 3, "results": [
                    {"id": 101, "popularity": 90.0}, {"id": 102, "popularity": 40.0},
                    {"id": 103, "popularity": 12.0}, {"id": 104, "popularity": 8.0},
                ]}
            if params["page"] == 2:
                return {"total_pages": 3, "results": [{"id": 105, "popularity": 4.0}, {"id": 101, "popularity": 3.0}]}
            raise AssertionError("should stop paging once popularity falls below the floor")
        return copy.deepcopy(MOVIES[int(path.rsplit("/", 1)[1])])


class BuildTests(unittest.TestCase):
    def build(self, cfg=CFG):
        api = FakeAPI()
        ics, events = bc.build(api, cfg, TODAY, NOW)
        return api, ics, {e["uid"]: e for e in events}

    def test_discover_params_and_paging(self):
        api, _, _ = self.build()
        pages = [p for path, p in api.calls if path == "/discover/movie"]
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0]["region"], "US")
        self.assertEqual(pages[0]["with_release_type"], "3")
        self.assertEqual(pages[0]["release_date.gte"], "2026-08-13")
        self.assertEqual(pages[0]["release_date.lte"], "2027-10-12")
        detail_ids = [path for path, _ in api.calls if path.startswith("/movie/")]
        self.assertNotIn("/movie/105", detail_ids)  # below min_popularity

    def test_event_content(self):
        _, _, ev = self.build()
        self.assertEqual(set(ev), {"tmdb-101@movie-calendar", "tmdb-102@movie-calendar"})
        e = ev["tmdb-101@movie-calendar"]
        self.assertEqual(e["date"], dt.date(2026, 11, 6))  # wide release, not the premiere
        self.assertTrue(e["summary"].startswith("🍿 Starfall"))
        d = e["description"]
        self.assertIn("PG-13 · 2h 8m · Science Fiction, Adventure", d)
        self.assertIn("Directed by Dee Rector", d)
        self.assertIn("Starring A One, B Two, C Three, D Four", d)
        self.assertNotIn("E Five", d)
        self.assertIn("youtube.com/watch?v=trailerXYZ", d)
        self.assertIn("imdb.com/title/tt1234567", d)
        self.assertIn("not endorsed or certified by TMDB", d)

    def test_rerelease_tag(self):
        _, _, ev = self.build()
        e = ev["tmdb-102@movie-calendar"]
        self.assertEqual(e["summary"], "🍿 The Fellowship (Re-release)")
        self.assertEqual(e["date"], dt.date(2026, 12, 4))
        self.assertTrue(e["description"].startswith("PG-13 · 2h 58m · Fantasy"))  # cert falls back to earlier entry

    def test_limited_releases_optional(self):
        cfg = copy.deepcopy(CFG)
        cfg["releases"]["release_types"] = [2, 3]
        _, _, ev = self.build(cfg)
        self.assertEqual(ev["tmdb-103@movie-calendar"]["summary"], "🍿 Small Indie (Limited)")

    def test_ics_is_valid(self):
        _, ics, _ = self.build()
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n"))
        for line in ics.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)
        self.assertNotIn("\n", ics.replace("\r\n", ""))
        unfolded = ics.replace("\r\n ", "")
        self.assertIn("DTSTART;VALUE=DATE:20261106", unfolded)
        self.assertIn("DTEND;VALUE=DATE:20261107", unfolded)
        self.assertIn("Commas\\; and Semicolons", unfolded)
        self.assertIn("TRANSP:TRANSPARENT", unfolded)
        self.assertNotIn("BEGIN:VALARM", unfolded)
        try:
            import icalendar
        except ImportError:
            return
        cal = icalendar.Calendar.from_ical(ics)
        evs = list(cal.walk("VEVENT"))
        self.assertEqual(len(evs), 2)
        self.assertEqual(str(evs[0]["SUMMARY"]), "🍿 " + MOVIES[101]["title"])
        self.assertIn("A crew races to stop a dying star.\nSecond line.", str(evs[0]["DESCRIPTION"]))
        self.assertEqual(evs[0]["DTSTART"].dt, dt.date(2026, 11, 6))

    def test_alert(self):
        cfg = copy.deepcopy(CFG)
        cfg["events"]["alert"] = "PT9H"
        _, ics, _ = self.build(cfg)
        self.assertEqual(ics.count("TRIGGER:PT9H"), 2)

    def test_runtime_format(self):
        self.assertEqual(bc.format_runtime(45), "45m")
        self.assertEqual(bc.format_runtime(120), "2h 0m")
        self.assertEqual(bc.format_runtime(None), "")


if __name__ == "__main__":
    unittest.main()
