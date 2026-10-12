#!/usr/bin/env python3
"""Build an iCalendar feed of US theatrical movie releases from TMDB.

Usage:
    TMDB_TOKEN=... python build_calendar.py --out public/movies.ics

Auth: set TMDB_TOKEN to your TMDB "API Read Access Token" (preferred), or
TMDB_API_KEY to the v3 API key. Standard library only — no dependencies.

This product uses the TMDB API but is not endorsed or certified by TMDB.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.themoviedb.org/3"
TMDB_MOVIE_URL = "https://www.themoviedb.org/movie/{id}"
IMDB_URL = "https://www.imdb.com/title/{id}/"
YOUTUBE_URL = "https://www.youtube.com/watch?v={key}"
POSTER_URL = "https://image.tmdb.org/t/p/w500{path}"
RELEASE_TYPE_LIMITED = 2
RELEASE_TYPE_THEATRICAL = 3
THEATRICAL_TYPES = {RELEASE_TYPE_LIMITED, RELEASE_TYPE_THEATRICAL}


# --------------------------------------------------------------------------- TMDB


class TMDB:
    def __init__(self, token: str | None, api_key: str | None):
        if not token and not api_key:
            sys.exit("Set TMDB_TOKEN (API Read Access Token) or TMDB_API_KEY.")
        self.token = token
        self.api_key = api_key

    def get(self, path: str, **params) -> dict:
        if self.api_key and not self.token:
            params["api_key"] = self.api_key
        url = f"{API}{path}?{urllib.parse.urlencode(params)}"
        headers = {"Accept": "application/json", "User-Agent": "movie-calendar/1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        for attempt in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    wait = int(e.headers.get("Retry-After", 0) or 0) or 2 ** attempt
                    time.sleep(wait)
                    continue
                raise
            except urllib.error.URLError:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"TMDB request kept failing: {path}")


def discover_ids(api: TMDB, cfg: dict, start: dt.date, end: dt.date) -> tuple[dict[int, float], list[dict]]:
    """Movies with a regional theatrical release in [start, end], most popular first.

    Returns ({id: popularity} for movies above min_popularity, [the first few
    movies just below it] for the build report).
    """
    rel = cfg["releases"]
    ids: dict[int, float] = {}
    below: list[dict] = []
    page, total = 1, 1
    while page <= min(total, rel["max_pages"]):
        data = api.get(
            "/discover/movie",
            region=rel["region"],
            with_release_type="|".join(str(t) for t in rel["release_types"]),
            **{"release_date.gte": start.isoformat(), "release_date.lte": end.isoformat()},
            sort_by="popularity.desc",
            include_adult="false",
            language="en-US",
            page=page,
        )
        total = data.get("total_pages", 1)
        results = data.get("results", [])
        for m in results:
            pop = m.get("popularity") or 0
            if pop >= rel["min_popularity"]:
                ids.setdefault(m["id"], pop)
            elif len(below) < 15 and m["id"] not in ids:
                below.append({"title": m.get("title", "?"), "popularity": pop})
        # Sorted by popularity: once a page dips below the floor, later pages will too.
        if not results or (results[-1].get("popularity") or 0) < rel["min_popularity"]:
            break
        page += 1
    return ids, below


def movie_details(api: TMDB, movie_id: int) -> dict:
    return api.get(
        f"/movie/{movie_id}",
        language="en-US",
        append_to_response="release_dates,credits,videos,external_ids",
    )


# ------------------------------------------------------------------ release logic


def _parse_date(s: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


def pick_release(movie: dict, rel: dict, start: dt.date, end: dt.date) -> tuple[dict | None, str]:
    """Choose the release-calendar entry for this movie.

    Returns ({"date", "type", "certification", "rerelease"}, "") when the movie
    belongs on the calendar, or (None, reason) when it's filtered out.
    """
    region, types = rel["region"], rel["release_types"]
    keywords = [k.lower() for k in rel.get("exclude_note_keywords", [])]

    entries = []
    for country in movie.get("release_dates", {}).get("results", []):
        if country.get("iso_3166_1") != region:
            continue
        for r in country.get("release_dates", []):
            d = _parse_date(r.get("release_date", ""))
            if d:
                entries.append({**r, "_date": d})
    entries.sort(key=lambda r: r["_date"])

    in_window = [r for r in entries if r.get("type") in types and start <= r["_date"] <= end]
    if not in_window:
        return None, f"no {region} theatrical date in window"

    # Drop individual release entries whose note marks them as events or limited runs
    # (e.g. "Fathom Events", "One night only"). A movie with a separate plain wide-release
    # entry still makes it.
    def flagged(r: dict) -> str:
        note = (r.get("note") or "").lower()
        return next((k for k in keywords if k in note), "")

    clean = [r for r in in_window if not flagged(r)]
    if not clean:
        return None, f'event/limited screening (note: "{in_window[0].get("note", "")}")'
    chosen = clean[0]

    # Earlier theatrical run in this region, or an original release years before → re-release.
    earlier = any(r.get("type") in THEATRICAL_TYPES and r["_date"] < chosen["_date"] - dt.timedelta(days=365) for r in entries)
    original = _parse_date(movie.get("release_date", ""))
    old_film = original is not None and (chosen["_date"] - original).days > 2 * 365
    note = (chosen.get("note") or "").lower()
    rerelease = earlier or old_film or any(k in note for k in ("re-release", "rerelease", "anniversary"))
    if rerelease and rel.get("exclude_rereleases"):
        return None, "re-release"

    cert = chosen.get("certification") or next((r.get("certification") for r in entries if r.get("certification")), "")
    return {"date": chosen["_date"], "type": chosen.get("type"), "certification": cert, "rerelease": rerelease}, ""


def trailer_url(movie: dict) -> str | None:
    vids = [v for v in movie.get("videos", {}).get("results", []) if v.get("site") == "YouTube" and v.get("type") == "Trailer"]
    vids.sort(key=lambda v: (not v.get("official"), v.get("iso_639_1") != "en", v.get("published_at") or ""))
    return YOUTUBE_URL.format(key=vids[0]["key"]) if vids else None


def format_runtime(minutes: int | None) -> str:
    if not minutes:
        return ""
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def build_event(movie: dict, release: dict, cfg: dict) -> dict:
    ev = cfg["events"]
    title = movie.get("title") or movie.get("original_title") or "Untitled"
    tags = []
    if release["rerelease"]:
        tags.append("Re-release")
    if release["type"] == RELEASE_TYPE_LIMITED:
        tags.append("Limited")
    summary = cfg["calendar"]["event_prefix"] + title + (f" ({', '.join(tags)})" if tags else "")

    crew = movie.get("credits", {}).get("crew", [])
    directors = [c["name"] for c in crew if c.get("job") == "Director"]
    cast = [c["name"] for c in movie.get("credits", {}).get("cast", [])[: ev["top_cast"]]]
    genres = [g["name"] for g in movie.get("genres", [])]

    facts = " · ".join(x for x in [release["certification"], format_runtime(movie.get("runtime")), ", ".join(genres)] if x)
    lines = []
    if facts:
        lines.append(facts)
    if directors:
        lines.append(f"Directed by {', '.join(directors)}")
    if cast:
        lines.append(f"Starring {', '.join(cast)}")
    if movie.get("overview"):
        lines += ["", movie["overview"].strip()]
    links = []
    if t := trailer_url(movie):
        links.append(f"Trailer: {t}")
    links.append(f"TMDB: {TMDB_MOVIE_URL.format(id=movie['id'])}")
    if imdb := movie.get("external_ids", {}).get("imdb_id") or movie.get("imdb_id"):
        links.append(f"IMDb: {IMDB_URL.format(id=imdb)}")
    if movie.get("poster_path"):
        links.append(f"Poster: {POSTER_URL.format(path=movie['poster_path'])}")
    lines += ["", *links, "", "Data from TMDB. This calendar is not endorsed or certified by TMDB."]

    return {
        "uid": f"tmdb-{movie['id']}@movie-calendar",
        "date": release["date"],
        "summary": summary,
        "description": "\n".join(lines),
        "url": TMDB_MOVIE_URL.format(id=movie["id"]),
        "categories": genres,
    }


# ------------------------------------------------------------------- iCalendar


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Fold to 75-octet lines (RFC 5545 §3.1) without splitting UTF-8 characters."""
    out, current, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # continuation lines start with a space
        if size + n > limit:
            out.append(current)
            current, size = "", 0
        current += ch
        size += n
    out.append(current)
    return "\r\n ".join(out)


