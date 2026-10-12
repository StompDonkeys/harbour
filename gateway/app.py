#!/usr/bin/env python3
"""
Harbour gateway — a small API that sits on the Synology next to your *arr stack.

The Fire TV app talks only to this gateway. The gateway holds every API key and
password, talks to Radarr / Sonarr / Seerr / File Station / NZBGet / qBittorrent,
and hands back simple, TV-ready JSON (rows of cards, detail pages, file listings,
download queues).

Config: /config/config.json (see config.example.json). Override with HARBOUR_CONFIG.
"""
import hmac
import xml.etree.ElementTree as ET
import json
import logging
import os
import posixpath
import re
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import requests
from flask import Flask, Response, has_request_context, jsonify, request, stream_with_context

VERSION = "0.2.0"
CONFIG_PATH = os.environ.get("HARBOUR_CONFIG", "/config/config.json")
TMDB_IMG = "https://image.tmdb.org/t/p/"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("harbour")

app = Flask(__name__)
app.json.sort_keys = False

CFG = {}
SERVICES = {}  # (kind, inst) -> service object, in config order
FILES = None
HUB = None
PLEX = None
CLIENTS = {}   # download clients by id
POOL = ThreadPoolExecutor(max_workers=16)
DEFAULT_REPO = "StompDonkeys/harbour"


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------- helpers

def clean(d):
    return {k: v for k, v in d.items() if v is not None and v != ""}


def join(*parts, sep=" · "):
    return sep.join(str(p) for p in parts if p not in (None, "", 0))


def year_of(s):
    return s[:4] if s and len(s) >= 4 else None


