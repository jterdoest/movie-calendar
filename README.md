# 🍿 Movie Release Calendar

A subscribable calendar of US theatrical movie releases, built daily from [TMDB](https://www.themoviedb.org/).

Each release is an all-day event titled `🍿 Movie Title`, with the rating, runtime, genres, director, top cast, synopsis, and links to the trailer, TMDB, IMDb, and the poster. Events show as **Free**, so they won't block your availability.

## Subscribe

**Feed URL:** `https://<your-github-username>.github.io/movie-calendar/movies.ics`

- **iPhone/iPad:** Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add Subscribed Calendar
- **Mac:** Calendar → File → New Calendar Subscription. Set "Auto-refresh" to every hour or every day.
- **Outlook:** Add calendar → Subscribe from web

## One-time setup

1. **Get a TMDB token.** Create a free account at themoviedb.org. Then go to Settings → API, request an API key, and copy the **API Read Access Token** (the long one).
2. **Add it as a repo secret.** Go to Settings → Secrets and variables → Actions → New repository secret. Name it `TMDB_TOKEN` and paste the token.
3. **Turn on Pages.** Go to Settings → Pages → Build and deployment → Source: **GitHub Actions**.
4. **Run it.** Go to Actions → Build movie calendar → Run workflow. After that, it runs every morning by itself.

## Customize

Edit `config.toml` and push. The next build uses your changes.

| Setting | What it does |
|---|---|
| `event_prefix` | Text in front of every title (default `🍿 `) |
| `release_types` | `[3]` = wide theatrical only. `[2, 3]` adds limited releases, which are tagged "(Limited)". |
| `min_popularity` | Raise it to show fewer, bigger titles. Use `0` for everything. |
| `days_back` / `days_ahead` | How far back and ahead the calendar covers |
| `alert` | `""` = no alert. `"PT9H"` = 9 AM on release day. `"-PT15H"` = 9 AM the day before. |

Re-releases of older films are tagged "(Re-release)".

## Run locally

```sh
TMDB_TOKEN=your_token python3 build_calendar.py --out public/movies.ics
python3 -m unittest discover tests
```

Needs Python 3.11 or newer. There are no other dependencies.

---

This product uses the TMDB API but is not endorsed or certified by TMDB.