def render_ics(events: list[dict], cfg: dict, now: dt.datetime) -> str:
    cal = cfg["calendar"]
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//movie-calendar//TMDB releases//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(cal['name'])}",
        f"NAME:{_escape(cal['name'])}",
        f"X-WR-CALDESC:{_escape(cal['description'])}",
        f"DESCRIPTION:{_escape(cal['description'])}",
        f"X-PUBLISHED-TTL:{cal['refresh_interval']}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{cal['refresh_interval']}",
    ]
    alert = cfg["events"].get("alert", "")
    for e in sorted(events, key=lambda e: (e["date"], e["summary"].lower())):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{e['date']:%Y%m%d}",
            f"DTEND;VALUE=DATE:{e['date'] + dt.timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_escape(e['summary'])}",
            f"DESCRIPTION:{_escape(e['description'])}",
            f"URL:{e['url']}",
        ]
        if e["categories"]:
            lines.append("CATEGORIES:" + ",".join(_escape(c) for c in e["categories"]))
        lines += ["TRANSP:TRANSPARENT", "X-MICROSOFT-CDO-BUSYSTATUS:FREE", "STATUS:CONFIRMED"]
        if alert:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(e['summary'])}", f"TRIGGER:{alert}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


# ------------------------------------------------------------------------- main