def fmt_runtime(mins):
    if not mins:
        return None
    h, m = divmod(int(mins), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def fmt_size(b):
    b = float(b or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or unit == "TB":
            return f"{b:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{b:.1f} {unit}"
        b /= 1024


def parse_dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None and "T" not in s:
        return d  # plain date
    return d.astimezone()  # container TZ


def fmt_date(s, with_year=False):
    d = parse_dt(s)
    if not d:
        return None
    out = d.strftime("%a %d %b %Y" if with_year else "%a %d %b")
    return out.replace(" 0", " ")


def tmdb_size(url, size):
    if url and "image.tmdb.org/t/p/original/" in url:
        return url.replace("/t/p/original/", f"/t/p/{size}/")
    return url


def daily_rng(salt):
    return random.Random(datetime.now().toordinal() * 1000 + sum(map(ord, salt)))


def row(title, items, style="poster", key=None, more=False):
    """A row of cards. `key` + `more` let the app page in further items for this row."""
    r = {"title": title, "style": style, "items": items}
    if key:
        r.update(key=key, more=more)
    return r


def action(id_, label, style=None, confirm=None, arg=None, opens=None, choose=None):
    """A button on a detail page. `opens` makes the app open a screen (e.g. "releases") instead
    of calling the gateway; `choose` makes it ask the user to pick one option first."""
    return clean({"id": id_, "label": label, "style": style, "confirm": confirm, "arg": arg,
                  "opens": opens, "choose": choose})


def facts(*pairs):
    return [{"label": k, "value": str(v)} for k, v in pairs if v not in (None, "", [], 0)]


def dedupe(items, limit=30):
    seen, out = set(), []
    for it in items:
        if it["key"] in seen:
            continue
        seen.add(it["key"])
        out.append(it)
        if len(out) >= limit:
            break
    return out


def safe_row(fn, label):
    try:
        return fn()
    except Exception as e:  # one broken row shouldn't kill the page
        log.warning("row %s failed: %s", label, e)
        return []



# ---------------------------------------------------------------- caching

class HomeCache:
    """
    Serve a service's home page from memory. A fresh copy (< FRESH s) is returned as-is;
    an older one is returned immediately while a background thread refreshes it; only a
    cold cache makes the TV wait — and concurrent cold requests share one computation.
    """
    FRESH = 45
    MAX_AGE = 1800

    def _hc_init(self):
        self._home = None
        self._home_lock = threading.Lock()
        self._refreshing = False

    def home_cached(self):
        h, now = self._home, time.time()
        if h and now - h[0] < self.FRESH:
            return h[1]
        if h and now - h[0] < self.MAX_AGE:
            if not self._refreshing:
                self._refreshing = True
                threading.Thread(target=self.refresh_home, daemon=True).start()
            return h[1]
        with self._home_lock:
            h = self._home
            if h and time.time() - h[0] < self.FRESH:
                return h[1]
            val = self.home()
            self._home = (time.time(), val)
            return val

    def refresh_home(self):
        try:
            with self._home_lock:
                val = self.home()
                self._home = (time.time(), val)
        except Exception as e:
            log.warning("background refresh of %s failed: %s", getattr(self, "name", "?"), e)
        finally:
            self._refreshing = False

    def drop_home(self):
        self._home = None


def par(**fns):
    """Run zero-argument callables concurrently; returns {name: result}."""
    futs = {k: POOL.submit(f) for k, f in fns.items()}
    return {k: f.result() for k, f in futs.items()}


def safe(fn, label, default):
    try:
        return fn()
    except Exception as e:
        log.warning("%s failed: %s", label, e)
        return default


def fmt_age(r):
    days, hours = r.get("age"), r.get("ageHours")
    if days and days >= 1:
        return f"{int(days)}d"
    if hours is not None:
        return f"{int(hours)}h" if hours >= 1 else f"{int(hours * 60)}m"
    return None


# ---------------------------------------------------------------- *arr base

class Arr(HomeCache):
    kind = ""
    default_icon = ""

    def __init__(self, conf):
        self.inst = conf["id"]
        self.name = conf.get("name", self.inst)
        self.icon = conf.get("icon", self.default_icon)
        self.url = conf["url"].rstrip("/")
        self.key = conf["api_key"]
        self._cache = {}
        self._lock = threading.Lock()
        self._hc_init()

    def _req(self, method, path, **kw):
        kw.setdefault("timeout", 30)
        r = requests.request(method, f"{self.url}/api/v3{path}",
                             headers={"X-Api-Key": self.key}, **kw)
        if r.status_code >= 400:
            msg = r.text[:200]
            try:
                body = r.json()
                if isinstance(body, dict):
                    msg = body.get("message") or msg
                elif isinstance(body, list) and body and isinstance(body[0], dict):
                    msg = body[0].get("errorMessage") or msg
            except ValueError:
                pass
            raise ApiError(f"{self.name}: {msg}", 502)
        return r.json() if r.content else {}

    def get(self, path, **params):
        return self._req("GET", path, params=params)

    def post(self, path, body):
        return self._req("POST", path, json=body)

    def put(self, path, body, **params):
        return self._req("PUT", path, json=body, params=params)

    def delete(self, path, **params):
        return self._req("DELETE", path, params=params)

    def command(self, body):
        return self.post("/command", body)

    def cached(self, key, ttl, fn):
        with self._lock:
            hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (time.time(), val)
        return val

    def invalidate(self):
        with self._lock:
            self._cache.clear()
        self.drop_home()

    def image(self, images, cover, size):
        for im in images or []:
            if im.get("coverType") != cover:
                continue
            if im.get("remoteUrl"):
                return tmdb_size(im["remoteUrl"], size)
            if im.get("url"):
                return f"/img/{self.kind}/{self.inst}/{im['url'].split('?')[0].lstrip('/')}"
        return None

    def info(self):
        return {"kind": self.kind, "inst": self.inst, "name": self.name, "icon": self.icon}

    # -- interactive ("manual") search, shared by Radarr and Sonarr
    def release_list(self, params):
        rels = self._req("GET", "/release", params=params, timeout=180)
        out = []
        for r in rels or []:
            rej = [x if isinstance(x, str) else (x.get("reason") or x.get("message") or str(x))
                   for x in r.get("rejections") or []]
            torrent = r.get("protocol") == "torrent"
            out.append(clean({
                "guid": r.get("guid"),
                "indexerId": int(r.get("indexerId") or 0),
                "title": r.get("title") or "?",
                "quality": ((r.get("quality") or {}).get("quality") or {}).get("name"),
                "size": int(r.get("size") or 0),
                "indexer": r.get("indexer"),
                "age": fmt_age(r),
                "peers": f"{r.get('seeders') or 0} seeders" if torrent else None,
                "protocol": "Torrent" if torrent else "Usenet",
                "approved": bool(r.get("approved")),
                "rejections": rej,
                "score": r.get("customFormatScore"),
            }))
        out.sort(key=lambda x: (not x["approved"], -(x.get("score") or 0), -x["size"]))
        return {"items": out}

    def grab(self, guid, indexer_id):
        if not guid:
            raise ApiError("Missing release")
        self._req("POST", "/release", json={"guid": guid, "indexerId": int(indexer_id or 0)}, timeout=60)
        self.invalidate()
        return {"message": "Sent to download client ✓"}


# ---------------------------------------------------------------- Radarr

class Radarr(Arr):
    kind = "radarr"
    default_icon = "movie"

    def movies(self):
        return self.cached("movies", 60, lambda: self.get("/movie"))

    @staticmethod
    def quality(m):
        q = (((m.get("movieFile") or {}).get("quality") or {}).get("quality") or {})
        return q.get("name")

    @staticmethod
    def rating(m):
        r = m.get("ratings") or {}
        for src in ("imdb", "tmdb"):
            v = (r.get(src) or {}).get("value")
            if v:
                return float(v)
        return float(r.get("value") or 0)

    def card(self, m, badge=None, tone=None, progress=None, subtitle=None, key=None):
        if badge is None:
            if m.get("hasFile"):
                badge, tone = self.quality(m), "ok"
            elif not m.get("monitored"):
                badge, tone = "Unmonitored", "muted"
            elif m.get("isAvailable"):
                badge, tone = "Missing", "warn"
            else:
                badge, tone = "Coming soon", "info"
        if subtitle is None:
            subtitle = join(m.get("year"), fmt_runtime(m.get("runtime")), m.get("certification"))
        imgs = m.get("images")
        return clean({
            "key": key or f"radarr:{self.inst}:{m['id']}",
            "id": str(m["id"]),
            "ref": f"movie-{m['tmdbId']}" if m.get("tmdbId") else None,
            "title": m.get("title") or "Untitled",
            "subtitle": subtitle,
            "poster": self.image(imgs, "poster", "w342"),
            "backdrop": self.image(imgs, "fanart", "w1280"),
            "overview": m.get("overview"),
            "badge": badge,
            "badgeTone": tone,
            "progress": progress,
        })

    def home(self):
        today = datetime.now(timezone.utc).date()
        d = par(
            movies=self.movies,
            queue=lambda: safe(lambda: self.get("/queue", page=1, pageSize=100, includeMovie="true")
                               .get("records", []), "radarr queue", []),
            cal=lambda: safe(lambda: self.get("/calendar", start=today.isoformat(),
                                              end=(today + timedelta(days=120)).isoformat(),
                                              unmonitored="false"), "radarr calendar", []),
        )
        movies = d["movies"]
        by_id = {m["id"]: m for m in movies}
        rows = []

        dl = []
        for r in d["queue"]:
            m = r.get("movie") or by_id.get(r.get("movieId"))
            if not m:
                continue
            size, left = r.get("size") or 0, r.get("sizeleft") or 0
            prog = max(0.0, min(1.0, 1 - left / size)) if size else 0.0
            state = (r.get("trackedDownloadState") or "").lower()
            status = (r.get("status") or "").lower()
            if state in ("importpending", "importing"):
                badge = "Importing"
            elif status == "downloading":
                badge = f"{prog * 100:.0f}%"
            else:
                badge = status.title() or "Queued"
            tone = "warn" if (r.get("trackedDownloadStatus") or "").lower() in ("warning", "error") else "info"
            sub = join(f"{prog * 100:.0f}%", f"{r['timeleft']} left" if r.get("timeleft") else None,
                       ((r.get("quality") or {}).get("quality") or {}).get("name"))
            dl.append(self.card(m, badge, tone, round(prog, 3), sub))

        labelled = []
        for m in d["cal"]:
            if m.get("hasFile"):
                continue
            opts = [(m.get(k), lbl) for k, lbl in
                    (("digitalRelease", "Digital"), ("physicalRelease", "Disc"), ("inCinemas", "Cinemas"))]
            opts = sorted((dt, lbl) for dt, lbl in opts if dt and dt[:10] >= today.isoformat())
            if opts:
                labelled.append((opts[0][0], opts[0][1], m))
        labelled.sort(key=lambda x: x[0])

        have = [m for m in movies if m.get("hasFile")]
        added_key = lambda m: (m.get("movieFile") or {}).get("dateAdded") or m.get("added") or ""
        recent = sorted(have, key=added_key, reverse=True)

        rows.append(row("Downloading", dedupe(dl), "landscape"))
        rows.append(row("Recently added", [self.card(m) for m in recent[:30]]))
        rows.append(row("Coming soon", dedupe([self.card(m, subtitle=join(lbl, fmt_date(dt)))
                                               for dt, lbl, m in labelled])))
        missing = [m for m in movies if m.get("monitored") and not m.get("hasFile") and m.get("isAvailable")]
        missing.sort(key=lambda m: m.get("added") or "", reverse=True)
        rows.append(row("Missing", [self.card(m) for m in missing[:30]]))
        top = sorted(have, key=self.rating, reverse=True)
        rows.append(row("Top rated", [self.card(m) for m in top[:30]]))
        genres = Counter(g for m in have for g in (m.get("genres") or []))
        for g, _ in genres.most_common(6):
            pool = [m for m in have if g in (m.get("genres") or [])]
            daily_rng(g).shuffle(pool)
            rows.append(row(g, [self.card(m) for m in pool[:30]]))

        rows = [r for r in rows if r["items"]]
        cands = [c for c in (self.card(m) for m in recent[:12]) if c.get("backdrop")]
        hero = random.Random(int(time.time() // 3600)).choice(cands) if cands else None
        return {"hero": hero, "rows": rows}

    def search(self, q):
        q = (q or "").strip().lower()
        ms = [m for m in self.movies() if not q or q in (m.get("title") or "").lower()]
        ms.sort(key=lambda m: (m.get("sortTitle") or m.get("title") or "").lower())
        return {"items": [self.card(m) for m in ms]}

    def detail(self, item_id):
        m = self.get(f"/movie/{int(item_id)}")
        mf = m.get("movieFile") or {}
        mi = mf.get("mediaInfo") or {}
        video = join(mi.get("resolution"), mi.get("videoCodec"), mi.get("videoDynamicRangeType"))
        audio = join(mi.get("audioCodec"), mi.get("audioChannels"), mi.get("audioLanguages"))
        r = self.rating(m)
        status = ("Downloaded" if m.get("hasFile") else
                  "Missing" if m.get("isAvailable") else (m.get("status") or "").title())
        f = facts(
            ("Status", status),
            ("Quality", self.quality(m)),
            ("Size", fmt_size(m.get("sizeOnDisk")) if m.get("hasFile") else None),
            ("Video", video), ("Audio", audio),
            ("Rating", f"{r:.1f}" if r else None),
            ("Genres", ", ".join(m.get("genres") or [])),
            ("Studio", m.get("studio")),
            ("In cinemas", fmt_date(m.get("inCinemas"), True)),
            ("Digital", fmt_date(m.get("digitalRelease"), True)),
            ("Monitored", "Yes" if m.get("monitored") else "No"),
            ("Added", fmt_date(m.get("added"), True)),
            ("Path", m.get("path")),
        )
        acts = [
            *([action("play", "Play", "primary", opens="play", arg=f"movie-{m['tmdbId']}")]
              if is_v2() and PLEX and m.get("hasFile") and m.get("tmdbId") else []),
            action("search", "Auto search", "primary"),
            action("releases", "Manual search", opens="releases", arg="movie"),
            *([action("info", "Cast & more", opens="item", arg=f"hub/hub/movie-{m['tmdbId']}")]
              if HUB and m.get("tmdbId") else []),
            action("unmonitor", "Unmonitor") if m.get("monitored") else action("monitor", "Monitor"),
            action("delete", "Remove", confirm=f"Remove “{m.get('title')}” from {self.name}? Files stay on the NAS."),
            action("delete_files", "Delete + files", "danger",
                   confirm=f"Delete “{m.get('title')}” AND its files from the NAS? This can't be undone."),
        ]
        return {"item": self.card(m), "facts": f, "actions": acts}

    def releases(self, item_id, arg=None):
        return self.release_list({"movieId": int(item_id)})

    def act(self, item_id, act, arg=None, choice=None):
        mid = int(item_id)
        if act == "search":
            self.command({"name": "MoviesSearch", "movieIds": [mid]})
            msg = "Searching for releases…"
        elif act in ("monitor", "unmonitor"):
            self.put("/movie/editor", {"movieIds": [mid], "monitored": act == "monitor"})
            msg = "Monitoring on" if act == "monitor" else "Monitoring off"
        elif act in ("delete", "delete_files"):
            self.delete(f"/movie/{mid}", deleteFiles=str(act == "delete_files").lower(),
                        addImportExclusion="false")
            self.invalidate()
            return {"message": "Deleted" if act == "delete_files" else "Removed", "close": True}
        else:
            raise ApiError(f"Unknown action {act}")
        self.invalidate()
        return {"message": msg, "refresh": True}


# ---------------------------------------------------------------- Sonarr

class Sonarr(Arr):
    kind = "sonarr"
    default_icon = "tv"

    def series(self):
        return self.cached("series", 60, lambda: self.get("/series"))

    def card(self, s, badge=None, tone=None, progress=None, subtitle=None, key=None):
        st = s.get("statistics") or {}
        have, total = st.get("episodeFileCount", 0), st.get("episodeCount", 0)
        if badge is None:
            if total and have >= total:
                badge, tone = ("Complete" if s.get("status") == "ended" else "Up to date"), "ok"
            elif total:
                badge, tone = f"{have}/{total} eps", ("warn" if s.get("monitored") else "muted")
            else:
                badge, tone = "Upcoming", "info"
        if subtitle is None:
            n = st.get("seasonCount") or len([x for x in s.get("seasons") or [] if x.get("seasonNumber", 0) > 0])
            subtitle = join(s.get("year"), f"{n} season{'s' if n != 1 else ''}" if n else None, s.get("network"))
        imgs = s.get("images")
        return clean({
            "key": key or f"sonarr:{self.inst}:{s['id']}",
            "id": str(s["id"]),
            "ref": f"tv-{s['tmdbId']}" if s.get("tmdbId") else None,
            "title": s.get("title") or "Untitled",
            "subtitle": subtitle,
            "poster": self.image(imgs, "poster", "w342"),
            "backdrop": self.image(imgs, "fanart", "w1280"),
            "overview": s.get("overview"),
            "badge": badge,
            "badgeTone": tone,
            "progress": progress,
        })

    @staticmethod
    def sxe(e):
        return f"S{e.get('seasonNumber', 0):02d}E{e.get('episodeNumber', 0):02d}"

    def home(self):
        today = datetime.now(timezone.utc).date()
        d = par(
            series=self.series,
            queue=lambda: safe(lambda: self.get("/queue", page=1, pageSize=100, includeSeries="true",
                                                includeEpisode="true").get("records", []), "sonarr queue", []),
            cal=lambda: safe(lambda: self.get("/calendar", start=today.isoformat(),
                                              end=(today + timedelta(days=14)).isoformat(),
                                              includeSeries="true"), "sonarr calendar", []),
            missing=lambda: safe(lambda: self.get("/wanted/missing", page=1, pageSize=100)
                                 .get("records", []), "sonarr missing", []),
        )
        series = d["series"]
        by_id = {s["id"]: s for s in series}
        rows = []

        dl = []
        for r in d["queue"]:
            s = r.get("series") or by_id.get(r.get("seriesId"))
            if not s:
                continue
            e = r.get("episode") or {}
            size, left = r.get("size") or 0, r.get("sizeleft") or 0
            prog = max(0.0, min(1.0, 1 - left / size)) if size else 0.0
            state = (r.get("trackedDownloadState") or "").lower()
            badge = "Importing" if state in ("importpending", "importing") else f"{prog * 100:.0f}%"
            sub = join(self.sxe(e) if e else None, e.get("title"),
                       f"{r['timeleft']} left" if r.get("timeleft") else None)
            dl.append(self.card(s, badge, "info", round(prog, 3), sub, key=f"sonarr:{self.inst}:q{r.get('id')}"))

        eps = sorted(d["cal"], key=lambda e: e.get("airDateUtc") or "")
        airing = []
        for e in eps:
            s = e.get("series") or by_id.get(e.get("seriesId"))
            if s:
                airing.append(self.card(s, badge=fmt_date(e.get("airDateUtc")), tone="info",
                                        subtitle=join(self.sxe(e), e.get("title")),
                                        key=f"sonarr:{self.inst}:e{e.get('id')}"))

        counts, order = Counter(), []
        for e in d["missing"]:
            sid = e.get("seriesId")
            if sid not in counts:
                order.append(sid)
            counts[sid] += 1
        missing = [self.card(by_id[sid], badge=f"{counts[sid]} missing", tone="warn")
                   for sid in order if sid in by_id]

        rows.append(row("Downloading", dedupe(dl), "landscape"))
        rows.append(row("Airing this fortnight", dedupe(airing), "landscape"))
        recent = sorted(series, key=lambda s: s.get("added") or "", reverse=True)
        rows.append(row("Recently added", [self.card(s) for s in recent[:30]]))
        returning = [s for s in series if s.get("status") == "continuing" and s.get("nextAiring")]
        returning.sort(key=lambda s: s.get("nextAiring"))
        rows.append(row("Returning soon", [self.card(s, badge=fmt_date(s.get("nextAiring")), tone="info")
                                           for s in returning[:30]]))
        rows.append(row("Missing episodes", dedupe(missing)))
        genres = Counter(g for s in series for g in (s.get("genres") or []))
        for g, _ in genres.most_common(6):
            pool = [s for s in series if g in (s.get("genres") or [])]
            daily_rng(g).shuffle(pool)
            rows.append(row(g, [self.card(s) for s in pool[:30]]))

        rows = [r for r in rows if r["items"]]
        cands = [c for c in (self.card(s) for s in recent[:12]) if c.get("backdrop")]
        hero = random.Random(int(time.time() // 3600)).choice(cands) if cands else None
        return {"hero": hero, "rows": rows}

    def search(self, q):
        q = (q or "").strip().lower()
        ss = [s for s in self.series() if not q or q in (s.get("title") or "").lower()]
        ss.sort(key=lambda s: (s.get("sortTitle") or s.get("title") or "").lower())
        return {"items": [self.card(s) for s in ss]}

    def detail(self, item_id):
        sid = int(item_id)
        d = par(series=lambda: self.get(f"/series/{sid}"), eps=lambda: self.get("/episode", seriesId=sid))
        s, eps = d["series"], d["eps"]
        st = s.get("statistics") or {}
        now = datetime.now(timezone.utc)
        f = facts(
            ("Status", (s.get("status") or "").title()),
            ("Network", s.get("network")),
            ("Episodes", f"{st.get('episodeFileCount', 0)} of {st.get('episodeCount', 0)} downloaded"),
            ("Size", fmt_size(st.get("sizeOnDisk")) if st.get("sizeOnDisk") else None),
            ("Next airing", fmt_date(s.get("nextAiring"), True)),
            ("Rating", (s.get("ratings") or {}).get("value")),
            ("Genres", ", ".join(s.get("genres") or [])),
            ("Monitored", "Yes" if s.get("monitored") else "No"),
            ("Added", fmt_date(s.get("added"), True)),
            ("Path", s.get("path")),
        )
        seasons = []
        snums = sorted((x for x in s.get("seasons") or []), key=lambda x: (x["seasonNumber"] == 0, x["seasonNumber"]))
        for sn in snums:
            n = sn["seasonNumber"]
            seps = sorted((e for e in eps if e.get("seasonNumber") == n), key=lambda e: e.get("episodeNumber", 0))
            if not seps:
                continue
            have = sum(1 for e in seps if e.get("hasFile"))
            episodes = []
            for e in seps:
                aired = parse_dt(e.get("airDateUtc"))
                state = ("Downloaded" if e.get("hasFile") else
                         "Missing" if aired and aired <= now else "Not aired")
                eid = str(e["id"])
                label = f"{self.sxe(e)} “{e.get('title') or 'TBA'}”"
                ep_acts = [
                    *([action("play", "Play", "primary", opens="play",
                              arg=f"tv-{s['tmdbId']}:s{n}e{e.get('episodeNumber', 0)}")]
                      if is_v2() and PLEX and e.get("hasFile") and s.get("tmdbId") else []),
                    action("episode_search", "Auto search", "primary", arg=eid),
                    action("releases", "Manual search", opens="releases", arg=f"episode:{eid}"),
                    action("episode_unmonitor", "Unmonitor", arg=eid) if e.get("monitored")
                    else action("episode_monitor", "Monitor", arg=eid),
                ]
                if e.get("hasFile") and e.get("episodeFileId"):
                    ep_acts.append(action("episode_delete_file", "Delete file", "danger",
                                          confirm=f"Delete the file for {label}? This can't be undone.",
                                          arg=str(e["episodeFileId"])))
                episodes.append(clean({
                    "id": eid,
                    "label": f"E{e.get('episodeNumber', 0):02d}",
                    "title": e.get("title") or "TBA",
                    "subtitle": join(fmt_date(e.get("airDateUtc"), True) or "TBA", state,
                                     None if e.get("monitored") else "unmonitored"),
                    "hasFile": bool(e.get("hasFile")),
                    "actions": ep_acts,
                }))
            seasons.append(clean({
                "number": n,
                "label": "Specials" if n == 0 else f"Season {n}",
                "subtitle": join(f"{have}/{len(seps)} episodes", None if sn.get("monitored") else "unmonitored"),
                "actions": [
                    action("season_search", "Auto search season", "primary", arg=str(n)),
                    action("releases", "Manual search", opens="releases", arg=f"season:{n}"),
                    action("season_unmonitor", "Unmonitor season", arg=str(n)) if sn.get("monitored")
                    else action("season_monitor", "Monitor season", arg=str(n)),
                ],
                "episodes": episodes,
            }))
        acts = [
            *([action("play", "Play", "primary", opens="play", arg=f"tv-{s['tmdbId']}")]
              if is_v2() and PLEX and st.get("episodeFileCount") and s.get("tmdbId") else []),
            action("search", "Search all missing", "primary"),
            *([action("info", "Cast & more", opens="item", arg=f"hub/hub/tv-{s['tmdbId']}")]
              if HUB and s.get("tmdbId") else []),
            action("unmonitor", "Unmonitor") if s.get("monitored") else action("monitor", "Monitor"),
            action("delete", "Remove", confirm=f"Remove “{s.get('title')}” from {self.name}? Files stay on the NAS."),
            action("delete_files", "Delete + files", "danger",
                   confirm=f"Delete “{s.get('title')}” AND all its files from the NAS? This can't be undone."),
        ]
        return {"item": self.card(s), "facts": f, "actions": acts, "seasons": seasons}

    def releases(self, item_id, arg=None):
        kind, _, val = (arg or "").partition(":")
        if kind == "season" and val.lstrip("-").isdigit():
            return self.release_list({"seriesId": int(item_id), "seasonNumber": int(val)})
        if kind == "episode" and val.isdigit():
            return self.release_list({"episodeId": int(val)})
        raise ApiError("Pick a season or an episode to search")

    def act(self, item_id, act, arg=None, choice=None):
        sid = int(item_id)
        if act == "search":
            self.command({"name": "SeriesSearch", "seriesId": sid})
            msg = "Searching for missing episodes…"
        elif act == "season_search":
            self.command({"name": "SeasonSearch", "seriesId": sid, "seasonNumber": int(arg)})
            msg = f"Searching season {arg}…"
        elif act == "episode_search":
            self.command({"name": "EpisodeSearch", "episodeIds": [int(arg)]})
            return {"message": "Searching for episode…", "refresh": False}
        elif act in ("episode_monitor", "episode_unmonitor"):
            self.put("/episode/monitor", {"episodeIds": [int(arg)], "monitored": act == "episode_monitor"})
            msg = "Episode monitored" if act == "episode_monitor" else "Episode unmonitored"
        elif act == "episode_delete_file":
            self.delete(f"/episodefile/{int(arg)}")
            msg = "Episode file deleted"
        elif act in ("season_monitor", "season_unmonitor"):
            s = self.get(f"/series/{sid}")
            for x in s.get("seasons") or []:
                if x.get("seasonNumber") == int(arg):
                    x["monitored"] = act == "season_monitor"
            self.put(f"/series/{sid}", s)
            msg = f"Season {arg} " + ("monitored" if act == "season_monitor" else "unmonitored")
        elif act in ("monitor", "unmonitor"):
            s = self.get(f"/series/{sid}")
            s["monitored"] = act == "monitor"
            self.put(f"/series/{sid}", s)
            msg = "Monitoring on" if act == "monitor" else "Monitoring off"
        elif act in ("delete", "delete_files"):
            self.delete(f"/series/{sid}", deleteFiles=str(act == "delete_files").lower(),
                        addImportListExclusion="false")
            self.invalidate()
            return {"message": "Deleted" if act == "delete_files" else "Removed", "close": True}
        else:
            raise ApiError(f"Unknown action {act}")
        self.invalidate()
        return {"message": msg, "refresh": True}


# ---------------------------------------------------------------- Seerr

SEERR_STATUS = {2: ("Pending", "info"), 3: ("Requested", "info"),
                4: ("Partly available", "warn"), 5: ("Available", "ok")}
REQUESTED = (2, 3, 5)


class Seerr(HomeCache):
    kind = "seerr"
    FRESH = 300  # discovery lists barely change

    def __init__(self, conf):
        self.inst = conf.get("id", "seerr")
        self.name = conf.get("name", "Discover")
        self.icon = conf.get("icon", "discover")
        self.url = conf["url"].rstrip("/")
        self.key = conf["api_key"]
        # 4K options appear only when Seerr has a 4K server for that media type.
        # "four_k": false in config hides them entirely.
        self.four_k = conf.get("four_k", True) is not False
        self._cache = {}
        self._hc_init()

    def info(self):
        return {"kind": self.kind, "inst": self.inst, "name": self.name, "icon": self.icon}

    def _req(self, method, path, params=None, body=None, raw_query=None):
        url = f"{self.url}/api/v1{path}" + (f"?{raw_query}" if raw_query else "")
        r = requests.request(method, url, headers={"X-Api-Key": self.key},
                             params=params, json=body, timeout=30)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message") or r.text[:200]
            except ValueError:
                msg = r.text[:200]
            raise ApiError(f"{self.name}: {msg}", 502)
        return r.json() if r.content else {}

    def cached(self, key, ttl, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        self._cache[key] = (time.time(), val)
        return val

    def invalidate(self):
        self._cache.clear()
        self.drop_home()

    # -- Radarr/Sonarr servers configured inside Seerr (for 4K + root folders)
    def servers(self, kind):
        return safe(lambda: self.cached(f"srv-{kind}", 300, lambda: self._req("GET", f"/service/{kind}")),
                    f"seerr {kind} servers", []) or []

    def has_4k(self, mt):
        kind = "radarr" if mt == "movie" else "sonarr"
        return self.four_k and any(s.get("is4k") for s in self.servers(kind))

    def folder_choice(self, mt, is4k):
        kind = "radarr" if mt == "movie" else "sonarr"
        srvs = [s for s in self.servers(kind) if bool(s.get("is4k")) == is4k]
        if not srvs:
            return None
        srv = next((s for s in srvs if s.get("isDefault")), srvs[0])
        det = safe(lambda: self.cached(f"srvd-{kind}-{srv['id']}", 300,
                                       lambda: self._req("GET", f"/service/{kind}/{srv['id']}")),
                   f"seerr {kind} server detail", {}) or {}
        default = srv.get("activeDirectory")
        folders = sorted(det.get("rootFolders") or [], key=lambda f: (f.get("path") != default, f.get("path") or ""))
        opts = []
        for rf in folders:
            path = rf.get("path")
            if not path:
                continue
            label = path + (f"  ·  {fmt_size(rf['freeSpace'])} free" if rf.get("freeSpace") else "") \
                + ("  (default)" if path == default else "")
            opts.append({"label": label, "value": f"{srv['id']}|{srv.get('activeProfileId') or ''}|{path}"})
        if not opts:
            return None
        return {"title": f"Save to which folder? — {srv.get('name') or kind.title()}", "options": opts}

    def card(self, r, mt=None):
        mt = mt or r.get("mediaType")
        if mt not in ("movie", "tv") or not r.get("id"):
            return None
        date = r.get("releaseDate") or r.get("firstAirDate")
        badge, tone = SEERR_STATUS.get((r.get("mediaInfo") or {}).get("status"), (None, None))
        vote = r.get("voteAverage")
        return clean({
            "key": f"seerr:{mt}-{r['id']}",
            "id": f"{mt}-{r['id']}",
            "ref": f"{mt}-{r['id']}",
            "title": r.get("title") or r.get("name") or "Untitled",
            "subtitle": join(year_of(date), "Movie" if mt == "movie" else "Series",
                             f"★ {vote:.1f}" if vote else None),
            "poster": f"{TMDB_IMG}w342{r['posterPath']}" if r.get("posterPath") else None,
            "backdrop": f"{TMDB_IMG}w1280{r['backdropPath']}" if r.get("backdropPath") else None,
            "overview": r.get("overview"),
            "badge": badge,
            "badgeTone": tone,
        })

    def cards(self, results, mt=None):
        return dedupe([c for c in (self.card(r, mt) for r in results or []) if c], 40)

    # Discovery rows: key -> (title, Seerr path, media type)
    ROWS = {
        "trending": ("Trending", "/discover/trending", None),
        "movies": ("Popular movies", "/discover/movies", "movie"),
        "tv": ("Popular TV", "/discover/tv", "tv"),
        "movies-upcoming": ("Upcoming movies", "/discover/movies/upcoming", "movie"),
        "tv-upcoming": ("Upcoming TV", "/discover/tv/upcoming", "tv"),
    }

    def row_page(self, key, page=1):
        if key not in self.ROWS:
            raise ApiError("Unknown row")
        _, path, mt = self.ROWS[key]
        page = max(1, int(page or 1))
        res = self._req("GET", path, {"page": page})
        total = int(res.get("totalPages") or page)
        return {"items": self.cards(res.get("results"), mt), "page": page, "hasMore": page < min(total, 500)}

    def home(self):
        def fetch(key):
            return lambda: safe(lambda: self.row_page(key, 1), self.ROWS[key][0], {"items": [], "hasMore": False})

        got = par(**{k: fetch(k) for k in self.ROWS})
        rows = [row(self.ROWS[k][0], got[k]["items"], key=k, more=got[k]["hasMore"]) for k in self.ROWS]
        rows = [r for r in rows if r["items"]]
        hero = next((i for r in rows for i in r["items"] if i.get("backdrop")), None)
        return {"hero": hero, "rows": rows}

    def search(self, q):
        q = (q or "").strip()
        if not q:
            return {"items": []}
        # Seerr is fussy about '+' for spaces, so encode the query ourselves.
        res = self._req("GET", "/search", raw_query=f"query={quote(q, safe='')}&page=1")
        return {"items": self.cards(res.get("results"))}

    def _split(self, item_id):
        mt, _, tid = item_id.partition("-")
        if mt not in ("movie", "tv") or not tid.isdigit():
            raise ApiError("Bad item id")
        return mt, int(tid)

    def detail(self, item_id):
        if HUB is not None:  # the Home hub builds the rich page (cast, recommendations, Plex…)
            return HUB.detail(item_id)
        mt, tid = self._split(item_id)
        return self.detail_from(mt, tid, self._req("GET", f"/{mt}/{tid}"))

    def detail_from(self, mt, tid, d):
        mi = d.get("mediaInfo") or {}
        st, st4k = mi.get("status"), mi.get("status4k")
        genres = ", ".join(g.get("name", "") for g in d.get("genres") or [])
        if mt == "movie":
            studio = ((d.get("productionCompanies") or [{}])[0] or {}).get("name")
            f = facts(("Availability", SEERR_STATUS.get(st, ("Not requested",))[0]),
                      ("Released", fmt_date(d.get("releaseDate"), True)),
                      ("Runtime", fmt_runtime(d.get("runtime"))),
                      ("Rating", f"{d['voteAverage']:.1f}" if d.get("voteAverage") else None),
                      ("Genres", genres), ("Studio", studio), ("Status", d.get("status")))
        else:
            network = ((d.get("networks") or [{}])[0] or {}).get("name")
            f = facts(("Availability", SEERR_STATUS.get(st, ("Not requested",))[0]),
                      ("First aired", fmt_date(d.get("firstAirDate"), True)),
                      ("Seasons", d.get("numberOfSeasons")),
                      ("Rating", f"{d['voteAverage']:.1f}" if d.get("voteAverage") else None),
                      ("Genres", genres), ("Network", network), ("Status", d.get("status")))
        f += self.library_facts(mt, d)
        choose = self.folder_choice(mt, False)
        acts = []
        if st not in REQUESTED:
            acts.append(action("request", "Request" if mt == "movie" else "Request all seasons", "primary",
                               choose=choose))
        if self.has_4k(mt) and st4k not in REQUESTED:
            acts.append(action("request4k", "Request in 4K", choose=self.folder_choice(mt, True)))
        seasons = []
        if mt == "tv":
            sstat = {x.get("seasonNumber"): x.get("status") for x in mi.get("seasons") or []}
            for s in d.get("seasons") or []:
                n = s.get("seasonNumber", 0)
                if n == 0:
                    continue
                ss = sstat.get(n)
                seasons.append(clean({
                    "number": n,
                    "label": s.get("name") or f"Season {n}",
                    "subtitle": join(f"{s.get('episodeCount', 0)} episodes",
                                     SEERR_STATUS.get(ss, ("Not requested",))[0]),
                    "actions": [] if ss in REQUESTED else
                    [action("request_season", f"Request season {n}", "primary", arg=str(n), choose=choose)],
                }))
        for svc_, m in self.library_matches(mt, d):
            acts.append(action("open", f"Open in {svc_.name}", opens="item", arg=f"{svc_.kind}/{svc_.inst}/{m['id']}"))
        if mi.get("id") and (st or mi.get("status4k") or mi.get("requests")):
            title = d.get("title") or d.get("name") or "this"
            acts.append(action("clear", f"Remove from {self.name}", "danger",
                               confirm=f"Clear “{title}” from {self.name}? Its requests and status are wiped so it "
                                       f"can be requested again. Files, Radarr and Sonarr aren't touched."))
        return {"item": self.card(d, mt), "facts": f, "actions": acts, "seasons": seasons}

    # -- what's actually in Radarr/Sonarr for this title
    def library_matches(self, mt, d):
        out = []
        if mt == "movie":
            for s in SERVICES.values():
                if isinstance(s, Radarr):
                    m = next((m for m in safe(s.movies, f"{s.name} movies", []) if m.get("tmdbId") == d.get("id")), None)
                    if m:
                        out.append((s, m))
        else:
            tvdb = (d.get("externalIds") or {}).get("tvdbId")
            for s in SERVICES.values():
                if isinstance(s, Sonarr):
                    m = next((x for x in safe(s.series, f"{s.name} series", [])
                              if (tvdb and x.get("tvdbId") == tvdb) or x.get("tmdbId") == d.get("id")), None)
                    if m:
                        out.append((s, m))
        return out

    def library_facts(self, mt, d):
        rows = []
        for s, m in self.library_matches(mt, d):
            if mt == "movie":
                if m.get("hasFile"):
                    mi = (m.get("movieFile") or {}).get("mediaInfo") or {}
                    rows += [
                        (s.name, join("Downloaded", Radarr.quality(m), fmt_size(m.get("sizeOnDisk")))),
                        (f"{s.name} video", join(mi.get("resolution"), mi.get("videoCodec"), mi.get("videoDynamicRangeType"))),
                        (f"{s.name} audio", join(mi.get("audioCodec"), mi.get("audioChannels"), mi.get("audioLanguages"))),
                        (f"{s.name} path", (m.get("movieFile") or {}).get("path") or m.get("path")),
                    ]
                else:
                    rows.append((s.name, "In library · not downloaded yet" + ("" if m.get("monitored") else " · unmonitored")))
            else:
                st = m.get("statistics") or {}
                rows += [
                    (s.name, join(f"{st.get('episodeFileCount', 0)} of {st.get('episodeCount', 0)} episodes",
                                  fmt_size(st.get("sizeOnDisk")) if st.get("sizeOnDisk") else None)),
                    (f"{s.name} path", m.get("path")),
                ]
        return facts(*rows)

    def act(self, item_id, act, arg=None, choice=None):
        mt, tid = self._split(item_id)
        if act == "clear":
            mid = ((self._req("GET", f"/{mt}/{tid}").get("mediaInfo")) or {}).get("id")
            if not mid:
                raise ApiError("Nothing to clear — it isn't tracked by Seerr")
            self._req("DELETE", f"/media/{mid}")
            self.drop_home()
            return {"message": f"Cleared from {self.name}", "refresh": True}
        body = {"mediaType": mt, "mediaId": tid, "is4k": act == "request4k"}
        if act in ("request", "request4k"):
            if mt == "tv":
                d = self._req("GET", f"/tv/{tid}")
                body["seasons"] = [s["seasonNumber"] for s in d.get("seasons") or [] if s.get("seasonNumber", 0) > 0]
        elif act == "request_season":
            body.update(mediaType="tv", seasons=[int(arg)])
        else:
            raise ApiError(f"Unknown action {act}")
        if choice:
            srv, prof, path = (str(choice).split("|", 2) + ["", "", ""])[:3]
            if srv.isdigit():
                body["serverId"] = int(srv)
            if prof.isdigit():
                body["profileId"] = int(prof)
            if path:
                body["rootFolder"] = path
        self._req("POST", "/request", body=body)
        self.drop_home()
        return {"message": "Requested ✓", "refresh": True}


# ---------------------------------------------------------------- Plex


PLEX_TV = "https://plex.tv"


def is_v2():
    """Harbour v2 has a built-in player; it gets Play buttons instead of Play in Plex."""
    return has_request_context() and request.headers.get("X-Harbour-App") == "2"


def plex_user_from_request():
    """The Plex Home profile the TV picked (Settings → Plex profile), sent as headers."""
    if not has_request_context():
        return None, None
    return request.headers.get("X-Plex-User") or None, request.headers.get("X-Plex-Pin") or None


class Plex:
    def __init__(self, conf):
        self.url = conf["url"].rstrip("/")
        self.token = conf["token"]
        self.default_user = str(conf.get("user") or "") or None
        self.default_pin = str(conf.get("pin") or "") or None
        self.client_id = "harbour-gateway"
        self._tokens = {}       # home user id -> server access token
        self._cache = {}
        self._lock = threading.Lock()

    def _headers(self, token):
        return {"X-Plex-Token": token, "Accept": "application/json", "X-Plex-Client-Identifier": self.client_id,
                "X-Plex-Product": "Harbour", "X-Plex-Version": VERSION}

    def get(self, path, token=None, **params):
        r = requests.get(self.url + path, headers=self._headers(token or self.token), params=params, timeout=20)
        if r.status_code == 401:
            raise ApiError("Plex: token rejected", 502)
        if r.status_code >= 400:
            raise ApiError(f"Plex: HTTP {r.status_code}", 502)
        return (r.json() if r.content else {}).get("MediaContainer") or {}

    def cached(self, key, ttl, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        self._cache[key] = (time.time(), val)
        return val

    def machine_id(self):
        return self.cached("machine", 86400, lambda: self.get("/identity").get("machineIdentifier"))

    # -- Plex Home profiles
    def users(self):
        def load():
            r = requests.get(f"{PLEX_TV}/api/home/users", headers=self._headers(self.token), timeout=20)
            if r.status_code >= 400:
                raise ApiError(f"Plex: couldn't list Home users (HTTP {r.status_code})", 502)
            root = ET.fromstring(r.content)
            return [{"id": u.get("id"), "title": u.get("title") or u.get("username") or "?",
                     "thumb": u.get("thumb"), "protected": u.get("protected") == "1",
                     "admin": u.get("admin") == "1"} for u in root.iter("User")]
        return self.cached("users", 600, load)

    def token_for(self, user=None, pin=None):
        """Server token for a Home user (by id or name). No user / the admin → the configured token."""
        user, pin = user or self.default_user, pin or self.default_pin
        if not user:
            return self.token
        with self._lock:
            if user in self._tokens:
                return self._tokens[user]
        match = next((u for u in safe(self.users, "plex users", []) if user in (u["id"], u["title"])), None)
        if not match or match["admin"]:
            return self.token
        params = {"pin": pin} if pin else {}
        r = requests.post(f"{PLEX_TV}/api/home/users/{match['id']}/switch", params=params,
                          headers=self._headers(self.token), timeout=20)
        if r.status_code in (401, 403):
            raise ApiError(f"Plex: {match['title']} needs the right PIN", 403)
        if r.status_code >= 400:
            raise ApiError(f"Plex: couldn't switch to {match['title']} (HTTP {r.status_code})", 502)
        root = ET.fromstring(r.content)
        user_token = root.get("authenticationToken") or root.get("authToken")
        if not user_token:
            raise ApiError("Plex: no token for that profile", 502)
        res = requests.get(f"{PLEX_TV}/api/v2/resources", params={"includeHttps": 1},
                           headers=self._headers(user_token), timeout=20)
        server_token = user_token
        if res.ok:
            mid = self.machine_id()
            for dev in res.json():
                if dev.get("clientIdentifier") == mid and dev.get("accessToken"):
                    server_token = dev["accessToken"]
        with self._lock:
            self._tokens[user] = server_token
        return server_token

    @staticmethod
    def tmdb_of(meta):
        for g in meta.get("Guid") or []:
            gid = g.get("id", "")
            if gid.startswith("tmdb://"):
                return gid[7:]
        return None

    def show_tmdb(self, rating_key):
        def load():
            m = (self.get(f"/library/metadata/{rating_key}", includeGuids=1).get("Metadata") or [{}])[0]
            return self.tmdb_of(m)
        return self.cached(f"show-tmdb-{rating_key}", 86400, load)

    def art(self, path, w=640, h=360):
        if not path:
            return None
        return f"/plexart?w={w}&h={h}&p={quote(path, safe='')}"

    def meta(self, rk, token):
        m = (self.get(f"/library/metadata/{rk}", token=token, includeGuids=1, includeOnDeck=1)
             .get("Metadata") or [None])[0]
        if not m:
            raise ApiError("Plex: that item is gone", 404)
        return m

    def leaves(self, rk, token):
        return self.get(f"/library/metadata/{rk}/allLeaves", token=token).get("Metadata") or []

    def resolve(self, ref, token):
        """ref → a playable movie/episode in Plex: plex:<ratingKey>, movie-<tmdb>, tv-<tmdb>, tv-<tmdb>:s1e2."""
        if ref.startswith("plex:"):
            m = self.meta(ref[5:], token)
        else:
            base, _, ep = ref.partition(":")
            mt, _, tid = base.partition("-")
            if mt not in ("movie", "tv") or not tid.isdigit():
                raise ApiError("Bad play reference")
            title = ""
            if HUB:
                title = safe(lambda: (lambda d: d.get("title") or d.get("name"))(HUB._tmdb(mt, int(tid))), "tmdb", "")
            found = self.find(mt, tid, title, token)
            if not found:
                raise ApiError("That isn't in Plex yet", 404)
            m = self.meta(found["ratingKey"], token)
            if ep:
                mm = re.fullmatch(r"s(\d+)e(\d+)", ep)
                want = (int(mm.group(1)), int(mm.group(2))) if mm else None
                m = next((x for x in self.leaves(m["ratingKey"], token)
                          if (x.get("parentIndex"), x.get("index")) == want), None)
                if not m:
                    raise ApiError("That episode isn't in Plex yet", 404)
                m = self.meta(m["ratingKey"], token)
        if m.get("type") in ("show", "season"):
            deck = ((m.get("OnDeck") or {}).get("Metadata") or [None])[0]
            if not deck:
                leaves = self.leaves(m["ratingKey"], token)
                deck = next((x for x in leaves if not x.get("viewCount")), leaves[0] if leaves else None)
            if not deck:
                raise ApiError("No episodes in Plex", 404)
            m = self.meta(deck["ratingKey"], token)
        if m.get("type") not in ("movie", "episode"):
            raise ApiError("Plex can't play that", 400)
        return m

    def play_info(self, ref, token, transcode=False, start=False):
        m = self.resolve(ref, token)
        rk = m["ratingKey"]
        media = (m.get("Media") or [{}])[0]
        part = (media.get("Part") or [{}])[0]
        if not part.get("key"):
            raise ApiError("Plex has no file for that", 404)
        offset = 0 if start else int(m.get("viewOffset") or 0)
        if transcode:
            q = {"path": f"/library/metadata/{rk}", "mediaIndex": 0, "partIndex": 0, "protocol": "hls",
                 "fastSeek": 1, "directPlay": 0, "directStream": 1, "directStreamAudio": 1,
                 "videoQuality": 100, "maxVideoBitrate": 40000, "videoResolution": "3840x2160",
                 "subtitles": "auto", "offset": offset // 1000, "session": f"harbour-{rk}-{int(time.time())}",
                 "X-Plex-Token": token, "X-Plex-Client-Identifier": "harbour-v2",
                 "X-Plex-Product": "Harbour", "X-Plex-Platform": "Android"}
            url = f"{self.url}/video/:/transcode/universal/start.m3u8?" + "&".join(
                f"{k}={quote(str(v), safe='')}" for k, v in q.items())
        else:
            url = f"{self.url}{part['key']}?X-Plex-Token={quote(token, safe='')}"
        is_ep = m.get("type") == "episode"
        nxt = None
        if is_ep:
            leaves = safe(lambda: self.leaves(m["grandparentRatingKey"], token), "plex leaves", [])
            keys = [x.get("ratingKey") for x in leaves]
            if rk in keys and keys.index(rk) + 1 < len(leaves):
                n = leaves[keys.index(rk) + 1]
                nxt = clean({"ref": f"plex:{n['ratingKey']}", "title": n.get("title"),
                             "subtitle": f"S{n.get('parentIndex', 0)} E{n.get('index', 0)}",
                             "thumb": self.art(n.get("thumb"), 320, 180)})
        dr = media.get("videoDynamicRange") or ""
        badges = [b for b in (
            "TRANSCODE" if transcode else "DIRECT PLAY",
            {"4k": "4K", "1080": "1080p", "720": "720p"}.get(str(media.get("videoResolution")), None),
            "DOLBY VISION" if "dolby" in dr.lower() or media.get("DOVIPresent") else ("HDR" if "hdr" in dr.lower() else None),
            {"truehd": "TRUEHD", "eac3": "DD+", "ac3": "DOLBY DIGITAL", "dca": "DTS", "dts": "DTS"}.get(
                (media.get("audioCodec") or "").lower()),
        ) if b]
        return clean({
            "ref": ref, "ratingKey": rk, "url": url, "hls": transcode, "offset": offset,
            "duration": int(m.get("duration") or media.get("duration") or 0),
            "title": m.get("grandparentTitle") if is_ep else m.get("title"),
            "subtitle": f"S{m.get('parentIndex', 0)} E{m.get('index', 0)} · {m.get('title')}" if is_ep
            else join(m.get("year"), fmt_runtime((m.get("duration") or 0) // 60000)),
            "backdrop": self.art(m.get("art") or m.get("grandparentArt"), 1280, 720),
            "badges": badges, "next": nxt,
        })

    def timeline(self, token, rk, state, time_ms, duration_ms):
        requests.get(f"{self.url}/:/timeline", params={
            "ratingKey": rk, "key": f"/library/metadata/{rk}", "state": state,
            "time": int(time_ms), "duration": int(duration_ms), "playbackTime": int(time_ms),
        }, headers=dict(self._headers(token), **{"X-Plex-Client-Identifier": "harbour-v2",
                                               "X-Plex-Device-Name": "Harbour"}), timeout=10)

    def on_deck(self, token):
        return self.get("/library/onDeck", token=token, includeGuids=1).get("Metadata") or []

    def find(self, mt, tmdb_id, title, token):
        """Find a title in Plex by its TMDB id (search by name, confirm by guid)."""
        if not title:
            return None
        want = "movie" if mt == "movie" else "show"
        res = self.get("/hubs/search", token=token, query=title, includeGuids=1, limit=20)
        for hub in res.get("Hub") or []:
            for m in hub.get("Metadata") or []:
                if m.get("type") == want and self.tmdb_of(m) == str(tmdb_id):
                    return m
        return None


# ---------------------------------------------------------------- Home hub

def tmdb_img(path, size):
    return f"{TMDB_IMG}{size}{path}" if path else None


class Hub:
    """
    The Home tab and the rich title / person pages. Metadata (cast, filmographies,
    recommendations) comes from TMDB via Seerr; ownership from Radarr/Sonarr; watch
    state from Plex.
    """
    kind = "hub"

    def __init__(self, conf, seerr, plex):
        self.inst = "hub"
        self.name = conf.get("name", "Home")
        self.icon = conf.get("icon", "home")
        self.seerr = seerr
        self.plex = plex
        self._homes = {}
        self._lock = threading.Lock()

    def info(self):
        return {"kind": self.kind, "inst": self.inst, "name": self.name, "icon": self.icon}

    # -- library lookups
    def radarrs(self):
        return [s for s in SERVICES.values() if isinstance(s, Radarr)]

    def sonarrs(self):
        return [s for s in SERVICES.values() if isinstance(s, Sonarr)]

    def owned(self):
        movies = {m.get("tmdbId") for r in self.radarrs() for m in safe(r.movies, r.name, []) if m.get("hasFile")}
        shows = {s.get("tmdbId") for so in self.sonarrs() for s in safe(so.series, so.name, [])
                 if (s.get("statistics") or {}).get("episodeFileCount")}
        return movies, shows

    def mark(self, card, owned):
        """Add an 'In library' badge when we already have it."""
        if not card:
            return card
        mt, _, tid = card["id"].partition("-")
        mine = owned[0] if mt == "movie" else owned[1]
        if tid.isdigit() and int(tid) in mine:
            card["badge"], card["badgeTone"] = "In library", "ok"
        return card

    # -- home
    def home_cached(self, user=None, pin=None):
        key = user or ""
        hit = self._homes.get(key)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        with self._lock:
            hit = self._homes.get(key)
            if hit and time.time() - hit[0] < 60:
                return hit[1]
            val = self.home(user, pin)
            self._homes[key] = (time.time(), val)
            return val

    def refresh_home(self):
        try:
            self._homes[""] = (time.time(), self.home())
        except Exception as e:
            log.warning("hub warm failed: %s", e)

    def drop_home(self):
        self._homes.clear()

    def home(self, user=None, pin=None):
        today = datetime.now(timezone.utc).date()
        token = None
        if self.plex:
            token = safe(lambda: self.plex.token_for(user, pin), "plex token", None)
        d = par(
            deck=lambda: safe(lambda: self.plex.on_deck(token), "plex on deck", []) if (self.plex and token) else [],
            radarr=lambda: [(r, safe(r.movies, r.name, [])) for r in self.radarrs()],
            imports=lambda: [(s, safe(lambda: s.get("/history", page=1, pageSize=80, sortKey="date",
                                                    sortDirection="descending", eventType=3, includeSeries="true",
                                                    includeEpisode="true").get("records", []), f"{s.name} history", []))
                             for s in self.sonarrs()],
            mcal=lambda: [(r, safe(lambda: r.get("/calendar", start=today.isoformat(),
                                                 end=(today + timedelta(days=60)).isoformat(), unmonitored="false"),
                                   f"{r.name} calendar", [])) for r in self.radarrs()],
            tcal=lambda: [(s, safe(lambda: s.get("/calendar", start=today.isoformat(),
                                                 end=(today + timedelta(days=10)).isoformat(), includeSeries="true"),
                                   f"{s.name} calendar", [])) for s in self.sonarrs()],
            trending=lambda: safe(lambda: self.seerr.row_page("trending", 1)["items"], "trending", []),
        )
        owned = (
            {m.get("tmdbId") for _, ms in d["radarr"] for m in ms if m.get("hasFile")},
            {s.get("tmdbId") for so in self.sonarrs() for s in safe(so.series, so.name, [])
             if (s.get("statistics") or {}).get("episodeFileCount")},
        )
        rows = []

        # Continue watching / up next (Plex)
        deck, because = [], None
        for m in d["deck"]:
            is_ep = m.get("type") == "episode"
            tmdb = self.plex.show_tmdb(m.get("grandparentRatingKey")) if is_ep else self.plex.tmdb_of(m)
            if not tmdb:
                continue
            dur, off = m.get("duration") or 0, m.get("viewOffset") or 0
            left = max(0, (dur - off) // 60000)
            title = m.get("grandparentTitle") if is_ep else m.get("title")
            sub = join(f"S{m.get('parentIndex', 0):02d}E{m.get('index', 0):02d}" if is_ep else None,
                       m.get("title") if is_ep else m.get("year"),
                       f"{left}m left" if off else ("Up next" if is_ep else None))
            deck.append(clean({
                "key": f"plex:{m.get('ratingKey')}", "id": f"{'tv' if is_ep else 'movie'}-{tmdb}",
                "ref": f"plex:{m.get('ratingKey')}",
                "title": title or "?", "subtitle": sub,
                "backdrop": self.plex.art(m.get("art") or m.get("grandparentArt") or m.get("thumb")),
                "poster": self.plex.art(m.get("grandparentThumb") if is_ep else m.get("thumb"), 342, 513),
                "overview": m.get("summary"),
                "progress": round(off / dur, 3) if dur and off else None,
                "badge": "Resume" if off else "Up next", "badgeTone": "info",
            }))
            if because is None:
                because = ("tv" if is_ep else "movie", tmdb, title)
        rows.append(row("Continue watching", dedupe(deck), "landscape"))

        # Recently added movies, across every Radarr
        recent = []
        for r, ms in d["radarr"]:
            for m in ms:
                if m.get("hasFile") and m.get("tmdbId"):
                    recent.append(((m.get("movieFile") or {}).get("dateAdded") or m.get("added") or "", r, m))
        recent.sort(key=lambda x: x[0], reverse=True)
        movies = []
        for _, r, m in recent:
            c = r.card(m)
            c.update(key=f"hub:movie-{m['tmdbId']}", id=f"movie-{m['tmdbId']}")
            if r.inst != self.radarrs()[0].inst:
                c["subtitle"] = join(c.get("subtitle"), r.name)
            movies.append(c)
        rows.append(row("Recently added movies", dedupe(movies)))

        # Recently added TV episodes, grouped by show
        eps = []
        for s, recs in d["imports"]:
            for h in recs:
                se, ep = h.get("series") or {}, h.get("episode") or {}
                if se.get("tmdbId"):
                    eps.append((h.get("date") or "", s, se, ep))
        eps.sort(key=lambda x: x[0], reverse=True)
        tv = []
        for dt, s, se, ep in eps:
            c = s.card(se, badge=fmt_date(dt), tone="ok",
                       subtitle=join(f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}",
                                     ep.get("title")))
            c.update(key=f"hub:tv-{se['tmdbId']}", id=f"tv-{se['tmdbId']}")
            tv.append(c)
        rows.append(row("Recently added TV", dedupe(tv), "landscape"))

        # Coming soon: movies + this week's episodes
        soon = []
        for r, cal in d["mcal"]:
            for m in cal:
                dates = sorted(x for x in (m.get("digitalRelease"), m.get("physicalRelease"), m.get("inCinemas"))
                               if x and x[:10] >= today.isoformat())
                if dates and m.get("tmdbId") and not m.get("hasFile"):
                    c = r.card(m, badge=fmt_date(dates[0]), tone="info")
                    c.update(key=f"hub:movie-{m['tmdbId']}", id=f"movie-{m['tmdbId']}")
                    soon.append((dates[0], c))
        for s, cal in d["tcal"]:
            for e in cal:
                se = e.get("series") or {}
                if se.get("tmdbId"):
                    c = s.card(se, badge=fmt_date(e.get("airDateUtc")), tone="info",
                               subtitle=join(Sonarr.sxe(e), e.get("title")))
                    c.update(key=f"hub:tv-{se['tmdbId']}", id=f"tv-{se['tmdbId']}")
                    soon.append((e.get("airDateUtc") or "", c))
        soon.sort(key=lambda x: x[0])
        rows.append(row("Coming soon", dedupe([c for _, c in soon])))

        rows.append(row("Trending", [self.mark(c, owned) for c in d["trending"]], key="trending", more=True))

        if because:
            mt, tid, title = because
            recs = safe(lambda: self.seerr._req("GET", f"/{mt}/{tid}/recommendations", {"page": 1}).get("results"),
                        "recommendations", [])
            rows.append(row(f"Because you watched {title}", [self.mark(c, owned) for c in self.seerr.cards(recs, mt)]))

        rows = [r for r in rows if r["items"]]
        hero = next((i for r in rows for i in r["items"] if i.get("backdrop")), None)
        return {"hero": hero, "rows": rows}

    def search(self, q):
        res = self.seerr.search(q)
        owned = self.owned()
        return {"items": [self.mark(c, owned) for c in res["items"]]}

    def row_page(self, key, page=1):
        res = self.seerr.row_page(key, page)
        owned = self.owned()
        res["items"] = [self.mark(c, owned) for c in res["items"]]
        return res

    # -- pages
    def _tmdb(self, mt, tid):
        return self.seerr.cached(f"tmdb-{mt}-{tid}", 600, lambda: self.seerr._req("GET", f"/{mt}/{tid}"))

    def detail(self, item_id):
        mt, _, tid = item_id.partition("-")
        if mt == "person" and tid.isdigit():
            return self.person(int(tid))
        if mt not in ("movie", "tv") or not tid.isdigit():
            raise ApiError("Bad item id")
        tid = int(tid)
        d = self._tmdb(mt, tid)
        page = self.seerr.detail_from(mt, tid, d)
        title = d.get("title") or d.get("name") or ""
        owned = self.owned()

        # headline facts: tagline, director/creator, ratings
        extra = []
        crew = (d.get("credits") or {}).get("crew") or []
        if mt == "movie":
            directors = [c.get("name") for c in crew if c.get("job") == "Director"]
            extra.append(("Director", ", ".join(directors[:3])))
        else:
            extra.append(("Created by", ", ".join(c.get("name", "") for c in d.get("createdBy") or [])))
        if mt == "movie":
            rt = safe(lambda: self.seerr.cached(f"ratings-{tid}", 86400,
                                                lambda: self.seerr._req("GET", f"/movie/{tid}/ratingscombined")),
                      "ratings", {}) or {}
            imdb = (rt.get("imdb") or {}).get("criticsScore")
            rtc = (rt.get("rt") or {}).get("criticsScore")
            extra.append(("Ratings", join(f"IMDb {imdb}" if imdb else None, f"Rotten Tomatoes {rtc}%" if rtc else None)))
        if d.get("tagline"):
            page["item"]["overview"] = f"“{d['tagline']}”\n\n{page['item'].get('overview') or ''}".strip()
        page["facts"] = facts(*extra) + page["facts"]

        # Plex: watched state + play
        if self.plex:
            user, pin = plex_user_from_request()
            token = safe(lambda: self.plex.token_for(user, pin), "plex token", None)
            pm = safe(lambda: self.plex.find(mt, tid, title, token), "plex search", None) if token else None
            if pm:
                if mt == "movie":
                    if pm.get("viewOffset"):
                        left = max(0, ((pm.get("duration") or 0) - pm["viewOffset"]) // 60000)
                        state = f"In progress · {left}m left"
                    else:
                        state = "Watched" if pm.get("viewCount") else "Not watched"
                else:
                    state = f"{pm.get('viewedLeafCount', 0)} of {pm.get('leafCount', 0)} episodes watched"
                page["facts"].insert(0, {"label": "Plex", "value": state})
                rk = pm.get("ratingKey")
                if is_v2():
                    if mt == "movie" and pm.get("viewOffset"):
                        left = max(0, ((pm.get("duration") or 0) - pm["viewOffset"]) // 60000)
                        page["actions"][0:0] = [
                            action("play", f"Resume · {left}m left", "primary", opens="play", arg=f"plex:{rk}"),
                            action("play_start", "Play from start", opens="play", arg=f"plex:{rk}|start")]
                    else:
                        page["actions"].insert(0, action("play", "Play", "primary", opens="play", arg=f"plex:{rk}"))
                else:
                    page["actions"].insert(0, action("plex", "▶  Play in Plex", "primary", opens="plex",
                                                     arg=f"{self.plex.machine_id()}|{rk}"))

        # trailer
        vids = d.get("relatedVideos") or []
        trailer = next((v for v in vids if v.get("site") == "YouTube" and v.get("type") == "Trailer"), None) \
            or next((v for v in vids if v.get("site") == "YouTube"), None)
        if trailer and trailer.get("key"):
            page["actions"].append(action("trailer", "Trailer", opens="youtube", arg=trailer["key"]))

        # cast + recommendations
        cast = sorted((d.get("credits") or {}).get("cast") or [], key=lambda c: c.get("order", 999))[:24]
        page["people"] = [clean({"id": f"person-{c['id']}", "name": c.get("name") or "?",
                                 "role": c.get("character"), "photo": tmdb_img(c.get("profilePath"), "w185")})
                          for c in cast if c.get("id")]
        recs = par(
            r=lambda: safe(lambda: self.seerr._req("GET", f"/{mt}/{tid}/recommendations", {"page": 1}).get("results"),
                           "recommendations", []),
            s=lambda: safe(lambda: self.seerr._req("GET", f"/{mt}/{tid}/similar", {"page": 1}).get("results"),
                           "similar", []),
        )
        page["rows"] = [r for r in (
            row("More like this", [self.mark(c, owned) for c in self.seerr.cards(recs["r"], mt)]),
            row("Similar", [self.mark(c, owned) for c in self.seerr.cards(recs["s"], mt)]),
        ) if r["items"]]
        return page

    def person(self, pid):
        d = par(p=lambda: self.seerr.cached(f"person-{pid}", 3600, lambda: self.seerr._req("GET", f"/person/{pid}")),
                c=lambda: self.seerr.cached(f"credits-{pid}", 3600,
                                            lambda: self.seerr._req("GET", f"/person/{pid}/combined_credits")))
        p, cr = d["p"], d["c"]
        owned = self.owned()

        def credit_cards(items):
            items = sorted(items, key=lambda c: -(c.get("popularity") or 0))
            out = []
            for c in items:
                card = self.seerr.card(c)
                if card:
                    if c.get("character"):
                        card["subtitle"] = join(card.get("subtitle"), f"as {c['character']}")
                    elif c.get("job"):
                        card["subtitle"] = join(card.get("subtitle"), c["job"])
                    out.append(self.mark(card, owned))
            return dedupe(out, 60)

        cast = cr.get("cast") or []
        crew = cr.get("crew") or []
        movies = credit_cards([c for c in cast if c.get("mediaType") == "movie"])
        shows = credit_cards([c for c in cast if c.get("mediaType") == "tv" and (c.get("episodeCount") or 2) > 1])
        behind = credit_cards([c for c in crew if c.get("job") in ("Director", "Screenplay", "Writer", "Creator",
                                                                    "Producer", "Executive Producer")])
        known = (movies + shows)[:1]
        age = None
        if p.get("birthday") and not p.get("deathday"):
            b = parse_dt(p["birthday"])
            if b:
                age = datetime.now().year - b.year
        item = clean({
            "key": f"person-{pid}", "id": f"person-{pid}", "title": p.get("name") or "?",
            "subtitle": join(p.get("knownForDepartment"), f"age {age}" if age else None, p.get("placeOfBirth")),
            "poster": tmdb_img(p.get("profilePath"), "w342"),
            "backdrop": known[0].get("backdrop") if known else None,
            "overview": p.get("biography"),
        })
        f = facts(("Born", fmt_date(p.get("birthday"), True)), ("Died", fmt_date(p.get("deathday"), True)),
                  ("Place of birth", p.get("placeOfBirth")), ("Known for", p.get("knownForDepartment")),
                  ("In your library", sum(1 for c in movies + shows if c.get("badge") == "In library") or None))
        rows = [r for r in (row("Movies", movies), row("TV", shows), row("Behind the camera", behind)) if r["items"]]
        return {"item": item, "facts": f, "actions": [], "rows": rows}

    def act(self, item_id, act, arg=None, choice=None):
        res = self.seerr.act(item_id, act, arg, choice)
        self.drop_home()
        return res


# ---------------------------------------------------------------- File Station

FS_ERRORS = {
    105: "The gateway's DSM user doesn't have permission for that",
    400: "Invalid parameter", 401: "Unknown file operation error", 402: "The NAS is too busy",
    407: "Operation not permitted", 408: "No such file or folder", 411: "Read-only file system",
    412: "File name too long", 414: "That name already exists", 415: "Disk quota exceeded",
    416: "No space left on the NAS", 417: "Input/output error", 418: "Illegal name or path",
    419: "Illegal file name", 421: "File is busy", 599: "Task not found", 900: "Delete failed",
    1000: "Copy failed", 1001: "Move failed", 1002: "Destination error",
    1003: "Something with that name is already there", 1100: "Couldn't create folder",
    1101: "Too many folders", 1200: "Rename failed",
}
AUTH_ERRORS = {400: "wrong DSM username or password", 401: "DSM account disabled",
               402: "permission denied", 403: "account uses 2-step verification — use a dedicated user without it",
               404: "2-step verification failed", 407: "IP blocked by DSM auto-block"}


class FileStation:
    def __init__(self, conf):
        self.url = conf["url"].rstrip("/")
        self.user = conf["username"]
        self.pw = conf["password"]
        self.verify = conf.get("verify_ssl", True)
        self.allowed = [a.rstrip("/") for a in conf.get("allowed_shares") or []]
        self.sid = None
        self.lock = threading.Lock()

    def login(self):
        r = requests.post(f"{self.url}/webapi/auth.cgi", data={
            "api": "SYNO.API.Auth", "version": "3", "method": "login",
            "account": self.user, "passwd": self.pw, "session": "FileStation", "format": "sid",
        }, timeout=20, verify=self.verify).json()
        if not r.get("success"):
            code = (r.get("error") or {}).get("code")
            raise ApiError(f"File Station login failed: {AUTH_ERRORS.get(code, f'code {code}')}", 502)
        self.sid = r["data"]["sid"]

    def call(self, api, version, method, **params):
        for attempt in range(2):
            with self.lock:
                if not self.sid:
                    self.login()
                sid = self.sid
            data = {"api": api, "version": str(version), "method": method, "_sid": sid, **params}
            r = requests.post(f"{self.url}/webapi/entry.cgi", data=data, timeout=60, verify=self.verify).json()
            if r.get("success"):
                return r.get("data") or {}
            err = r.get("error") or {}
            code = err.get("code")
            if code in (106, 107, 119) and attempt == 0:
                with self.lock:
                    self.sid = None
                continue
            sub = [e.get("code") for e in err.get("errors") or [] if isinstance(e, dict)]
            msg = FS_ERRORS.get(sub[0] if sub else code) or FS_ERRORS.get(code) or f"File Station error {code}"
            raise ApiError(msg, 400)
        raise ApiError("File Station session error", 502)

    def run_task(self, api, version, timeout=180, **params):
        tid = self.call(api, version, "start", **params)["taskid"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = self.call(api, version, "status", taskid=tid)
            if s.get("finished"):
                return True
            time.sleep(0.6)
        return False  # still running on the NAS

    def check(self, path, movable=False):
        if not isinstance(path, str) or not path.startswith("/"):
            raise ApiError("Bad path")
        norm = posixpath.normpath(path)
        if norm != path.rstrip("/") or norm == "/":
            raise ApiError("Bad path")
        if self.allowed and not any(norm == a or norm.startswith(a + "/") for a in self.allowed):
            raise ApiError("That shared folder isn't enabled in Harbour's config", 403)
        if movable and norm.count("/") < 2:
            raise ApiError("Shared folders themselves can't be moved, renamed or deleted")
        return norm

    @staticmethod
    def name_ok(name):
        name = (name or "").strip()
        if not name or name in (".", "..") or "/" in name or "\\" in name or len(name) > 255:
            raise ApiError("That isn't a valid name")
        return name

    def listing(self, path):
        if not path:
            d = self.call("SYNO.FileStation.List", 2, "list_share", sort_by="name",
                          additional=json.dumps(["time"]))
            items = [{"name": s["name"], "path": s["path"], "isDir": True, "size": 0,
                      "modified": ((s.get("additional") or {}).get("time") or {}).get("mtime", 0)}
                     for s in d.get("shares", [])
                     if not self.allowed or s["path"] in self.allowed]
            return {"path": "", "parent": None, "items": items}
        path = self.check(path)
        items, offset = [], 0
        while True:
            d = self.call("SYNO.FileStation.List", 2, "list", folder_path=path, offset=offset, limit=1000,
                          sort_by="name", sort_direction="asc", additional=json.dumps(["size", "time"]))
            files = d.get("files", [])
            for f in files:
                add = f.get("additional") or {}
                items.append({"name": f["name"], "path": f["path"], "isDir": bool(f.get("isdir")),
                              "size": add.get("size", 0) if not f.get("isdir") else 0,
                              "modified": (add.get("time") or {}).get("mtime", 0)})
            offset += len(files)
            if not files or offset >= d.get("total", 0):
                break
        items.sort(key=lambda i: (not i["isDir"], i["name"].lower()))
        parent = posixpath.dirname(path)
        return {"path": path, "parent": "" if parent == "/" else parent, "items": items}

    def op(self, b):
        op = b.get("op")
        j = lambda v: json.dumps(v, ensure_ascii=False)
        if op == "mkdir":
            parent, name = self.check(b.get("path")), self.name_ok(b.get("name"))
            self.call("SYNO.FileStation.CreateFolder", 2, "create",
                      folder_path=j([parent]), name=j([name]), force_parent="false")
            return {"message": f"Created “{name}”"}
        if op == "rename":
            path, name = self.check(b.get("path"), movable=True), self.name_ok(b.get("name"))
            self.call("SYNO.FileStation.Rename", 2, "rename", path=j([path]), name=j([name]))
            return {"message": f"Renamed to “{name}”"}
        if op in ("move", "copy"):
            paths = [self.check(p, movable=True) for p in b.get("paths") or []]
            dest = self.check(b.get("dest"))
            if not paths:
                raise ApiError("Nothing selected")
            for p in paths:
                if dest == p or dest.startswith(p + "/"):
                    raise ApiError("Can't put a folder inside itself")
                if posixpath.dirname(p) == dest:
                    raise ApiError("It's already in this folder")
            done = self.run_task("SYNO.FileStation.CopyMove", 3, path=j(paths), dest_folder_path=dest,
                                 overwrite="false", remove_src="true" if op == "move" else "false")
            verb = "Moved" if op == "move" else "Copied"
            return {"message": f"{verb} {len(paths)} item{'s' if len(paths) != 1 else ''}" if done
                    else f"{verb[:-1]}ing in the background on the NAS…"}
        if op == "delete":
            paths = [self.check(p, movable=True) for p in b.get("paths") or []]
            if not paths:
                raise ApiError("Nothing selected")
            done = self.run_task("SYNO.FileStation.Delete", 2, path=j(paths), recursive="true")
            return {"message": "Deleted" if done else "Deleting in the background on the NAS…"}
        raise ApiError(f"Unknown file operation {op}")




# ---------------------------------------------------------------- download clients

def _status_label(s):
    labels = {"PP_QUEUED": "Waiting to process", "LOADING_PARS": "Loading pars",
              "VERIFYING_SOURCES": "Verifying", "VERIFYING_REPAIRED": "Verifying",
              "FETCHING": "Fetching NZB", "PP_FINISHED": "Finishing"}
    return labels.get(s) or s.replace("_", " ").title()


def _sort_rank(it):
    if it["speed"] > 0 or it["status"] == "Downloading":
        return 0
    return 2 if it["paused"] else 1


class NzbGet:
    kind = "nzbget"

    def __init__(self, conf):
        self.id = conf.get("id", "nzbget")
        self.name = conf.get("name", "NZBGet")
        self.url = conf["url"].rstrip("/")
        self.auth = (conf.get("username") or "", conf.get("password") or "")

    def rpc(self, method, *params):
        r = requests.post(f"{self.url}/jsonrpc", json={"method": method, "params": list(params), "id": 1},
                          auth=self.auth, timeout=15)
        if r.status_code == 401:
            raise ApiError(f"{self.name}: wrong username or password", 502)
        if r.status_code >= 400:
            raise ApiError(f"{self.name}: HTTP {r.status_code}", 502)
        j = r.json()
        if j.get("error"):
            raise ApiError(f"{self.name}: {(j['error'] or {}).get('message', 'error')}", 502)
        return j.get("result")

    def snapshot(self):
        d = par(st=lambda: self.rpc("status"), groups=lambda: self.rpc("listgroups", 0))
        st, groups = d["st"] or {}, d["groups"] or []
        rate = int(st.get("DownloadRate") or 0)
        items = []
        for g in groups:
            size = ((g.get("FileSizeHi") or 0) << 32) + (g.get("FileSizeLo") or 0) or int((g.get("FileSizeMB") or 0) * 1048576)
            left = ((g.get("RemainingSizeHi") or 0) << 32) + (g.get("RemainingSizeLo") or 0) \
                or int((g.get("RemainingSizeMB") or 0) * 1048576)
            status = g.get("Status") or ""
            active = status == "DOWNLOADING"
            speed = rate if active else 0
            items.append({
                "client": self.id, "clientName": self.name, "id": str(g.get("NZBID")),
                "name": g.get("NZBName") or "?", "status": _status_label(status),
                "progress": round(1 - left / size, 4) if size else 0.0,
                "size": size, "left": left, "speed": speed,
                "eta": int(left / speed) if speed else -1,
                "category": g.get("Category") or None,
                "paused": status == "PAUSED", "canTop": True, "canDeleteFiles": False,
            })
        client = {"id": self.id, "name": self.name, "kind": self.kind, "speed": rate,
                  "paused": bool(st.get("DownloadPaused")), "count": len(items), "seeding": 0}
        return client, items

    def act(self, item_id, act):
        if item_id is None:
            if act not in ("pause", "resume"):
                raise ApiError("Unknown action")
            self.rpc("pausedownload" if act == "pause" else "resumedownload")
            return {"message": f"{self.name} {'paused' if act == 'pause' else 'resumed'}"}
        cmd = {"pause": "GroupPause", "resume": "GroupResume", "delete": "GroupDelete",
               "delete_files": "GroupFinalDelete", "top": "GroupMoveTop"}.get(act)
        if not cmd:
            raise ApiError("Unknown action")
        ok = self.rpc("editqueue", cmd, "", [int(item_id)])
        if ok is False:
            raise ApiError(f"{self.name} refused that")
        return {"message": {"pause": "Paused", "resume": "Resumed", "delete": "Deleted",
                            "delete_files": "Deleted", "top": "Moved to top"}[act]}


QB_STATES = {"downloading": "Downloading", "forcedDL": "Downloading", "metaDL": "Fetching metadata",
             "forcedMetaDL": "Fetching metadata", "stalledDL": "Stalled", "queuedDL": "Queued",
             "pausedDL": "Paused", "stoppedDL": "Paused", "checkingDL": "Checking",
             "checkingResumeData": "Checking", "allocating": "Allocating", "moving": "Moving",
             "error": "Error", "missingFiles": "Missing files"}


class QBittorrent:
    kind = "qbittorrent"

    def __init__(self, conf):
        self.id = conf.get("id", "qbittorrent")
        self.name = conf.get("name", "qBittorrent")
        self.url = conf["url"].rstrip("/")
        self.user, self.pw = conf.get("username") or "", conf.get("password") or ""
        self.api_key = conf.get("api_key") or ""
        self.s = requests.Session()
        self.lock = threading.Lock()
        self.logged_in = False
        if self.api_key:
            # qBittorrent 5.2+: API key instead of username/password (no login, no cookies)
            self.s.headers["Authorization"] = f"Bearer {self.api_key}"

    def login(self):
        r = self.s.post(f"{self.url}/api/v2/auth/login", data={"username": self.user, "password": self.pw},
                        headers={"Referer": self.url}, timeout=10)
        if r.status_code == 403:
            raise ApiError(f"{self.name}: login blocked — too many failed attempts, try again later", 502)
        if r.text.strip() != "Ok.":
            raise ApiError(f"{self.name}: wrong username or password", 502)
        self.logged_in = True

    def req(self, method, path, ok404=False, **kw):
        for attempt in range(2):
            with self.lock:
                if not self.api_key and not self.logged_in and (self.user or self.pw):
                    self.login()
            r = self.s.request(method, f"{self.url}/api/v2{path}", headers={"Referer": self.url}, timeout=15, **kw)
            if r.status_code in (401, 403) and self.api_key:
                raise ApiError(f"{self.name}: API key rejected — check it in qBittorrent's Web UI settings "
                               "(needs qBittorrent 5.2 or newer)", 502)
            if r.status_code == 403 and attempt == 0:
                self.logged_in = False
                if not (self.user or self.pw):
                    break
                continue
            if r.status_code == 404 and ok404:
                return None
            if r.status_code >= 400:
                raise ApiError(f"{self.name}: HTTP {r.status_code}", 502)
            return r
        raise ApiError(f"{self.name}: not authorised", 502)

    def snapshot(self):
        d = par(info=lambda: self.req("GET", "/transfer/info").json(),
                ts=lambda: self.req("GET", "/torrents/info").json())
        info, ts = d["info"] or {}, d["ts"] or []
        items, seeding = [], 0
        for t in sorted(ts, key=lambda t: (t.get("priority") or 10 ** 6, t.get("added_on") or 0)):
            prog = float(t.get("progress") or 0)
            if prog >= 1:
                seeding += 1
                continue
            state = t.get("state") or ""
            size = int(t.get("size") or t.get("total_size") or 0)
            left = int(t.get("amount_left") if t.get("amount_left") is not None else size * (1 - prog))
            eta = t.get("eta")
            items.append({
                "client": self.id, "clientName": self.name, "id": t.get("hash"),
                "name": t.get("name") or "?", "status": QB_STATES.get(state, state or "?"),
                "progress": round(prog, 4), "size": size, "left": left,
                "speed": int(t.get("dlspeed") or 0),
                "eta": -1 if eta is None or eta >= 8640000 else int(eta),
                "category": t.get("category") or None,
                "paused": state in ("pausedDL", "stoppedDL"), "canTop": False, "canDeleteFiles": True,
            })
        client = {"id": self.id, "name": self.name, "kind": self.kind,
                  "speed": int(info.get("dl_info_speed") or 0),
                  "paused": bool(items) and all(i["paused"] for i in items),
                  "count": len(items), "seeding": seeding}
        return client, items

    def act(self, item_id, act):
        hashes = item_id or "all"
        if act in ("pause", "resume"):
            new, old = ("stop", "pause") if act == "pause" else ("start", "resume")
            # qBittorrent 5 renamed pause/resume to stop/start
            if self.req("POST", f"/torrents/{new}", ok404=True, data={"hashes": hashes}) is None:
                self.req("POST", f"/torrents/{old}", data={"hashes": hashes})
            return {"message": ("Paused" if act == "pause" else "Resumed") + ("" if item_id else f" all in {self.name}")}
        if act in ("delete", "delete_files") and item_id:
            self.req("POST", "/torrents/delete",
                     data={"hashes": hashes, "deleteFiles": "true" if act == "delete_files" else "false"})
            return {"message": "Deleted" + (" with files" if act == "delete_files" else "")}
        raise ApiError("Unknown action")


def downloads_snapshot():
    clients, items = [], []
    futs = [(c, POOL.submit(c.snapshot)) for c in CLIENTS.values()]
    for c, fut in futs:
        try:
            info, its = fut.result()
            clients.append(info)
            items.extend(its)
        except Exception as e:
            msg = e.message if isinstance(e, ApiError) else f"Couldn't reach {c.name}"
            clients.append({"id": c.id, "name": c.name, "kind": c.kind, "speed": 0, "paused": False,
                            "count": 0, "seeding": 0, "error": msg})
    items.sort(key=_sort_rank)
    return {"clients": clients, "items": items}


# ---------------------------------------------------------------- wiring

def init():
    global CFG, FILES, HUB, PLEX
    HUB = None
    CFG = load_config()
    SERVICES.clear()
    CLIENTS.clear()
    for c in CFG.get("radarr") or []:
        s = Radarr(c)
        SERVICES[(s.kind, s.inst)] = s
    for c in CFG.get("sonarr") or []:
        s = Sonarr(c)
        SERVICES[(s.kind, s.inst)] = s
    seerr = CFG.get("seerr")
    for c in (seerr if isinstance(seerr, list) else [seerr] if seerr else []):
        s = Seerr(c)
        SERVICES[(s.kind, s.inst)] = s
    FILES = FileStation(CFG["filestation"]) if CFG.get("filestation") else None
    PLEX = Plex(CFG["plex"]) if (CFG.get("plex") or {}).get("token") else None
    seerrs = [x for x in SERVICES.values() if isinstance(x, Seerr)]
    hub_conf = CFG.get("hub", {})
    HUB = Hub(hub_conf if isinstance(hub_conf, dict) else {}, seerrs[0], PLEX) \
        if seerrs and hub_conf is not False else None
    if HUB:  # Home goes first in the menu
        items = list(SERVICES.items())
        SERVICES.clear()
        SERVICES[(HUB.kind, HUB.inst)] = HUB
        SERVICES.update(items)
    for key, cls in (("nzbget", NzbGet), ("qbittorrent", QBittorrent)):
        conf = CFG.get(key)
        for c in (conf if isinstance(conf, list) else [conf] if conf else []):
            cl = cls(c)
            CLIENTS[cl.id] = cl
    log.info("Loaded %d services, %d download clients, File Station %s",
             len(SERVICES), len(CLIENTS), "on" if FILES else "off")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def svc(kind, inst):
    s = SERVICES.get((kind, inst))
    if not s:
        raise ApiError(f"No service {kind}/{inst}", 404)
    return s


def warm_loop():
    """Keep every home page warm so switching tabs on the TV is instant."""
    time.sleep(2)
    while True:
        for s in list(SERVICES.values()):
            s.refresh_home()
        time.sleep(240)


PUBLIC = ("/", "/apk", "/Harbour.apk", "/HarbourV2.apk")


@app.before_request
def check_token():
    if request.path.lower() in [p.lower() for p in PUBLIC]:
        return None
    token = CFG.get("token") or ""
    given = request.headers.get("X-Token") or request.args.get("t") or ""
    if token and not hmac.compare_digest(token, given):
        return jsonify(error="Wrong access token — check Harbour's settings"), 401
    return None


@app.errorhandler(ApiError)
def on_api_error(e):
    return jsonify(error=e.message), e.status


@app.errorhandler(requests.RequestException)
def on_requests_error(e):
    host = urlsplit(getattr(e.request, "url", "") or "").netloc if getattr(e, "request", None) else ""
    log.warning("upstream error: %s", e)
    return jsonify(error=f"Couldn't reach {host or 'a service'}"), 502


@app.errorhandler(Exception)
def on_error(e):
    if hasattr(e, "code") and hasattr(e, "description"):  # werkzeug HTTP errors
        return jsonify(error=e.description), e.code
    log.exception("unhandled")
    return jsonify(error=f"Gateway error: {e}"), 500


@app.get("/")
def root():
    return jsonify(name="Harbour gateway", version=VERSION, ok=True)


@app.get("/apk")
@app.get("/Harbour.apk")
@app.get("/harbour.apk")
def apk():
    """Short link for Downloader: http://<nas>:8765/Harbour.apk streams the newest APK from GitHub.
    Served directly (not a redirect) with an .apk name and type, so Downloader installs it."""
    repo = CFG.get("github_repo") or DEFAULT_REPO
    r = requests.get(f"https://github.com/{repo}/releases/latest/download/Harbour.apk", stream=True, timeout=60)
    if r.status_code >= 400:
        return jsonify(error=f"GitHub returned HTTP {r.status_code}"), 502
    headers = {"Content-Disposition": 'attachment; filename="Harbour.apk"'}
    if r.headers.get("Content-Length"):
        headers["Content-Length"] = r.headers["Content-Length"]
    return Response(stream_with_context(r.iter_content(64 * 1024)),
                    mimetype="application/vnd.android.package-archive", headers=headers)


@app.get("/api/ping")
def ping():
    return jsonify(ok=True, version=VERSION)


@app.get("/api/config")
def config():
    return jsonify(name=CFG.get("name", "Harbour"),
                   version=VERSION,
                   services=[s.info() for s in SERVICES.values()],
                   files=FILES is not None,
                   downloads=bool(CLIENTS),
                   plex=PLEX is not None,
                   repo=CFG.get("github_repo") or DEFAULT_REPO)


@app.post("/api/reload")
def reload_config():
    init()
    return config()


@app.post("/api/log")
def app_log():
    """The TV app posts crash reports here so they show up in Container Manager's log."""
    b = request.get_json(silent=True) or {}
    log.warning("APP REPORT (%s)\n%s", b.get("version", "?"), str(b.get("text", ""))[:20000])
    return jsonify(ok=True)


@app.get("/api/svc/<kind>/<inst>/home")
def svc_home(kind, inst):
    s = svc(kind, inst)
    if isinstance(s, Hub):
        return jsonify(s.home_cached(*plex_user_from_request()))
    return jsonify(s.home_cached())


def plex_token():
    if not PLEX:
        raise ApiError("Plex isn't configured", 404)
    return PLEX.token_for(*plex_user_from_request())


@app.get("/api/play")
def play():
    ref = request.args.get("ref", "")
    start = ref.endswith("|start") or request.args.get("start") == "1"
    ref = ref.split("|")[0]
    token = plex_token()
    return jsonify(PLEX.play_info(ref, token, transcode=request.args.get("transcode") == "1", start=start))


@app.post("/api/plex/progress")
def plex_progress():
    b = request.get_json(silent=True) or {}
    PLEX.timeline(plex_token(), b.get("ratingKey"), b.get("state", "playing"), b.get("time", 0), b.get("duration", 0))
    if HUB and b.get("state") == "stopped":
        HUB.drop_home()
    return jsonify(ok=True)


@app.get("/HarbourV2.apk")
@app.get("/harbourv2.apk")
def apk_v2():
    """Newest Harbour v2 build (tags v2.*) for Downloader."""
    repo = CFG.get("github_repo") or DEFAULT_REPO
    rels = requests.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 30}, timeout=20).json()
    asset = next((a for r in rels if str(r.get("tag_name", "")).startswith("v2.")
                  for a in r.get("assets") or [] if a.get("name", "").endswith(".apk")), None)
    if not asset:
        return jsonify(error="No Harbour v2 build published yet"), 404
    r = requests.get(asset["browser_download_url"], stream=True, timeout=60)
    headers = {"Content-Disposition": 'attachment; filename="HarbourV2.apk"'}
    if r.headers.get("Content-Length"):
        headers["Content-Length"] = r.headers["Content-Length"]
    return Response(stream_with_context(r.iter_content(64 * 1024)),
                    mimetype="application/vnd.android.package-archive", headers=headers)


@app.get("/api/plex/users")
def plex_users():
    if not PLEX:
        raise ApiError("Plex isn't configured", 404)
    return jsonify(users=[{k: u[k] for k in ("id", "title", "thumb", "protected")} for u in PLEX.users()])


@app.get("/plexart")
def plexart():
    if not PLEX:
        return Response(status=404)
    w, h = int(request.args.get("w", 640)), int(request.args.get("h", 360))
    r = requests.get(f"{PLEX.url}/photo/:/transcode", params={
        "width": w, "height": h, "minSize": 1, "upscale": 1, "url": request.args.get("p", ""),
        "X-Plex-Token": PLEX.token}, timeout=30)
    if r.status_code >= 400:
        return Response(status=404)
    return Response(r.content, mimetype=r.headers.get("Content-Type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/svc/<kind>/<inst>/search")
def svc_search(kind, inst):
    return jsonify(svc(kind, inst).search(request.args.get("q", "")))


@app.get("/api/svc/<kind>/<inst>/row")
def svc_row(kind, inst):
    s = svc(kind, inst)
    if not hasattr(s, "row_page"):
        raise ApiError("This row can't be expanded")
    return jsonify(s.row_page(request.args.get("key", ""), request.args.get("page", 1)))


@app.get("/api/svc/<kind>/<inst>/item/<item_id>")
def svc_detail(kind, inst, item_id):
    return jsonify(svc(kind, inst).detail(item_id))


@app.post("/api/svc/<kind>/<inst>/item/<item_id>/action")
def svc_action(kind, inst, item_id):
    b = request.get_json(silent=True) or {}
    return jsonify(svc(kind, inst).act(item_id, b.get("action"), b.get("arg"), b.get("choice")))


@app.get("/api/svc/<kind>/<inst>/item/<item_id>/releases")
def svc_releases(kind, inst, item_id):
    s = svc(kind, inst)
    if not hasattr(s, "releases"):
        raise ApiError("Manual search isn't available here")
    return jsonify(s.releases(item_id, request.args.get("arg")))


@app.post("/api/svc/<kind>/<inst>/item/<item_id>/grab")
def svc_grab(kind, inst, item_id):
    s = svc(kind, inst)
    b = request.get_json(silent=True) or {}
    if not hasattr(s, "grab"):
        raise ApiError("Manual search isn't available here")
    return jsonify(s.grab(b.get("guid"), b.get("indexerId")))


@app.get("/img/<kind>/<inst>/<path:p>")
def img(kind, inst, p):
    s = svc(kind, inst)
    parts = urlsplit(s.url)
    r = requests.get(f"{parts.scheme}://{parts.netloc}/{p}", headers={"X-Api-Key": s.key}, timeout=30)
    if r.status_code >= 400:
        return Response(status=404)
    return Response(r.content, mimetype=r.headers.get("Content-Type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})


def files():
    if not FILES:
        raise ApiError("File Station isn't configured", 404)
    return FILES


@app.get("/api/files/list")
def files_list():
    return jsonify(files().listing(request.args.get("path", "")))


@app.post("/api/files/op")
def files_op():
    return jsonify(files().op(request.get_json(silent=True) or {}))


@app.get("/api/downloads")
def downloads():
    if not CLIENTS:
        raise ApiError("No download clients configured", 404)
    return jsonify(downloads_snapshot())


@app.post("/api/downloads/action")
def downloads_action():
    b = request.get_json(silent=True) or {}
    c = CLIENTS.get(b.get("client"))
    if not c:
        raise ApiError("Unknown download client", 404)
    return jsonify(c.act(b.get("id"), b.get("action")))


if os.path.exists(CONFIG_PATH):
    init()
else:
    log.warning("No config at %s — copy config.example.json there", CONFIG_PATH)

if __name__ == "__main__":
    threading.Thread(target=warm_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8765")), threaded=True)