def _md(text: str) -> str:
    return text.replace("|", "\\|")


def render_report(kept: list[dict], dropped: list[dict], below: list[dict], rel: dict, start: dt.date, end: dt.date) -> str:
    """Markdown summary shown on the GitHub Actions run page, for tuning config.toml."""
    out = [
        f"## 🍿 {len(kept)} movies on the calendar",
        f"{rel['region']} releases {start} → {end} · min_popularity {rel['min_popularity']} · "
        f"exclude_rereleases {str(rel.get('exclude_rereleases', False)).lower()}",
        "",
        "| Date | Title | Popularity |",
        "|---|---|---:|",
    ]
    out += [f"| {k['date']} | {_md(k['title'])} | {k['popularity']:.1f} |" for k in sorted(kept, key=lambda k: k["date"])]
    if dropped:
        out += ["", f"### Filtered out ({len(dropped)})", "", "| Title | Popularity | Reason |", "|---|---:|---|"]
        out += [f"| {_md(d['title'])} | {d['popularity']:.1f} | {_md(d['reason'])} |" for d in sorted(dropped, key=lambda d: -d["popularity"])]
    if below:
        out += ["", f"### Just below min_popularity ({rel['min_popularity']})", "",
                ", ".join(f"{b['title']} ({b['popularity']:.1f})" for b in below)]
    return "\n".join(out) + "\n"


def build(api: TMDB, cfg: dict, today: dt.date, now: dt.datetime) -> tuple[str, list[dict], str]:
    rel = cfg["releases"]
    start = today - dt.timedelta(days=rel["days_back"])
    end = today + dt.timedelta(days=rel["days_ahead"])
    ids, below = discover_ids(api, cfg, start, end)
    print(f"Discovered {len(ids)} movies with {rel['region']} releases {start} → {end}", file=sys.stderr)

    events, kept, dropped = [], [], []
    for i, (movie_id, popularity) in enumerate(ids.items(), 1):
        try:
            movie = movie_details(api, movie_id)
        except Exception as exc:  # one bad title shouldn't sink the feed
            print(f"  skip {movie_id}: {exc}", file=sys.stderr)
            continue
        title = movie.get("title") or str(movie_id)
        release, reason = pick_release(movie, rel, start, end)
        if release:
            events.append(build_event(movie, release, cfg))
            kept.append({"title": title, "date": release["date"], "popularity": popularity})
        else:
            dropped.append({"title": title, "popularity": popularity, "reason": reason})
        if i % 40 == 0:
            time.sleep(1)  # stay well under TMDB's rate limit
    print(f"Wrote {len(events)} events, filtered out {len(dropped)}", file=sys.stderr)
    report = render_report(kept, dropped, below, rel, start, end)
    return render_ics(events, cfg, now), events, report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=Path(__file__).with_name("config.toml"), type=Path)
    p.add_argument("--out", default="public/movies.ics", type=Path)
    args = p.parse_args()

    cfg = tomllib.loads(args.config.read_text(encoding="utf-8"))
    api = TMDB(os.environ.get("TMDB_TOKEN"), os.environ.get("TMDB_API_KEY"))
    now = dt.datetime.now(dt.timezone.utc)
    ics, events, report = build(api, cfg, now.date(), now)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report)
    if not events:
        sys.exit("No events found — refusing to publish an empty calendar.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(ics, encoding="utf-8", newline="")
    print(f"Saved {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
