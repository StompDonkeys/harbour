#!/usr/bin/env python3
"""
Harbour gateway — a small API that sits on the Synology next to your *arr stack.

The Fire TV app talks only to this gateway. The gateway holds every API key and
password, talks to Radarr / Sonarr / Seerr / File Station / NZBGet / qBittorrent,
and hands back simple, TV-ready JSON (rows of cards, detail pages, file listings,
download queues).

Config: /config/config.json (see config.example.json). Override with HARBOUR_CONFIG.
"""
import hashlib
import hmac
import xml.etree.ElementTree as ET
import json
import logging
import os
import posixpath
import re
import random
import secrets
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

import requests
from flask import Flask, Response, has_request_context, jsonify, request, stream_with_context

VERSION = os.environ.get("HARBOUR_VERSION") or "3.0.0-dev"
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
LIBS = {}      # Harbour v2's merged Movies / TV libraries
POOL = ThreadPoolExecutor(max_workers=64)  # roomy: pages fan out, and fan-outs nest
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


def action(id_, label, style=None, confirm=None, arg=None, opens=None, choose=None, items=None):
    """A button on a detail page. `opens` makes the app open a screen (e.g. "releases") instead
    of calling the gateway; `choose` makes it ask the user to pick one option first; `items`
    makes it a little menu of further actions (Harbour v2)."""
    return clean({"id": id_, "label": label, "style": style, "confirm": confirm, "arg": arg,
                  "opens": opens, "choose": choose, "items": items or None})


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
        self._refreshing_keys = set()
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

    def cached(self, key, ttl, fn, stale=0):
        """Cache fn() for ttl seconds. With `stale`, an older copy (up to that age) is returned
        straight away while a background thread fetches a fresh one — the TV never waits on it."""
        with self._lock:
            hit = self._cache.get(key)
        age = time.time() - hit[0] if hit else None
        if hit and age < ttl:
            return hit[1]
        if hit and age < stale:
            with self._lock:
                busy = key in self._refreshing_keys
                self._refreshing_keys.add(key)
            if not busy:
                def refresh():
                    try:
                        v = fn()
                        with self._lock:
                            self._cache[key] = (time.time(), v)
                    except Exception as e:
                        log.warning("refresh %s %s failed: %s", self.name, key, e)
                    finally:
                        with self._lock:
                            self._refreshing_keys.discard(key)
                threading.Thread(target=refresh, daemon=True).start()
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
        return self.cached("movies", 60, lambda: self.get("/movie"), stale=1800)

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
        elif act == "delete_file":
            self.delete(f"/moviefile/{int(arg)}")
            msg = "File deleted"
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
        return self.cached("series", 60, lambda: self.get("/series"), stale=1800)

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
                    "number": e.get("episodeNumber", 0),
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


def first_meta(x):
    """Plex sometimes returns a single item as an object instead of a one-item list."""
    if isinstance(x, list):
        return x[0] if x else None
    return x if isinstance(x, dict) else None


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
        m = first_meta(self.get(f"/library/metadata/{rk}", token=token, includeGuids=1, includeOnDeck=1)
                       .get("Metadata"))
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
            deck = first_meta((m.get("OnDeck") or {}).get("Metadata"))
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


def is_4k(s):
    return "4k" in (getattr(s, "icon", "") or "").lower() or "4k" in (s.name or "").lower() \
        or "4k" in (s.inst or "").lower() or "uhd" in (s.inst or "").lower()


_TMDB_CACHE = {}


def tmdb_direct(path, **params):
    """Straight to TMDB, only when a key is configured ("tmdb": {"api_key": ...}). Extras only:
    title logos and season trailers. Returns None without a key or on any error."""
    key = (CFG.get("tmdb") or {}).get("api_key")
    if not key:
        return None
    ck = path + repr(sorted(params.items()))
    hit = _TMDB_CACHE.get(ck)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    headers = {}
    if len(key) > 40:  # a v4 "read access token"
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    try:
        r = requests.get(f"https://api.themoviedb.org/3{path}", params=params, headers=headers, timeout=15)
        val = r.json() if r.status_code < 400 else None
    except (requests.RequestException, ValueError) as e:
        log.warning("tmdb %s failed: %s", path, e)
        val = None
    _TMDB_CACHE[ck] = (time.time(), val)
    return val


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
        self._dash = None
        self._dash_lock = threading.Lock()
        self._dash_refreshing = False

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
                "backdrop": self.plex.art(m.get("art") or m.get("grandparentArt") or m.get("thumb"), 1920, 1080),
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
        title = d.get("title") or d.get("name") or ""
        coll = d.get("collection") or {}
        # Read the request now: everything below runs at once on worker threads.
        v2, who = is_v2(), plex_user_from_request()

        def plex_lookup():
            if not self.plex:
                return None
            token = safe(lambda: self.plex.token_for(*who), "plex token", None)
            return safe(lambda: self.plex.find(mt, tid, title, token), "plex search", None) if token else None

        got = par(
            page=lambda: self.seerr.detail_from(mt, tid, d),
            owned=self.owned,
            ratings=lambda: self.ratings(mt, tid, d),
            pm=plex_lookup,
            r=lambda: safe(lambda: self.seerr.cached(f"recs-{mt}-{tid}", 3600, lambda: self.seerr._req(
                "GET", f"/{mt}/{tid}/recommendations", {"page": 1}).get("results")), "recommendations", []),
            s=lambda: safe(lambda: self.seerr.cached(f"sim-{mt}-{tid}", 3600, lambda: self.seerr._req(
                "GET", f"/{mt}/{tid}/similar", {"page": 1}).get("results")), "similar", []),
            c=lambda: safe(lambda: self.seerr.cached(f"coll-{coll['id']}", 3600, lambda: self.seerr._req(
                "GET", f"/collection/{coll['id']}")).get("parts"), "collection", [])
            if (v2 and coll.get("id")) else [],
            logo=lambda: self.logo(mt, tid) if v2 else None,
        )
        page, owned, ratings, pm = got["page"], got["owned"], got["ratings"], got["pm"]

        # headline facts: director/creator, ratings
        extra = []
        crew = (d.get("credits") or {}).get("crew") or []
        if mt == "movie":
            directors = [c.get("name") for c in crew if c.get("job") == "Director"]
            extra.append(("Director", ", ".join(directors[:3])))
        else:
            extra.append(("Created by", ", ".join(c.get("name", "") for c in d.get("createdBy") or [])))
        if not v2:
            extra.append(("Ratings", join(*(f"{r['label']} {r['value']}" for r in ratings if r["label"] != "TMDB"))))
            if d.get("tagline"):
                page["item"]["overview"] = f"“{d['tagline']}”\n\n{page['item'].get('overview') or ''}".strip()
        page["facts"] = facts(*extra) + page["facts"]

        # Plex: watched state + play
        play_acts, plex_state = [], None
        if pm:
            if mt == "movie":
                if pm.get("viewOffset"):
                    left = max(0, ((pm.get("duration") or 0) - pm["viewOffset"]) // 60000)
                    plex_state = f"In progress · {left}m left"
                else:
                    plex_state = "Watched" if pm.get("viewCount") else "Not watched"
            else:
                plex_state = f"{pm.get('viewedLeafCount', 0)} of {pm.get('leafCount', 0)} episodes watched"
            page["facts"].insert(0, {"label": "Plex", "value": plex_state})
            rk = pm.get("ratingKey")
            if v2:
                if mt == "movie" and pm.get("viewOffset"):
                    left = max(0, ((pm.get("duration") or 0) - pm["viewOffset"]) // 60000)
                    play_acts = [
                        action("play", f"Resume · {left}m left", "primary", opens="play", arg=f"plex:{rk}"),
                        action("play_start", "Play from start", opens="play", arg=f"plex:{rk}|start")]
                else:
                    play_acts = [action("play", "Play" if mt == "movie" else "Play next", "primary",
                                        opens="play", arg=f"plex:{rk}")]
            else:
                play_acts = [action("plex", "▶  Play in Plex", "primary", opens="plex",
                                    arg=f"{self.plex.machine_id()}|{rk}")]

        videos = self.videos(d.get("relatedVideos") or [])

        # cast + recommendations
        cast = sorted((d.get("credits") or {}).get("cast") or [], key=lambda c: c.get("order", 999))[:24]
        page["people"] = [clean({"id": f"person-{c['id']}", "name": c.get("name") or "?",
                                 "role": c.get("character"), "photo": tmdb_img(c.get("profilePath"), "w185")})
                          for c in cast if c.get("id")]
        rows = []
        if got["c"]:
            parts = sorted(got["c"], key=lambda p: p.get("releaseDate") or "9999")
            rows.append(row(coll.get("name") or "Collection", [self.mark(c, owned) for c in self.seerr.cards(parts, "movie")]))
        rows += [
            row("More like this", [self.mark(c, owned) for c in self.seerr.cards(got["r"], mt)]),
            row("Similar", [self.mark(c, owned) for c in self.seerr.cards(got["s"], mt)]),
        ]
        page["rows"] = [r for r in rows if r["items"]]

        if not v2:
            page["actions"][0:0] = play_acts
            trailer = next((v for v in videos if v["type"] == "Trailer"), videos[0] if videos else None)
            if trailer:
                page["actions"].append(action("trailer", "Trailer", opens="youtube", arg=trailer["key"]))
            return page

        # Have the first trailer ready before anyone presses the button.
        if videos and trailers_available():
            yt_prefetch(next((v for v in videos if v.get("type") == "Trailer"), videos[0])["key"])

        # ---- Harbour v2: one page that does everything for this title
        matches = self.seerr.library_matches(mt, d)
        page.update(
            tagline=d.get("tagline") or None,
            meta=self.meta_line(mt, d),
            ratings=ratings,
            status=self.status_chips(mt, d, matches, plex_state),
            actions=self.v2_actions(mt, tid, d, matches, play_acts, videos),
            videos=videos,
            providers=self.providers(d),
            seasonCards=self.season_cards(tid, d, matches) if mt == "tv" else [],
            seasons=[],
            logo=got["logo"],
        )
        page["item"]["backdrop"] = tmdb_img(d.get("backdropPath"), "w1280") or page["item"].get("backdrop")
        return clean(page)

    # -- pieces of the v2 title page
    def ratings(self, mt, tid, d):
        out = []
        if mt == "movie":
            rt = safe(lambda: self.seerr.cached(f"ratings-{tid}", 86400,
                                                lambda: self.seerr._req("GET", f"/movie/{tid}/ratingscombined")),
                      "ratings", {}) or {}
            imdb = (rt.get("imdb") or {}).get("criticsScore")
            if imdb:
                out.append({"label": "IMDb", "value": f"{imdb}"})
            r = rt.get("rt") or {}
        else:
            r = safe(lambda: self.seerr.cached(f"ratings-tv-{tid}", 86400,
                                               lambda: self.seerr._req("GET", f"/tv/{tid}/ratings")),
                     "ratings", {}) or {}
        if r.get("criticsScore"):
            out.append({"label": "Rotten Tomatoes", "value": f"{r['criticsScore']}%"})
        if r.get("audienceScore"):
            out.append({"label": "Audience", "value": f"{r['audienceScore']}%"})
        if d.get("voteAverage"):
            out.append({"label": "TMDB", "value": f"{d['voteAverage']:.1f}"})
        return out

    @staticmethod
    def certification(mt, d):
        prefs = (CFG.get("region") or "AU", "US")
        if mt == "movie":
            res = (d.get("releases") or {}).get("results") or []
            for p in prefs:
                for r in res:
                    if r.get("iso_3166_1") == p:
                        c = next((x.get("certification") for x in r.get("release_dates") or [] if x.get("certification")), None)
                        if c:
                            return c
        else:
            res = (d.get("contentRatings") or {}).get("results") or []
            for p in prefs:
                c = next((r.get("rating") for r in res if r.get("iso_3166_1") == p and r.get("rating")), None)
                if c:
                    return c
        return None

    def meta_line(self, mt, d):
        genres = " · ".join(g.get("name", "") for g in (d.get("genres") or [])[:3])
        if mt == "movie":
            return [x for x in (year_of(d.get("releaseDate")), self.certification(mt, d),
                                fmt_runtime(d.get("runtime")), genres) if x]
        first, last = year_of(d.get("firstAirDate")), year_of(d.get("lastAirDate"))
        ended = (d.get("status") or "") in ("Ended", "Canceled")
        years = f"{first}–{last}" if ended and last and last != first else (first if ended else f"{first}–") if first else None
        n = d.get("numberOfSeasons")
        return [x for x in (years, self.certification(mt, d), f"{n} season{'s' if n != 1 else ''}" if n else None,
                            genres) if x]

    @staticmethod
    def videos(vids):
        order = {"Trailer": 0, "Teaser": 1, "Clip": 2, "Featurette": 3, "Behind the Scenes": 4}
        yt = [v for v in vids if v.get("site") == "YouTube" and v.get("key")]
        yt.sort(key=lambda v: order.get(v.get("type"), 9))
        return [clean({"key": v["key"], "name": v.get("name") or v.get("type") or "Video", "type": v.get("type"),
                       "thumb": f"https://i.ytimg.com/vi/{v['key']}/mqdefault.jpg"}) for v in yt[:20]]

    def providers(self, d):
        region = CFG.get("region") or "AU"
        for wp in d.get("watchProviders") or []:
            if wp.get("iso_3166_1") == region:
                return [clean({"name": p.get("name"), "logo": tmdb_img(p.get("logoPath"), "w92")})
                        for p in sorted(wp.get("flatrate") or [], key=lambda p: p.get("displayPriority", 99))][:6]
        return []

    def logo(self, mt, tid):
        """A clear title logo (needs the optional TMDB key)."""
        res = tmdb_direct(f"/{mt}/{tid}/images", include_image_language="en,null")
        logos = [l for l in (res or {}).get("logos") or [] if (l.get("file_path") or "").endswith(".png")]
        return tmdb_img(logos[0]["file_path"], "w500") if logos else None

    def status_chips(self, mt, d, matches, plex_state):
        chips = []
        if mt == "movie":
            for s, m in matches:
                if m.get("hasFile"):
                    chips.append({"text": join(s.name, Radarr.quality(m), fmt_size(m.get("sizeOnDisk"))), "tone": "ok"})
                elif not m.get("monitored"):
                    chips.append({"text": f"{s.name} · unmonitored", "tone": "muted"})
                elif m.get("isAvailable"):
                    chips.append({"text": f"{s.name} · missing", "tone": "warn"})
                else:
                    chips.append({"text": f"{s.name} · waiting for release", "tone": "info"})
        else:
            for s, m in matches:
                st = m.get("statistics") or {}
                have, total = st.get("episodeFileCount", 0), st.get("episodeCount", 0)
                chips.append({"text": join(s.name, f"{have} of {total} episodes",
                                           fmt_size(st.get("sizeOnDisk")) if st.get("sizeOnDisk") else None),
                              "tone": "ok" if total and have >= total else "warn"})
            status = d.get("status")
            if status:
                chips.append({"text": {"Returning Series": "Returning series"}.get(status, status), "tone": "muted"})
            nx = d.get("nextEpisodeToAir") or {}
            if nx.get("airDate"):
                chips.append({"text": f"Next: S{nx.get('seasonNumber', 0):02d}E{nx.get('episodeNumber', 0):02d}"
                                      f" · {fmt_date(nx['airDate'])}", "tone": "info"})
        if not matches:
            st = (d.get("mediaInfo") or {}).get("status")
            if st in REQUESTED:
                chips.append({"text": SEERR_STATUS[st][0], "tone": SEERR_STATUS[st][1]})
            else:
                chips.append({"text": "Not in your library", "tone": "muted"})
        if plex_state:
            chips.append({"text": f"Plex · {plex_state}", "tone": "muted"})
        return chips

    def v2_actions(self, mt, tid, d, matches, play_acts, videos):
        title = d.get("title") or d.get("name") or "this"
        acts = list(play_acts)
        if videos:
            t = next((v for v in videos if v.get("type") == "Trailer"), videos[0])
            acts.append(action("trailer", "Trailer", opens="trailer", arg=t["key"]))
        mi = d.get("mediaInfo") or {}
        st, st4k = mi.get("status"), mi.get("status4k")
        have_4k = any(is_4k(s) for s, _ in matches)
        if not matches and st not in REQUESTED:
            acts.append(action("request", "Add to library" if mt == "movie" else "Add series", "primary",
                               choose=self.seerr.folder_choice(mt, False)))
        if self.seerr.has_4k(mt) and not have_4k and st4k not in REQUESTED:
            acts.append(action("request4k", "Add in 4K", choose=self.seerr.folder_choice(mt, True)))
        for s, m in matches:
            tgt = f"{s.kind}/{s.inst}/{m['id']}"
            if mt == "movie":
                if not m.get("hasFile"):
                    acts.append(action("lib_search", f"Search · {s.name}" if len(matches) > 1 else "Search",
                                       "primary", arg=tgt))
                items = [
                    action("lib_search", "Auto search", "primary", arg=tgt),
                    action("releases", "Manual search", opens="releases", arg=f"{tgt}|movie"),
                    action("lib_unmonitor", "Unmonitor", arg=tgt) if m.get("monitored")
                    else action("lib_monitor", "Monitor", arg=tgt),
                ]
                mf = m.get("movieFile") or {}
                if m.get("hasFile") and mf.get("id"):
                    items.append(action("lib_delete_file", "Delete file", "danger", arg=f"{tgt}|{mf['id']}",
                                        confirm=f"Delete the {s.name} file for “{title}”? Radarr keeps the movie, "
                                                f"so it can download it again."))
                items += [
                    action("lib_delete", f"Remove from {s.name}", arg=tgt,
                           confirm=f"Remove “{title}” from {s.name}? Files stay on the NAS."),
                    action("lib_delete_files", "Remove + delete files", "danger", arg=tgt,
                           confirm=f"Remove “{title}” from {s.name} AND delete its files? This can't be undone."),
                ]
            else:
                stt = m.get("statistics") or {}
                if stt.get("episodeFileCount", 0) < stt.get("episodeCount", 0):
                    acts.append(action("lib_search", "Search missing", "primary", arg=tgt))
                items = [
                    action("lib_search", "Search all missing", "primary", arg=tgt),
                    action("lib_unmonitor", "Unmonitor series", arg=tgt) if m.get("monitored")
                    else action("lib_monitor", "Monitor series", arg=tgt),
                    action("lib_delete", f"Remove from {s.name}", arg=tgt,
                           confirm=f"Remove “{title}” from {s.name}? Files stay on the NAS."),
                    action("lib_delete_files", "Remove + delete files", "danger", arg=tgt,
                           confirm=f"Remove “{title}” from {s.name} AND delete every episode file? "
                                   f"This can't be undone."),
                ]
            acts.append(action("menu", s.name, opens="menu", items=items))
        if mi.get("id") and (st or st4k or mi.get("requests")):
            acts.append(action("menu", "More", opens="menu", items=[action(
                "clear", f"Clear from {self.seerr.name}", "danger",
                confirm=f"Clear “{title}” from {self.seerr.name}? Its requests and status are wiped so it can be "
                        f"requested again. Files, Radarr and Sonarr aren't touched.")]))
        return acts

    def season_cards(self, tid, d, matches):
        stats = {}
        if matches:
            for x in matches[0][1].get("seasons") or []:
                stats[x.get("seasonNumber")] = x
        out = []
        seasons = sorted(d.get("seasons") or [], key=lambda x: (x.get("seasonNumber", 0) == 0, x.get("seasonNumber", 0)))
        for s in seasons:
            n = s.get("seasonNumber", 0)
            if not s.get("episodeCount"):
                continue
            sst = (stats.get(n) or {}).get("statistics") or {}
            have, total = sst.get("episodeFileCount"), sst.get("totalEpisodeCount") or sst.get("episodeCount")
            badge, tone = None, None
            if matches and total:
                badge = f"{have or 0}/{total}"
                tone = "ok" if (have or 0) >= total else ("warn" if (stats.get(n) or {}).get("monitored") else "muted")
            out.append(clean({
                "key": f"tv-{tid}:s{n}", "id": f"tv-{tid}:s{n}",
                "title": "Specials" if n == 0 else (s.get("name") or f"Season {n}"),
                "subtitle": join(f"{s['episodeCount']} episodes", year_of(s.get("airDate"))),
                "poster": tmdb_img(s.get("posterPath"), "w342") or tmdb_img(d.get("posterPath"), "w342"),
                "overview": s.get("overview"),
                "badge": badge, "badgeTone": tone,
            }))
        return out

    # -- a season, episode by episode (TMDB for the story, Sonarr for files, Plex for watched)
    def season(self, item_id, n):
        mt, _, tid = item_id.partition("-")
        if mt != "tv" or not tid.isdigit():
            raise ApiError("Bad show id")
        tid, n = int(tid), int(n)
        d = self._tmdb("tv", tid)
        title = d.get("name") or ""
        matches = self.seerr.library_matches("tv", d)
        so, ser = matches[0] if matches else (None, None)
        who = plex_user_from_request()  # read now: the fetches below run on worker threads
        got = par(
            tm=lambda: safe(lambda: self.seerr.cached(f"tmdb-season-{tid}-{n}", 600,
                                                      lambda: self.seerr._req("GET", f"/tv/{tid}/season/{n}")),
                            "tmdb season", {}) or {},
            eps=lambda: safe(lambda: so.get("/episode", seriesId=ser["id"], seasonNumber=n, includeEpisodeFile="true"),
                             "sonarr episodes", []) if so else [],
            queue=lambda: safe(lambda: {r.get("episodeId") for r in so.get("/queue", page=1, pageSize=250)
                                        .get("records", [])}, "sonarr queue", set()) if so else set(),
            plex=lambda: self.plex_season(tid, title, n, who),
            vids=lambda: tmdb_direct(f"/tv/{tid}/season/{n}/videos") or {},
        )
        tm, se_by = got["tm"], {e.get("episodeNumber"): e for e in got["eps"]}
        te_by = {e.get("episodeNumber"): e for e in tm.get("episodes") or []}
        now = datetime.now(timezone.utc)
        tgt = f"{so.kind}/{so.inst}/{ser['id']}" if so else None
        episodes = []
        for num in sorted(set(te_by) | set(se_by)):
            te, se, pl = te_by.get(num) or {}, se_by.get(num), got["plex"].get(num)
            air = parse_dt(se.get("airDateUtc")) if se and se.get("airDateUtc") else parse_dt(te.get("airDate"))
            air_utc = air.astimezone(timezone.utc) if air and air.tzinfo else (
                air.replace(tzinfo=timezone.utc) if air else None)
            ef = (se or {}).get("episodeFile") or {}
            quality = ((ef.get("quality") or {}).get("quality") or {}).get("name")
            if se and se.get("hasFile"):
                status, label = "downloaded", join("Downloaded", quality, fmt_size(ef.get("size")) if ef.get("size") else None)
            elif se and se.get("id") in got["queue"]:
                status, label = "downloading", "Downloading"
            elif air_utc and air_utc > now:
                status, label = "upcoming", f"Airs {fmt_date(se.get('airDateUtc') if se else te.get('airDate'), True)}"
            elif se and not se.get("monitored"):
                status, label = "unmonitored", "Unmonitored"
            elif se:
                status, label = "missing", "Missing"
            else:
                status, label = "none", "Not in your library"
            acts = []
            if pl:
                off, dur = pl.get("viewOffset") or 0, pl.get("duration") or 0
                acts.append(action("play", f"Resume · {max(0, (dur - off) // 60000)}m left" if off else "Play",
                                   "primary", opens="play", arg=f"plex:{pl['ratingKey']}"))
                if off:
                    acts.append(action("play_start", "Play from start", opens="play", arg=f"plex:{pl['ratingKey']}|start"))
            if se:
                eid = se["id"]
                acts += [
                    action("lib_episode_search", "Auto search", None if se.get("hasFile") else "primary",
                           arg=f"{tgt}|{eid}"),
                    action("releases", "Manual search", opens="releases", arg=f"{tgt}|episode:{eid}"),
                    action("lib_episode_unmonitor", "Unmonitor", arg=f"{tgt}|{eid}") if se.get("monitored")
                    else action("lib_episode_monitor", "Monitor", arg=f"{tgt}|{eid}"),
                ]
                if se.get("hasFile") and se.get("episodeFileId"):
                    acts.append(action("lib_episode_delete_file", "Delete file", "danger",
                                       arg=f"{tgt}|{se['episodeFileId']}",
                                       confirm=f"Delete the file for S{n:02d}E{num:02d} of {title}? "
                                               f"This can't be undone."))
            still = tmdb_img(te.get("stillPath"), "w780")
            if not still and se:
                still = so.image((se or {}).get("images"), "screenshot", "w780")
            episodes.append(clean({
                "key": f"e{num}", "number": num, "label": f"E{num:02d}",
                "title": te.get("name") or (se or {}).get("title") or "TBA",
                "overview": te.get("overview") or (se or {}).get("overview"),
                "still": still,
                "airDate": fmt_date(se.get("airDateUtc") if se and se.get("airDateUtc") else te.get("airDate"), True),
                "runtime": fmt_runtime(te.get("runtime") or (ser or {}).get("runtime")),
                "rating": f"{te['voteAverage']:.1f}" if te.get("voteAverage") else None,
                "status": status, "statusLabel": label,
                "watched": bool(pl and pl.get("viewCount")),
                "progress": round(pl["viewOffset"] / pl["duration"], 3) if pl and pl.get("viewOffset") and pl.get("duration") else None,
                "actions": acts,
            }))
        # season-level actions
        sacts = []
        sstat = next((x for x in (ser or {}).get("seasons") or [] if x.get("seasonNumber") == n), None)
        if so:
            sacts += [
                action("lib_season_search", "Search season", "primary", arg=f"{tgt}|{n}"),
                action("releases", "Manual search", opens="releases", arg=f"{tgt}|season:{n}"),
                action("lib_season_unmonitor", "Unmonitor season", arg=f"{tgt}|{n}") if (sstat or {}).get("monitored")
                else action("lib_season_monitor", "Monitor season", arg=f"{tgt}|{n}"),
            ]
        else:
            mi_seasons = {x.get("seasonNumber"): x.get("status") for x in (d.get("mediaInfo") or {}).get("seasons") or []}
            if mi_seasons.get(n) not in REQUESTED:
                sacts.append(action("request_season", f"Add season {n}", "primary", arg=str(n),
                                    choose=self.seerr.folder_choice("tv", False)))
        have = sum(1 for e in got["eps"] if e.get("hasFile"))
        tm_season = next((s for s in d.get("seasons") or [] if s.get("seasonNumber") == n), {})
        return {
            "show": clean({"id": f"tv-{tid}", "title": title,
                           "backdrop": tmdb_img(d.get("backdropPath"), "w1280"),
                           "poster": tmdb_img(d.get("posterPath"), "w342")}),
            "season": clean({
                "number": n, "label": "Specials" if n == 0 else (tm.get("name") or tm_season.get("name") or f"Season {n}"),
                "overview": tm.get("overview") or tm_season.get("overview"),
                "poster": tmdb_img(tm.get("posterPath") or tm_season.get("posterPath"), "w342"),
                "meta": join(f"{len(episodes)} episodes", year_of(tm.get("airDate") or tm_season.get("airDate")),
                             f"{have} downloaded" if so else None),
            }),
            "seasons": [{"number": s.get("seasonNumber", 0),
                         "label": "Specials" if s.get("seasonNumber", 0) == 0 else f"Season {s.get('seasonNumber')}"}
                        for s in sorted(d.get("seasons") or [], key=lambda x: (x.get("seasonNumber", 0) == 0,
                                                                               x.get("seasonNumber", 0)))
                        if s.get("episodeCount")],
            "actions": sacts,
            "episodes": episodes,
            "videos": self.videos(got["vids"].get("results") or []),
        }

    def plex_season(self, tid, title, n, who):
        if not self.plex:
            return {}
        user, pin = who
        token = safe(lambda: self.plex.token_for(user, pin), "plex token", None)
        if not token:
            return {}
        pm = safe(lambda: self.plex.find("tv", tid, title, token), "plex search", None)
        if not pm:
            return {}
        leaves = safe(lambda: self.plex.leaves(pm["ratingKey"], token), "plex leaves", [])
        return {x.get("index"): x for x in leaves if x.get("parentIndex") == n}

    # -- v2 Home: a dashboard
    def drop_dash(self):
        """Something changed: the next visit gets the old page instantly and a fresh one is built."""
        if self._dash:
            self._dash = (0, self._dash[1])

    def refresh_dash(self):
        if self._dash_refreshing:
            return
        self._dash_refreshing = True
        try:
            val = self._build_dash()
            self._dash = (time.time(), val)
        except Exception as e:
            log.warning("dashboard refresh failed: %s", e)
        finally:
            self._dash_refreshing = False

    def dash(self):
        hit = self._dash
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        if hit and hit[1].get("day") == datetime.now().astimezone().date().isoformat():
            threading.Thread(target=self.refresh_dash, daemon=True).start()
            return hit[1]
        with self._dash_lock:
            hit = self._dash
            if hit and time.time() - hit[0] < 60:
                return hit[1]
            val = self._build_dash()
            self._dash = (time.time(), val)
            return val

    def disk(self):
        """(free, total) bytes across the volumes your libraries live on."""
        for a in self.radarrs() + self.sonarrs():
            got = safe(lambda: par(d=lambda: a.get("/diskspace"), r=lambda: a.get("/rootfolder")),
                       f"{a.name} disk", None)
            if not got:
                continue
            roots = [r.get("path") or "" for r in got["r"]]
            def owner(rt):  # the most specific volume holding this root folder
                cands = [v for v in got["d"] if v.get("totalSpace") and
                         (rt == v.get("path") or rt.startswith((v.get("path") or "").rstrip("/") + "/"))]
                return max(cands, key=lambda v: len(v.get("path") or ""), default=None)
            vols = [v for v in (owner(rt) for rt in roots) if v]
            if not vols:
                vols = sorted(got["d"], key=lambda v: -(v.get("totalSpace") or 0))[:1]
            seen, free, total = set(), 0, 0
            for v in vols:
                k = (v.get("freeSpace"), v.get("totalSpace"))
                if k in seen:
                    continue
                seen.add(k)
                free += v.get("freeSpace") or 0
                total += v.get("totalSpace") or 0
            if total:
                return free, total
        return None

    def health(self):
        out = []
        for a in self.radarrs() + self.sonarrs():
            for h in safe(lambda: a.get("/health"), f"{a.name} health", []) or []:
                if h.get("type") in ("warning", "error"):
                    out.append({"text": f"{a.name}: {h.get('message')}", "tone": "danger" if h["type"] == "error" else "warn"})
        return out[:5]

    def _build_dash(self):
        now = datetime.now().astimezone()
        today = now.date()
        d = par(
            cal=lambda: safe(lambda: calendar((today - timedelta(days=7)).isoformat(),
                                              (today + timedelta(days=56)).isoformat()),
                             "calendar", {"entries": []}),
            radarr=lambda: [(r, safe(r.movies, r.name, [])) for r in self.radarrs()],
            sonarr=lambda: [(s, safe(s.series, s.name, [])) for s in self.sonarrs()],
            disk=lambda: safe(self.disk, "disk", None),
            health=lambda: safe(self.health, "health", []),
            dl=lambda: safe(downloads_snapshot, "downloads", None) if CLIENTS else None,
            trending=lambda: safe(lambda: self.seerr.row_page("trending", 1)["items"], "trending", []),
        )
        ents = d["cal"]["entries"]
        iso_today = today.isoformat()

        # stats
        movies, on_disk, msize, uhd = {}, set(), 0, 0
        for r, ms in d["radarr"]:
            for m in ms:
                k = m.get("tmdbId") or f"{r.inst}:{m.get('id')}"
                movies[k] = m
                msize += m.get("sizeOnDisk") or 0
                if m.get("hasFile"):
                    on_disk.add(k)
                    uhd += 1 if is_4k(r) else 0
        shows, airing, eps, eptotal, tsize = {}, 0, 0, 0, 0
        for so, ss in d["sonarr"]:
            for s in ss:
                k = s.get("tvdbId") or f"{so.inst}:{s.get('id')}"
                if k not in shows and s.get("status") in ("continuing", "upcoming"):
                    airing += 1
                shows[k] = s
                st = s.get("statistics") or {}
                eps += st.get("episodeFileCount") or 0
                eptotal += st.get("episodeCount") or 0
                tsize += st.get("sizeOnDisk") or 0
        stats = []
        if d["radarr"]:
            stats.append({"label": "Movies", "value": f"{len(movies):,}",
                          "sub": join(f"{len(on_disk):,} on disk", f"{uhd:,} in 4K" if uhd else None)})
        if d["sonarr"]:
            stats.append({"label": "TV shows", "value": f"{len(shows):,}", "sub": f"{airing:,} still airing"})
            stats.append({"label": "Episodes", "value": f"{eps:,}", "sub": f"of {eptotal:,} aired"})
        if msize or tsize:
            stats.append({"label": "Library size", "value": fmt_size(msize + tsize),
                          "sub": join(f"Movies {fmt_size(msize)}" if msize else None,
                                      f"TV {fmt_size(tsize)}" if tsize else None)})
        if d["disk"]:
            free, total = d["disk"]
            stats.append({"label": "Free space", "value": fmt_size(free), "sub": f"of {fmt_size(total)}",
                          "tone": "warn" if total and free / total < 0.1 else None})
        if d["dl"] is not None:
            active = [i for i in d["dl"]["items"] if (i.get("progress") or 0) < 1 and not i.get("paused")]
            speed = sum(c.get("speed") or 0 for c in d["dl"]["clients"])
            stats.append({"label": "Downloading", "value": f"{len(active)}",
                          "sub": f"{fmt_size(speed)}/s" if speed else "idle"})
        stats = [clean(s) for s in stats]

        def open_id(e):
            if e["kind"] == "tv" and e.get("open") and e.get("season") is not None:
                return f"{e['open']}:s{e['season']}e{e.get('episode', 0)}"
            return e.get("open") or f"{e['svc']}/{e['id']}"

        def card(e, badge=None, tone="info"):
            return clean({"key": f"dash:{e['key']}", "id": open_id(e), "title": e["title"],
                          "subtitle": e.get("subtitle"), "poster": e.get("poster"), "backdrop": e.get("backdrop"),
                          "overview": e.get("overview"), "badge": badge, "badgeTone": tone})

        # week strip (today + 6)
        week = []
        for i in range(7):
            day = today + timedelta(days=i)
            es = [e for e in ents if e["date"] == day.isoformat()]
            week.append({"date": day.isoformat(), "dow": "Today" if i == 0 else day.strftime("%a"),
                         "day": day.day, "count": len(es),
                         "markers": [{"kind": e["kind"], "status": e["status"]} for e in es[:6]],
                         "entries": [dict(e, openId=open_id(e)) for e in es]})

        yesterday = (today - timedelta(days=1)).isoformat()
        ready = sorted((e for e in ents if e["kind"] == "tv" and e["status"] == "downloaded"
                        and e["date"] <= iso_today), key=lambda e: (e["date"], e.get("sort") or ""), reverse=True)
        ready_cards = [card(e, "New" if e["date"] >= yesterday else fmt_date(e["date"]),
                            "ok" if e["date"] >= yesterday else "muted") for e in ready]
        coming = [e for e in ents if e["kind"] == "tv" and e["date"] >= iso_today
                  and e["status"] in ("upcoming", "airing", "missing", "downloading")
                  and e["date"] <= (today + timedelta(days=7)).isoformat()]
        coming_cards = [card(e, ("Today" + (f" {e['time']}" if e.get("time") else "")) if e["date"] == iso_today
                             else fmt_date(e["date"])) for e in coming]
        new_movies = []
        cutoff = (now - timedelta(days=21)).astimezone(timezone.utc).isoformat()
        for r, ms in d["radarr"]:
            for m in ms:
                added = (m.get("movieFile") or {}).get("dateAdded") or ""
                if m.get("hasFile") and m.get("tmdbId") and added >= cutoff[:19]:
                    c = r.card(m)
                    c.update(key=f"dash:movie-{m['tmdbId']}", id=f"movie-{m['tmdbId']}")
                    new_movies.append((added, c))
        new_movies = [c for _, c in sorted(new_movies, key=lambda x: x[0], reverse=True)]
        soon = [e for e in ents if e["kind"] == "movie" and e["date"] > iso_today and e["status"] != "downloaded"]
        soon_cards = [card(e, fmt_date(e["date"]), "info") for e in soon]
        for c, e in zip(soon_cards, soon):
            c["subtitle"] = join(e.get("label"), (e.get("subtitle") or "").split(" · ", 1)[-1] if e.get("subtitle") else None)
        owned = self.owned()
        rows = [r for r in (
            row("Ready to watch", dedupe(ready_cards, 30), "landscape"),
            row("Coming up this week", dedupe(coming_cards, 30), "landscape"),
            row("New movies", dedupe(new_movies, 30)),
            row("Coming soon to your library", dedupe(soon_cards, 30)),
            row("Trending", [self.mark(c, owned) for c in d["trending"]], key="trending", more=True),
        ) if r["items"]]

        hour = now.hour
        greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        todays = [e for e in ents if e["date"] == iso_today]
        for e in todays:
            e["openId"] = open_id(e)
        hero = next((e.get("backdrop") for e in todays if e.get("backdrop")), None) \
            or next((c.get("backdrop") for r in rows for c in r["items"] if c.get("backdrop")), None)
        return {
            "day": iso_today,
            "greeting": greet,
            "date": f"{now.strftime('%A')} {now.day} {now.strftime('%B')}",
            "hero": hero,
            "stats": stats,
            "today": todays,
            "week": week,
            "rows": rows,
            "alerts": d["health"],
        }

    # -- pass library actions on to the right Radarr/Sonarr
    @staticmethod
    def _target(arg):
        target, _, sub = (arg or "").partition("|")
        parts = target.split("/", 2)
        if len(parts) != 3:
            raise ApiError("That action is missing its library")
        return svc(parts[0], parts[1]), parts[2], (sub or None)

    def releases(self, item_id, arg=None):
        s, tid, sub = self._target(arg)
        return s.releases(tid, sub)

    def grab_for(self, item_id, guid, indexer_id, arg=None):
        s, _, _ = self._target(arg)
        return s.grab(guid, indexer_id)


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
        if act and act.startswith("lib_"):
            target, tid, sub = self._target(arg)
            res = dict(target.act(tid, act[4:], sub, choice))
            res["close"] = False  # stay on the title page; it just shows the new state
            res["refresh"] = True
        else:
            res = self.seerr.act(item_id, act, arg, choice)
        self.drop_home()
        self.drop_dash()
        return res



# ---------------------------------------------------------------- v2 libraries

class Library:
    """Harbour v2's Movies and TV tabs: every Radarr (or every Sonarr) merged, one card per title,
    each opening the one-page-per-title view from the Home hub."""
    kind = "lib"

    def __init__(self, inst, name, icon, member_kind):
        self.inst, self.name, self.icon, self.member_kind = inst, name, icon, member_kind

    def info(self):
        return {"kind": self.kind, "inst": self.inst, "name": self.name, "icon": self.icon}

    def members(self):
        return [s for s in SERVICES.values() if s.kind == self.member_kind]

    def retarget(self, c, s, first):
        c = dict(c)
        ref = c.get("ref") or ""
        if re.fullmatch(r"(movie|tv)-\d+", ref):
            c["id"] = ref
            c["key"] = f"lib:{ref}"
        else:
            c["id"] = f"{s.kind}~{s.inst}~{c['id']}"  # no '/': it travels in URL paths
            c["key"] = f"lib:{c['key']}"
        if not first:
            c["subtitle"] = join(c.get("subtitle"), s.name)
        return c

    def home_cached(self):
        return self.home()

    def home(self):
        ms = self.members()
        pages = par(**{str(i): (lambda s=s: safe(s.home_cached, s.name, {"rows": []})) for i, s in enumerate(ms)})
        rows, order = {}, []
        for i, s in enumerate(ms):
            for r in pages[str(i)].get("rows") or []:
                t = r["title"]
                if t not in rows:
                    rows[t] = {"title": t, "style": r.get("style", "poster"), "items": []}
                    order.append(t)
                rows[t]["items"] += [self.retarget(c, s, i == 0) for c in r.get("items") or []]
        out = []
        for t in order:
            r = rows[t]
            r["items"] = dedupe(r["items"], 80)
            if r["items"]:
                out.append(r)
        hero = next((i for r in out for i in r["items"] if i.get("backdrop")), None)
        return {"hero": hero, "rows": out}

    def search(self, q):
        items = []
        for i, s in enumerate(self.members()):
            items += [self.retarget(c, s, i == 0) for c in safe(lambda: s.search(q)["items"], s.name, [])]
        items = dedupe(items, 100000)
        items.sort(key=lambda c: re.sub(r"^(the|a|an) ", "", (c.get("title") or "").lower()))
        return {"items": items}

    def row_page(self, key, page=1):
        raise ApiError("This row can't be expanded")

    @staticmethod
    def _split(item_id):
        parts = item_id.split("~", 2)
        return (svc(parts[0], parts[1]), parts[2]) if len(parts) == 3 else (None, item_id)

    def _hub(self):
        if not HUB:
            raise ApiError("This needs Jellyseerr configured")
        return HUB

    def detail(self, item_id):
        s, tid = self._split(item_id)
        return s.detail(tid) if s else self._hub().detail(tid)

    def act(self, item_id, act, arg=None, choice=None):
        s, tid = self._split(item_id)
        return s.act(tid, act, arg, choice) if s else self._hub().act(tid, act, arg, choice)

    def releases(self, item_id, arg=None):
        s, tid = self._split(item_id)
        return s.releases(tid, arg) if s else self._hub().releases(tid, arg)

    def grab_for(self, item_id, guid, indexer_id, arg=None):
        s, _ = self._split(item_id)
        return s.grab(guid, indexer_id) if s else self._hub().grab_for(item_id, guid, indexer_id, arg)

    def season(self, item_id, n):
        return self._hub().season(item_id, n)


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


class _MultipartFile:
    """A multipart/form-data body that reads the file from disk as it's sent (never all in memory)."""

    def __init__(self, fields, file_field, filename, path):
        self.boundary = f"----harbour{int(time.time() * 1000)}"
        head = b""
        for k, v in fields.items():
            head += (f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        safe_name = filename.replace('"', "'")
        head += (f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                 f"filename=\"{safe_name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        self.parts = [head, None, f"\r\n--{self.boundary}--\r\n".encode()]
        self.f = open(path, "rb")
        self.size = len(head) + os.path.getsize(path) + len(self.parts[2])
        self.idx, self.off = 0, 0
        self.content_type = f"multipart/form-data; boundary={self.boundary}"

    def __len__(self):
        return self.size

    def read(self, n=-1):
        n = n if n and n > 0 else 1 << 20
        out = b""
        while len(out) < n and self.idx < 3:
            if self.idx == 1:
                chunk = self.f.read(n - len(out))
                if not chunk:
                    self.idx += 1
                    continue
                out += chunk
            else:
                part = self.parts[self.idx]
                chunk = part[self.off:self.off + n - len(out)]
                out += chunk
                self.off += len(chunk)
                if self.off >= len(part):
                    self.idx, self.off = self.idx + 1, 0
        return out

    def close(self):
        self.f.close()


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

    def upload(self, folder, name, src_path):
        """Stream a local file into a NAS folder (created if needed) via File Station's upload API."""
        folder, name = self.check(folder), self.name_ok(name)
        for attempt in range(2):
            with self.lock:
                if not self.sid:
                    self.login()
                sid = self.sid
            body = _MultipartFile({"path": folder, "create_parents": "true", "overwrite": "true"},
                                  "file", name, src_path)
            try:
                r = requests.post(f"{self.url}/webapi/entry.cgi",
                                  params={"api": "SYNO.FileStation.Upload", "version": "2", "method": "upload",
                                          "_sid": sid},
                                  data=body, headers={"Content-Type": body.content_type,
                                                      "Content-Length": str(len(body))},
                                  timeout=900, verify=self.verify).json()
            finally:
                body.close()
            if r.get("success"):
                return f"{folder}/{name}"
            code = (r.get("error") or {}).get("code")
            if code in (106, 107, 119) and attempt == 0:
                with self.lock:
                    self.sid = None
                continue
            raise ApiError(FS_ERRORS.get(code) or f"File Station upload error {code}", 502)
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

    def stream(self, path, range_header=None):
        """Open a NAS file through File Station's download API for streaming (Range passed through)."""
        path = self.check(path)
        for attempt in range(2):
            with self.lock:
                if not self.sid:
                    self.login()
                sid = self.sid
            hdr = {"Range": range_header} if range_header else {}
            r = requests.get(f"{self.url}/webapi/entry.cgi", params={
                "api": "SYNO.FileStation.Download", "version": "2", "method": "download",
                "path": json.dumps([path], ensure_ascii=False), "mode": "open", "_sid": sid,
            }, headers=hdr, stream=True, timeout=(10, 120), verify=self.verify)
            if r.headers.get("Content-Type", "").startswith(("application/json", "text/plain")) and r.status_code == 200:
                # An error comes back as JSON instead of the file.
                try:
                    err = (json.loads(r.content or b"{}").get("error") or {}).get("code")
                except ValueError:
                    return r
                if err in (106, 107, 119) and attempt == 0:
                    with self.lock:
                        self.sid = None
                    continue
                raise ApiError(FS_ERRORS.get(err) or f"File Station error {err}", 400)
            return r
        raise ApiError("File Station session error", 502)

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

    def categories(self):
        conf = self.rpc("config") or []
        return [o.get("Value") for o in conf
                if re.fullmatch(r"Category\d+\.Name", o.get("Name") or "") and o.get("Value")]

    def add_nzb(self, url, title, category=""):
        name = re.sub(r"[\\/:*?\"<>|]", "_", title)[:200] + ".nzb"
        # append(NZBFilename, Content-or-URL, Category, Priority, AddToTop, AddPaused, DupeKey, DupeScore, DupeMode, PPParameters)
        nid = self.rpc("append", name, url, category, 0, False, False, "", 0, "SCORE", [])
        if not nid or (isinstance(nid, int) and nid <= 0):
            raise ApiError(f"{self.name} couldn't add that NZB")
        return nid

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

    def categories(self):
        return sorted((self.req("GET", "/torrents/categories").json() or {}).keys())

    def add_torrent(self, magnet, url, category=None):
        data = {"category": category} if category else {}
        if magnet:
            self.req("POST", "/torrents/add", data=dict(data, urls=magnet))
            return
        if not url:
            raise ApiError("That result has no download link")
        # Fetch it here: indexer links often redirect to a magnet, which qBittorrent won't follow.
        r = requests.get(url, allow_redirects=False, timeout=30)
        loc = r.headers.get("Location") or ""
        while r.is_redirect and loc and not loc.startswith("magnet:"):
            r = requests.get(loc, allow_redirects=False, timeout=30)
            loc = r.headers.get("Location") or ""
        if loc.startswith("magnet:"):
            self.req("POST", "/torrents/add", data=dict(data, urls=loc))
        elif r.status_code < 400 and r.content:
            self.req("POST", "/torrents/add", data=data, files={"torrents": ("release.torrent", r.content,
                                                                             "application/x-bittorrent")})
        else:
            raise ApiError(f"Couldn't fetch the torrent (HTTP {r.status_code})", 502)

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



# ---------------------------------------------------------------- Trailers, played inside Harbour
#
# yt-dlp finds the actual video and audio streams behind a YouTube trailer; the gateway then
# relays them to the TV so Harbour's own player can play them (no YouTube app, no leaving
# Harbour). YouTube changes things often, so the gateway keeps its own copy of yt-dlp up to
# date in the background (falls back to the copy baked into the image).

YT_DIR = os.environ.get("HARBOUR_YTDLP_DIR", "/tmp/harbour-ytdlp")
YT_FORMAT = ("bv*[height<=1080][vcodec^=avc1][ext=mp4]+ba[ext=m4a]/"
             "bv*[height<=1080][vcodec^=avc1]+ba/b[height<=1080][ext=mp4]/b[height<=1080]/b")
_YT = {}


def trailer_conf():
    c = CFG.get("trailers", {})
    return c if isinstance(c, dict) else {"enabled": bool(c)}


def _yt_env():
    env = dict(os.environ)
    if os.path.isdir(os.path.join(YT_DIR, "yt_dlp")):
        env["PYTHONPATH"] = YT_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("HOME", "/tmp")
    env.setdefault("XDG_CACHE_HOME", "/tmp/.cache")
    env.setdefault("DENO_DIR", "/tmp/.deno")
    return env


def trailers_available():
    if trailer_conf().get("enabled", True) is False:
        return False
    import importlib.util
    return os.path.isdir(os.path.join(YT_DIR, "yt_dlp")) or importlib.util.find_spec("yt_dlp") is not None


YT_POOL = ThreadPoolExecutor(max_workers=2)   # at most two yt-dlp runs at once
_YT_FUT = {}
_YT_LOCK = threading.Lock()
YT_CHUNK = 8 * 1024 * 1024   # YouTube cuts long open-ended reads short, so fetch in pieces


def _yt_lookup(key):
    cmd = [sys.executable, "-m", "yt_dlp", "-J", "--no-warnings", "--no-playlist", "--no-progress",
           "-f", YT_FORMAT, f"https://www.youtube.com/watch?v={key}"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=_yt_env())
    except subprocess.TimeoutExpired:
        raise ApiError("YouTube took too long to answer", 504)
    if p.returncode != 0:
        last = [ln for ln in (p.stderr or "").strip().splitlines() if ln.strip()][-1:]
        raise ApiError(f"YouTube: {last[0][:220] if last else 'lookup failed'}", 502)
    try:
        info = json.loads(p.stdout)
    except ValueError:
        raise ApiError("YouTube: unreadable answer", 502)
    streams = []
    for f in info.get("requested_formats") or [info]:
        if f.get("url"):
            streams.append({"url": f["url"], "headers": f.get("http_headers") or info.get("http_headers") or {},
                            "height": f.get("height"), "size": f.get("filesize") or f.get("filesize_approx")})
    if not streams:
        raise ApiError("YouTube: no playable stream", 502)
    out = {"title": info.get("title"), "duration": info.get("duration"), "streams": streams}
    _YT[key] = (time.time(), out)
    return out


def yt_prefetch(key):
    """Start looking a trailer up in the background (no-op if it's cached or already running)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", key or ""):
        return None
    hit = _YT.get(key)
    if hit and time.time() - hit[0] < 3 * 3600:
        return None
    with _YT_LOCK:
        fut = _YT_FUT.get(key)
        if fut is None or (fut.done() and fut.exception() is not None):
            fut = YT_POOL.submit(_yt_lookup, key)
            _YT_FUT[key] = fut
            if len(_YT_FUT) > 200:
                for k in [k for k, f in _YT_FUT.items() if f.done()][:100]:
                    _YT_FUT.pop(k, None)
        return fut


def yt_resolve(key, fresh=False):
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", key or ""):
        raise ApiError("Bad video id")
    if fresh:
        _YT.pop(key, None)
        with _YT_LOCK:
            _YT_FUT.pop(key, None)
    hit = _YT.get(key)
    if hit and time.time() - hit[0] < 3 * 3600:
        return hit[1]
    fut = yt_prefetch(key)
    return fut.result(timeout=120) if fut else _YT[key][1]


def yt_update_loop():
    """Keep a fresh yt-dlp in YT_DIR (daily). Harmless if pip or the network isn't there."""
    if not trailers_available() or trailer_conf().get("auto_update", True) is False:
        return
    time.sleep(30)
    while True:
        try:
            p = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                                "--no-cache-dir", "--upgrade", "--target", YT_DIR, "yt-dlp[default]"],
                               capture_output=True, text=True, timeout=600, env=_yt_env())
            if p.returncode == 0:
                log.info("yt-dlp is up to date")
            else:
                log.warning("yt-dlp update failed: %s", (p.stderr or "")[-300:])
        except Exception as e:
            log.warning("yt-dlp update failed: %s", e)
        time.sleep(86400)


# ---------------------------------------------------------------- X / Twitter video downloads
#
# Paste a post link (x.com or twitter.com) on the iPhone app's Downloads page: the gateway asks
# yt-dlp which sizes the video comes in, then downloads the one you pick and saves it on the NAS
# through File Station (default /data/media/twitter, i.e. /volume1/data/media/twitter).
# Only X's single-file MP4 renditions are offered, so no ffmpeg is needed in the image.

X_URL = re.compile(r"^https?://(?:www\.|mobile\.)?(x\.com|twitter\.com|fxtwitter\.com|vxtwitter\.com|fixupx\.com|"
                   r"fixvx\.com)/([A-Za-z0-9_]{1,30}|i(?:/web)?)/status(?:es)?/(\d{5,25})", re.I)
X_POOL = ThreadPoolExecutor(max_workers=2)
_X_JOBS = {}            # job id -> dict (newest last)
_X_LOOKUPS = {}         # tweet id -> (time, yt-dlp info)
_X_LOCK = threading.Lock()
_X_SIZES = {}           # direct mp4 url -> real byte size (X's bitrates overstate it)
_X_SRC = {}             # job id -> (direct mp4 url, headers) — kept on the gateway


def x_conf():
    c = CFG.get("twitter") or {}
    return c if isinstance(c, dict) else {}


def x_folder():
    return (x_conf().get("folder") or "/data/media/twitter").rstrip("/")


def x_available():
    import importlib.util
    return FILES is not None and (os.path.isdir(os.path.join(YT_DIR, "yt_dlp"))
                                  or importlib.util.find_spec("yt_dlp") is not None)


def x_parse(url):
    m = X_URL.match((url or "").strip())
    if not m:
        raise ApiError("That doesn't look like an X post link (x.com/…/status/…)")
    user = "i" if m.group(2).lower().startswith("i/") else m.group(2)
    return m.group(3), f"https://x.com/{user}/status/{m.group(3)}"


def _x_cmd(*args):
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-progress" if "-J" in args else "--newline"]
    cookies = x_conf().get("cookies")
    if cookies and os.path.isfile(cookies):
        cmd += ["--cookies", cookies]
    return cmd + list(args)


def _x_err(stderr):
    last = [ln for ln in (stderr or "").strip().splitlines() if ln.strip()][-1:]
    msg = last[0] if last else "lookup failed"
    msg = re.sub(r"^ERROR:\s*(\[\w+\]\s*\d*:?\s*)?", "", msg)
    if re.search(r"No video could be found", msg, re.I):
        return "That post doesn't have a video"
    if re.search(r"login|authenticat|NSFW|age.restrict|protected|sensitive", msg, re.I):
        c = x_conf().get("cookies")
        if c and os.path.isfile(c):
            return ("X refused this post even with your sign-in. The sign-in may have expired (paste fresh values in "
                    "Settings → X sign-in), or your X account needs 'Display media that may contain sensitive content' on")
        return "X needs a signed-in account for this post — add one in Settings → X sign-in"
    return f"X: {msg[:200]}"


def _x_options(entry):
    """The single-file MP4 sizes X offers for one video, biggest first."""
    dur = entry.get("duration") or 0
    seen, out = set(), []
    for f in entry.get("formats") or []:
        if not str(f.get("format_id", "")).startswith("http") or f.get("ext") not in (None, "mp4"):
            continue
        w, hgt = f.get("width"), f.get("height")
        if not hgt:
            m = re.search(r"/(\d{2,4})x(\d{2,4})/", f.get("url") or "")
            if m:
                w, hgt = int(m.group(1)), int(m.group(2))
        key = (w, hgt)
        if key in seen:
            continue
        seen.add(key)
        size = f.get("filesize") or f.get("filesize_approx") or \
            (int(f["tbr"] * 1000 / 8 * dur) if f.get("tbr") and dur else None)
        out.append({"format": f["format_id"], "width": w, "height": hgt, "size": size,
                    "_url": f.get("url"), "_headers": f.get("http_headers") or {}})
    out.sort(key=lambda o: ((o["height"] or 0) * (o["width"] or 0), o["size"] or 0), reverse=True)
    for o in out:
        short = min(o["width"] or 0, o["height"] or 0)
        o["label"] = f'{o["width"]}×{o["height"]}' if o["height"] else "Original"
        o["quality"] = "Full HD" if short >= 1000 else "HD" if short >= 700 else "SD" if short >= 400 else "Small"
    if not out:   # nothing progressive: let yt-dlp pick the best single file
        out.append({"format": "b", "label": "Best available", "quality": "", "size": None,
                    "width": entry.get("width"), "height": entry.get("height")})
    return out


def _x_real_sizes(videos):
    """Ask X for each file's real size. The bitrate X advertises is a ceiling, so estimates from it
    can be 5x too big; a HEAD request per size gives the true number."""
    def head(u, hdr):
        if u in _X_SIZES:
            return _X_SIZES[u]
        r = requests.head(u, headers={k: v for k, v in hdr.items() if k.lower() == "user-agent"},
                          timeout=8, allow_redirects=True)
        n = int(r.headers.get("Content-Length") or 0) if r.ok else 0
        if n:
            _X_SIZES[u] = n
            if len(_X_SIZES) > 500:
                _X_SIZES.clear()
        return n
    opts = [o for v in videos for o in v["options"] if o.get("_url")]
    got = par(**{str(i): (lambda o=o: safe(lambda: head(o["_url"], o.get("_headers") or {}), "X size", 0))
                 for i, o in enumerate(opts)})
    for i, o in enumerate(opts):
        n = got[str(i)]
        if n:
            o["size"], o["exact"] = n, True


def x_lookup(url, public=True):
    tid, url = x_parse(url)
    hit = _X_LOOKUPS.get(tid)
    if hit and time.time() - hit[0] < 1800:
        info = hit[1]
    else:
        try:
            p = subprocess.run(_x_cmd("-J", url), capture_output=True, text=True, timeout=90, env=_yt_env())
        except subprocess.TimeoutExpired:
            raise ApiError("X took too long to answer", 504)
        if p.returncode != 0:
            raise ApiError(_x_err(p.stderr), 502)
        try:
            info = json.loads(p.stdout)
        except ValueError:
            raise ApiError("X: unreadable answer", 502)
        _X_LOOKUPS[tid] = (time.time(), info)
        if len(_X_LOOKUPS) > 100:
            for k in sorted(_X_LOOKUPS, key=lambda k: _X_LOOKUPS[k][0])[:50]:
                _X_LOOKUPS.pop(k, None)
    entries = [e for e in (info.get("entries") or [info]) if e]
    first = entries[0] if entries else info
    videos = [{"index": i + 1, "duration": e.get("duration"), "thumbnail": e.get("thumbnail"),
               "options": _x_options(e)} for i, e in enumerate(entries)]
    _x_real_sizes(videos)
    if public:
        for v in videos:
            v["options"] = [{k: val for k, val in o.items() if not k.startswith("_")} for o in v["options"]]
    text = (first.get("description") or info.get("description") or first.get("title") or "").strip()
    text = re.sub(r"\s*https://t\.co/\w+\s*$", "", text)
    return {"id": tid, "url": url, "text": text[:400],
            "author": first.get("uploader") or info.get("uploader"),
            "handle": first.get("uploader_id") or info.get("uploader_id"),
            "date": first.get("upload_date") or info.get("upload_date"),
            "videos": videos, "folder": x_folder()}


def _x_name(meta, index, count, height):
    handle = re.sub(r"[^\w-]", "", meta.get("handle") or "x") or "x"
    d = meta.get("date") or ""
    day = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else datetime.now().strftime("%Y-%m-%d")
    part = f" ({index} of {count})" if count > 1 else ""
    size = f" {height}p" if height else ""
    return f"{handle} {day} {meta['id']}{part}{size}.mp4"


def _x_fetch_direct(job, url, headers, dest):
    """Download exactly the MP4 the user picked (X serves each size as its own file)."""
    job["status"] = "downloading"
    hdr = {k: v for k, v in (headers or {}).items() if k.lower() in ("user-agent", "referer", "accept")}
    with requests.get(url, headers=hdr, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(512 * 1024):
                f.write(chunk)
                done += len(chunk)
                if total:
                    job["progress"] = min(done / total * 0.9, 0.9)
    if os.path.getsize(dest) < 1024:
        raise ValueError("file too small")
    return dest


def _x_save(job, src):
    job["size"] = os.path.getsize(src)
    job["status"], job["progress"] = "saving", 0.92
    job["path"] = FILES.upload(job["folder"], job["name"], src)
    job["status"], job["progress"] = "done", 1.0
    log.info("saved X video %s (%s)", job["path"], fmt_size(job["size"]))


def x_forget(job_id=None):
    """Remove one finished download from the list, or every finished one."""
    with _X_LOCK:
        ids = [job_id] if job_id else list(_X_JOBS)
        for i in ids:
            j = _X_JOBS.get(i)
            if j and j.get("finished"):
                _X_JOBS.pop(i)
                _X_SRC.pop(i, None)
    return x_jobs()


def _x_run(job):
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="harbour-x-", dir=os.environ.get("TMPDIR", "/tmp"))
    try:
        src = _X_SRC.pop(job["id"], None)
        got = None
        if src:
            try:
                got = _x_fetch_direct(job, src[0], src[1], os.path.join(tmp, "video.mp4"))
            except Exception as e:   # fall back to yt-dlp below
                log.info("X direct download failed (%s), using yt-dlp", e)
                for f in os.listdir(tmp):
                    os.remove(os.path.join(tmp, f))
        if got:
            _x_save(job, got)
            return
        fmt = job["format"]
        if job.get("height"):   # pick by size, so the format id can't drift between lookup and download
            fmt = f"b[height={job['height']}]/{fmt}/b"
        args = ["-f", fmt, "-o", os.path.join(tmp, "video.%(ext)s"), "--no-mtime"]
        if job["count"] > 1:
            args += ["--playlist-items", str(job["index"])]
        else:
            args += ["--no-playlist"]
        proc = subprocess.Popen(_x_cmd(*args, job["url"]), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=_yt_env())
        job["status"] = "downloading"
        for line in proc.stdout:
            m = re.search(r"\[download\]\s+([\d.]+)%", line)
            if m:
                job["progress"] = min(float(m.group(1)) / 100 * 0.9, 0.9)
        err = proc.stderr.read()
        if proc.wait(timeout=1800) != 0:
            raise ApiError(_x_err(err), 502)
        files_ = [os.path.join(tmp, f) for f in os.listdir(tmp) if not f.endswith(".part")]
        if not files_:
            raise ApiError("yt-dlp didn't produce a file", 502)
        _x_save(job, max(files_, key=os.path.getsize))
    except requests.RequestException:
        job["status"], job["error"] = "failed", "Couldn't reach File Station on the NAS to save the video"
        log.warning("X download %s: File Station unreachable", job["url"])
    except Exception as e:
        job["status"], job["error"] = "failed", getattr(e, "message", None) or str(e)
        log.warning("X download %s failed: %s", job["url"], job["error"])
    finally:
        job["finished"] = time.time()
        shutil.rmtree(tmp, ignore_errors=True)


def x_download(url, index=1, fmt=None):
    if not x_available():
        raise ApiError("X downloads need File Station set up on the gateway", 404)
    meta = x_lookup(url, public=False)
    vids = meta["videos"]
    try:
        index = int(index or 1)
    except (TypeError, ValueError):
        raise ApiError("Bad video number")
    if not 1 <= index <= len(vids):
        raise ApiError("That post doesn't have that many videos")
    opt = next((o for o in vids[index - 1]["options"] if o["format"] == fmt), None)
    if not opt:
        raise ApiError("That size isn't available any more — look the post up again")
    job = {"id": secrets.token_hex(6), "url": meta["url"], "tweet": meta["id"], "index": index,
           "count": len(vids), "format": opt["format"], "label": opt["label"], "quality": opt["quality"],
           "author": meta.get("author"), "handle": meta.get("handle"), "text": meta.get("text"),
           "thumbnail": vids[index - 1].get("thumbnail"), "folder": x_folder(),
           "name": _x_name(meta, index, len(vids), opt.get("height")),
           "status": "queued", "progress": 0.0, "size": opt.get("size"), "started": time.time()}
    FILES.check(job["folder"])   # fail now, not after the download, if the folder isn't allowed
    with _X_LOCK:
        _X_JOBS[job["id"]] = job
        if opt.get("_url"):
            _X_SRC[job["id"]] = (opt["_url"], opt.get("_headers") or {})
        job["height"] = opt.get("height")
        while len(_X_JOBS) > 30:
            old = next((k for k, j in _X_JOBS.items() if j.get("finished")), None)
            if not old:
                break
            _X_JOBS.pop(old)
            _X_SRC.pop(old, None)
    X_POOL.submit(_x_run, job)
    return {"job": job, "message": f"Downloading to {job['folder']}"}


def x_jobs():
    with _X_LOCK:
        return {"jobs": list(reversed(list(_X_JOBS.values()))), "folder": x_folder()}



# ---------------------------------------------------------------- Indexer search
#
# Search your indexers directly — Prowlarr (torrent + usenet) and/or Newznab indexers listed in
# the config — and send a result straight to qBittorrent or NZBGet. Download links (which carry
# indexer API keys) never leave the gateway: the TV only sees an id.

IDX_CATS = {"movies": "2000", "tv": "5000"}
_IDX_RESULTS = {}   # id -> (time, source info for grabbing)
_IDX_LOCK = threading.Lock()


def _age(dt):
    d = parse_dt(dt) if isinstance(dt, str) else None
    if not d:
        return None, None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    days = max(0, (datetime.now(timezone.utc) - d).total_seconds() / 86400)
    if days < 1:
        return f"{int(days * 24) or 1}h", days
    if days < 60:
        return f"{int(days)}d", days
    if days < 730:
        return f"{int(days / 30)}mo", days
    return f"{int(days / 365)}y", days


def _cat_label(cats):
    ids = [int(c) for c in cats if str(c).isdigit()]
    for i in ids:
        if 2000 <= i < 3000:
            return "Movies"
        if 5000 <= i < 6000:
            return "TV"
    for i in ids:
        if 3000 <= i < 4000:
            return "Audio"
        if 7000 <= i < 8000:
            return "Books"
        if 1000 <= i < 2000:
            return "Console"
        if 4000 <= i < 5000:
            return "PC"
    return None


class Prowlarr:
    def __init__(self, conf):
        self.name = conf.get("name", "Prowlarr")
        self.url = conf["url"].rstrip("/")
        self.key = conf["api_key"]

    def search(self, q, kind):
        params = [("query", q), ("type", "search"), ("limit", "100")]
        if kind in IDX_CATS:
            params.append(("categories", IDX_CATS[kind]))
        r = requests.get(f"{self.url}/api/v1/search", params=params, headers={"X-Api-Key": self.key}, timeout=90)
        if r.status_code >= 400:
            raise ApiError(f"{self.name}: HTTP {r.status_code}", 502)
        out = []
        for x in r.json() or []:
            proto = "torrent" if (x.get("protocol") or "").lower() == "torrent" else "usenet"
            age, days = _age(x.get("publishDate"))
            out.append(({
                "title": x.get("title") or "?", "indexer": x.get("indexer") or self.name, "protocol": proto,
                "size": int(x.get("size") or 0), "age": age, "ageDays": days,
                "seeders": x.get("seeders"), "leechers": x.get("leechers"), "grabs": x.get("grabs"),
                "category": _cat_label([c.get("id") for c in x.get("categories") or [] if isinstance(c, dict)]),
            }, {"via": "prowlarr", "src": self, "guid": x.get("guid"), "indexerId": x.get("indexerId"),
                "magnet": x.get("magnetUrl"), "url": x.get("downloadUrl"), "title": x.get("title") or "release"}))
        return out

    def grab(self, info):
        r = requests.post(f"{self.url}/api/v1/search", json={"guid": info["guid"], "indexerId": info["indexerId"]},
                          headers={"X-Api-Key": self.key}, timeout=60)
        if r.status_code >= 400:
            try:
                msg = (r.json() or {}).get("message")
            except ValueError:
                msg = None
            raise ApiError(f"{self.name}: {msg or f'HTTP {r.status_code}'}", 502)


class Newznab:
    """A usenet indexer straight from its Newznab API (for indexers that aren't in Prowlarr)."""
    NS = "{http://www.newznab.com/DTD/2010/feeds/attributes/}"

    def __init__(self, conf):
        self.name = conf.get("name", "Indexer")
        self.url = conf["url"].rstrip("/")
        self.api_path = conf.get("api_path", "/api")
        self.key = conf["api_key"]

    def search(self, q, kind):
        params = {"t": "search", "q": q, "apikey": self.key, "limit": "100", "extended": "1"}
        if kind in IDX_CATS:
            params["cat"] = IDX_CATS[kind]
        r = requests.get(f"{self.url}{self.api_path}", params=params, timeout=60)
        if r.status_code >= 400:
            raise ApiError(f"{self.name}: HTTP {r.status_code}", 502)
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            raise ApiError(f"{self.name}: unreadable answer", 502)
        err = root if root.tag == "error" else None
        if err is not None:
            raise ApiError(f"{self.name}: {err.get('description') or 'error'}", 502)
        out = []
        for it in root.iter("item"):
            attrs = {a.get("name"): a.get("value") for a in it.iter(f"{self.NS}attr")}
            enc = it.find("enclosure")
            size = attrs.get("size") or (enc.get("length") if enc is not None else 0)
            link = (enc.get("url") if enc is not None else None) or it.findtext("link")
            age, days = _age(it.findtext("pubDate") and _rfc822(it.findtext("pubDate")))
            cats = [a.get("value") for a in it.iter(f"{self.NS}attr") if a.get("name") == "category"]
            out.append(({
                "title": it.findtext("title") or "?", "indexer": self.name, "protocol": "usenet",
                "size": int(size or 0), "age": age, "ageDays": days,
                "grabs": int(attrs["grabs"]) if (attrs.get("grabs") or "").isdigit() else None,
                "category": _cat_label(cats),
            }, {"via": "newznab", "src": self, "url": link, "title": it.findtext("title") or "release"}))
        return out


def _rfc822(s):
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def indexer_sources():
    out = []
    p = CFG.get("prowlarr")
    for c in (p if isinstance(p, list) else [p] if p else []):
        out.append(Prowlarr(c))
    for c in CFG.get("newznab") or []:
        out.append(Newznab(c))
    return out


def indexer_search(q, kind="all", protocol="all"):
    q = (q or "").strip()
    if not q:
        return {"results": [], "errors": []}
    srcs = indexer_sources()
    if not srcs:
        raise ApiError("No indexers set up — add \"prowlarr\" (or \"newznab\") to the gateway config", 404)
    futs = [(s, POOL.submit(s.search, q, kind)) for s in srcs]
    results, errors = [], []
    now = time.time()
    with _IDX_LOCK:
        for k in [k for k, v in _IDX_RESULTS.items() if now - v[0] > 3600]:
            _IDX_RESULTS.pop(k, None)
    for s, f in futs:
        try:
            for pub, info in f.result():
                if protocol != "all" and pub["protocol"] != protocol:
                    continue
                rid = hashlib.sha1(f"{s.name}|{pub['title']}|{info.get('guid') or info.get('url')}".encode()).hexdigest()[:16]
                with _IDX_LOCK:
                    _IDX_RESULTS[rid] = (now, dict(info, protocol=pub["protocol"]))
                results.append(clean(dict(pub, id=rid)))
        except Exception as e:
            errors.append(e.message if isinstance(e, ApiError) else f"{s.name}: {e}")
    seen, uniq = set(), []
    for r in results:
        k = (r["title"].lower(), r["size"], r["protocol"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    uniq.sort(key=lambda r: (-(r.get("seeders") or 0) if r["protocol"] == "torrent" else -(r.get("grabs") or 0),
                             r.get("ageDays") or 9e9))
    return {"results": uniq, "errors": errors}


def grab_targets():
    """Where a result can go: each download client (by its categories), plus Prowlarr's own clients."""
    out = {"torrent": [], "usenet": []}
    for c in CLIENTS.values():
        proto = "torrent" if c.kind == "qbittorrent" else "usenet"
        cats = safe(c.categories, f"{c.name} categories", []) or []
        out[proto].append({"label": f"{c.name} · no category", "value": f"{c.id}|"})
        out[proto] += [{"label": f"{c.name} · {cat}", "value": f"{c.id}|{cat}"} for cat in cats]
    if CFG.get("prowlarr"):
        for proto in out:
            out[proto].append({"label": "Let Prowlarr send it (its own download client)", "value": "prowlarr|"})
    return out


def indexer_grab(rid, target):
    with _IDX_LOCK:
        hit = _IDX_RESULTS.get(rid)
    if not hit:
        raise ApiError("That search result has expired — search again", 410)
    info = hit[1]
    cid, _, cat = (target or "").partition("|")
    if cid == "prowlarr":
        if info["via"] != "prowlarr":
            raise ApiError("Prowlarr can only send its own results")
        info["src"].grab(info)
        return {"message": "Sent via Prowlarr ✓"}
    c = CLIENTS.get(cid)
    if not c:
        raise ApiError("Unknown download client", 404)
    if info["protocol"] == "torrent":
        if c.kind != "qbittorrent":
            raise ApiError("Torrents need qBittorrent")
        c.add_torrent(info.get("magnet"), info.get("url"), cat or None)
    else:
        if c.kind != "nzbget":
            raise ApiError("Usenet results need NZBGet")
        if not info.get("url"):
            raise ApiError("That result has no NZB link")
        c.add_nzb(info["url"], info["title"], cat or "")
    return {"message": f"Sent to {c.name} ✓"}



# ---------------------------------------------------------------- V3: setup page, pairing, auto-detect
#
# Everything you used to do by editing config.json now happens at http://<nas>:8765/setup:
# enter (or auto-detect) each app's address and key, test it, save. TVs and phones pair from
# there too, so nobody types API keys on a remote.

STACK_DIR = os.environ.get("HARBOUR_STACK_DIR", "/stack")
_PAIRS = {}            # pairing id -> {...}
_PAIR_LOCK = threading.Lock()
SETUP_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup.html")


def save_config(new):
    """Write config.json atomically (and privately), then reload everything."""
    os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=2, ensure_ascii=False)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, CONFIG_PATH)
    except OSError as e:
        raise ApiError(f"Couldn't save settings ({e.strerror}). The gateway's config folder must be writable: in "
                       f"docker-compose use ./config:/config (not :ro), and give your Docker user Read/Write on "
                       f"that folder in File Station.", 500)
    init()


def normalise_config(c):
    """Tidy what the setup page sends: drop empty sections, give every Radarr/Sonarr a unique id."""
    def filled(d):
        return isinstance(d, dict) and bool((d.get("url") or "").strip())

    for key in ("radarr", "sonarr", "newznab", "seerr", "nzbget", "qbittorrent"):
        v = c.get(key)
        if isinstance(v, list):
            v = [x for x in v if filled(x)]
            if v:
                c[key] = v
            else:
                c.pop(key, None)
        elif v is not None and not filled(v):
            c.pop(key, None)
    for key in ("plex", "prowlarr", "filestation", "tautulli", "komga"):
        if key in c and not filled(c[key]):
            c.pop(key)
    if isinstance(c.get("tmdb"), dict) and not (c["tmdb"].get("api_key") or "").strip():
        c.pop("tmdb")
    for key in ("radarr", "sonarr"):
        used = set()
        for i, x in enumerate(c.get(key) or []):
            base = re.sub(r"[^a-z0-9]+", "-", (x.get("id") or x.get("name") or key).lower()).strip("-") or key
            ident, n = base, 2
            while ident in used:
                ident, n = f"{base}-{n}", n + 1
            used.add(ident)
            x["id"] = ident
            x.setdefault("name", key.title() if i == 0 else f"{key.title()} {i + 1}")
            for k in ("url",):
                x[k] = x[k].strip().rstrip("/")
    fs = c.get("filestation")
    if isinstance(fs, dict) and isinstance(fs.get("allowed_shares"), str):
        fs["allowed_shares"] = [x.strip() for x in fs["allowed_shares"].split(",") if x.strip()]
    return c


def setup_cookie_value():
    token = CFG.get("token") or ""
    return hmac.new(token.encode(), b"harbour-setup", hashlib.sha256).hexdigest()


def setup_authed():
    if not CFG.get("token"):
        return True  # first run: nothing to protect yet
    given = request.headers.get("X-Token") or ""
    if given and valid_token(given):
        return True
    return hmac.compare_digest(request.cookies.get("hb_setup", ""), setup_cookie_value())


def valid_token(given):
    token = CFG.get("token") or ""
    if token and hmac.compare_digest(token, given):
        return True
    return any(hmac.compare_digest(d.get("token", ""), given) for d in CFG.get("devices") or [] if d.get("token"))


# -- version / updates
def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def gateway_update():
    """Newest published gateway (GitHub release gateway-v*), if newer than this one. Cached 6h."""
    hit = _UPDATE_CACHE.get("gw")
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    repo = CFG.get("github_repo") or DEFAULT_REPO
    newest = None
    try:
        rels = requests.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 50}, timeout=10).json()
        tags = [r.get("tag_name", "") for r in rels if str(r.get("tag_name", "")).startswith("gateway-v")]
        if tags:
            best = max(tags, key=lambda t: version_tuple(t))
            if version_tuple(best) > version_tuple(VERSION) and "dev" not in VERSION:
                newest = best.removeprefix("gateway-v")
    except (requests.RequestException, ValueError, AttributeError):
        pass
    _UPDATE_CACHE["gw"] = (time.time(), newest)
    return newest


_UPDATE_CACHE = {}


# -- pairing: a TV asks, you approve it on the setup page, it gets its own token
def pair_start(name):
    pid = secrets.token_urlsafe(18)
    code = f"{secrets.randbelow(9000) + 1000}"
    with _PAIR_LOCK:
        for k in [k for k, p in _PAIRS.items() if time.time() - p["at"] > 900]:
            _PAIRS.pop(k, None)
        _PAIRS[pid] = {"id": pid, "code": code, "name": (name or "Harbour device")[:60], "at": time.time(),
                       "status": "pending", "token": None, "ip": request.remote_addr}
    return {"id": pid, "code": code, "expires": 600}


def pair_status(pid):
    with _PAIR_LOCK:
        p = _PAIRS.get(pid)
        if not p:
            return {"status": "expired"}
        if p["status"] == "pending" and time.time() - p["at"] > 600:
            p["status"] = "expired"
        out = {"status": p["status"]}
        if p["status"] == "approved" and p["token"]:
            out["token"] = p["token"]
            p["token"] = None  # hand it over once
            _PAIRS.pop(pid, None)
        return out


def pair_decide(pid, approve):
    with _PAIR_LOCK:
        p = _PAIRS.get(pid)
        if not p or p["status"] != "pending":
            raise ApiError("That request has gone — ask the TV to start pairing again", 404)
        if not approve:
            p["status"] = "denied"
            return {"ok": True}
        token = secrets.token_urlsafe(24)
        p["status"], p["token"] = "approved", token
    new = json.loads(json.dumps(CFG))
    new.setdefault("devices", []).append({"name": p["name"], "token": token,
                                          "added": datetime.now().astimezone().isoformat(timespec="minutes")})
    save_config(new)
    return {"ok": True}


# -- auto-detect: read API keys straight from the apps' own config files
SKIP_DIRS = {"cache", "Cache", "logs", "Logs", "MediaCover", "Backups", "backups", "Metadata", "Media", "Codecs",
             "Crash Reports", "Plug-in Support", "Sentry", "transcode", "Transcode", "Diagnostics", "Drivers",
             "Updates", "node_modules", "db", "Scanners", "Plug-ins", "asp", "logs.db"}


def _xml_val(text, tag):
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
    return m.group(1).strip() if m else None


def detect_apps(host):
    """Walk the mounted docker folder for Radarr/Sonarr/Prowlarr (config.xml), Jellyseerr (settings.json),
    Plex (Preferences.xml), NZBGet (nzbget.conf) and qBittorrent (qBittorrent.conf), then check each
    answers on this NAS."""
    if not os.path.isdir(STACK_DIR):
        return {"mounted": False, "found": []}
    found, seen_files = [], 0
    for root, dirs, files in os.walk(STACK_DIR):
        depth = root[len(STACK_DIR):].count(os.sep)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")] if depth < 6 else []
        seen_files += len(files)
        if seen_files > 60000:
            break
        rel = os.path.relpath(root, STACK_DIR)
        top = rel.split(os.sep)[0] if rel != "." else ""
        for fn in files:
            path = os.path.join(root, fn)
            try:
                if fn == "config.xml":
                    txt = open(path, encoding="utf-8", errors="ignore").read(20000)
                    key, port = _xml_val(txt, "ApiKey"), _xml_val(txt, "Port")
                    if not key or not port:
                        continue
                    inst = (_xml_val(txt, "InstanceName") or top or "").strip()
                    low = (inst + " " + top).lower()
                    kind = next((k for k in ("radarr", "sonarr", "prowlarr", "lidarr", "readarr", "whisparr")
                                 if k in low), None)
                    if kind not in ("radarr", "sonarr", "prowlarr"):
                        continue
                    base = (_xml_val(txt, "UrlBase") or "").strip().rstrip("/")
                    if base and not base.startswith("/"):
                        base = "/" + base
                    found.append({"kind": kind, "id": re.sub(r"[^a-z0-9-]", "-", (top or kind).lower()),
                                  "name": inst or top or kind.title(),
                                  "url": f"http://{host}:{port}{base}", "api_key": key, "source": rel})
                elif fn == "settings.json" and ("seerr" in rel.lower() or "overseerr" in rel.lower()):
                    d = json.load(open(path, encoding="utf-8"))
                    key = ((d.get("main") or {}).get("apiKey"))
                    if key:
                        found.append({"kind": "seerr", "id": "seerr",
                                      "name": "Jellyseerr" if "jelly" in rel.lower() else "Overseerr",
                                      "url": f"http://{host}:5055", "api_key": key, "source": rel})
                elif fn == "Preferences.xml" and "plex" in rel.lower():
                    txt = open(path, encoding="utf-8", errors="ignore").read(20000)
                    m = re.search(r'PlexOnlineToken="([^"]+)"', txt)
                    if m:
                        found.append({"kind": "plex", "id": "plex", "name": "Plex",
                                      "url": f"http://{host}:32400", "token": m.group(1), "source": rel})
                elif fn == "nzbget.conf":
                    txt = open(path, encoding="utf-8", errors="ignore").read(200000)
                    def conf(k):
                        m = re.search(rf"^{k}=(.*)$", txt, re.M)
                        return m.group(1).strip() if m else ""
                    found.append({"kind": "nzbget", "id": "nzbget", "name": "NZBGet",
                                  "url": f"http://{host}:{conf('ControlPort') or 6789}",
                                  "username": conf("ControlUsername"), "password": conf("ControlPassword"),
                                  "source": rel})
                elif fn == "config.ini" and "tautulli" in rel.lower():
                    txt = open(path, encoding="utf-8", errors="ignore").read(200000)

                    def ini(k):
                        m = re.search(rf"^{k}\s*=\s*(.*)$", txt, re.M)
                        return m.group(1).strip().strip('"') if m else ""
                    key = ini("api_key")
                    if key:
                        root_ = ini("http_root").strip("/")
                        found.append({"kind": "tautulli", "id": "tautulli", "name": "Tautulli",
                                      "url": f"http://{host}:{ini('http_port') or 8181}" + (f"/{root_}" if root_ else ""),
                                      "api_key": key, "source": rel})
                elif fn == "qBittorrent.conf":
                    txt = open(path, encoding="utf-8", errors="ignore").read(100000)
                    m = re.search(r"^WebUI\\\\Port=(\d+)", txt, re.M)
                    found.append({"kind": "qbittorrent", "id": "qbittorrent", "name": "qBittorrent",
                                  "url": f"http://{host}:{m.group(1) if m else 8080}", "source": rel,
                                  "note": "Add its API key (qBittorrent 5.2+) or username/password"})
            except (OSError, ValueError):
                continue
    # de-dupe (same app found twice) and check each one answers
    uniq = {}
    for f in found:
        uniq.setdefault((f["kind"], f.get("url"), f.get("api_key") or f.get("token")), f)
    found = list(uniq.values())
    futs = [(f, POOL.submit(test_service, f["kind"], f)) for f in found]
    for f, fut in futs:
        try:
            r = fut.result(timeout=20)
        except Exception as e:
            r = {"ok": False, "message": str(e)}
        f["verified"], f["message"] = r["ok"], r["message"]
    found.sort(key=lambda f: ("radarr sonarr seerr plex tautulli prowlarr nzbget qbittorrent".find(f["kind"]), f["name"]))
    return {"mounted": True, "found": found}


# -- connection tests for the setup page
def test_service(kind, c):
    url = (c.get("url") or "").rstrip("/")
    try:
        if kind in ("radarr", "sonarr", "prowlarr"):
            ver = "v1" if kind == "prowlarr" else "v3"
            r = requests.get(f"{url}/api/{ver}/system/status", headers={"X-Api-Key": c.get("api_key", "")}, timeout=8)
            if r.status_code == 401:
                return {"ok": False, "message": "Wrong API key"}
            r.raise_for_status()
            j = r.json()
            return {"ok": True, "message": f"{j.get('appName') or kind.title()} {j.get('version', '')} ✓".strip()}
        if kind == "seerr":
            r = requests.get(f"{url}/api/v1/settings/main", headers={"X-Api-Key": c.get("api_key", "")}, timeout=8)
            if r.status_code in (401, 403):
                return {"ok": False, "message": "Wrong API key"}
            r.raise_for_status()
            st = requests.get(f"{url}/api/v1/status", timeout=8).json()
            return {"ok": True, "message": f"Connected · version {st.get('version', '?')} ✓"}
        if kind == "plex":
            r = requests.get(f"{url}/library/sections", params={"X-Plex-Token": c.get("token", "")},
                             headers={"Accept": "application/json"}, timeout=8)
            if r.status_code == 401:
                return {"ok": False, "message": "Wrong Plex token"}
            r.raise_for_status()
            n = len((r.json().get("MediaContainer") or {}).get("Directory") or [])
            return {"ok": True, "message": f"Connected · {n} libraries ✓"}
        if kind == "komga":
            libs = Komga(c).get("/api/v1/libraries")
            return {"ok": True, "message": f"Komga answered · {len(libs)} librar{'y' if len(libs) == 1 else 'ies'} ✓"}
        if kind == "trakt":
            r = requests.get("https://api.trakt.tv/movies/trending", params={"limit": 1},
                             headers=Trakt(c).headers, timeout=10)
            if r.status_code in (401, 403):
                return {"ok": False, "message": "Trakt rejected that Client ID"}
            r.raise_for_status()
            return {"ok": True, "message": "Trakt answered ✓"}
        if kind == "tautulli":
            d = Tautulli(c).api("get_tautulli_info", timeout=8) or {}
            return {"ok": True, "message": f"Tautulli {d.get('tautulli_version', '')} ✓".replace("  ", " ")}
        if kind == "nzbget":
            v = NzbGet(c).rpc("version")
            return {"ok": True, "message": f"NZBGet {v} ✓"}
        if kind == "qbittorrent":
            v = QBittorrent(c).req("GET", "/app/version").text
            return {"ok": True, "message": f"qBittorrent {v} ✓"}
        if kind == "filestation":
            fs = FileStation(c)
            fs.login()
            shares = fs.call("SYNO.FileStation.List", 2, "list_share").get("shares") or []
            return {"ok": True, "message": f"Logged in · {len(shares)} shared folders ✓"}
        if kind == "newznab":
            r = requests.get(f"{url}{c.get('api_path', '/api')}", params={"t": "caps", "apikey": c.get("api_key", "")},
                             timeout=10)
            if r.status_code >= 400 or b"<error" in r.content[:300]:
                return {"ok": False, "message": "The indexer rejected that key"}
            return {"ok": True, "message": "Indexer answered ✓"}
        if kind == "tmdb":
            key = c.get("api_key", "")
            hdr = {"Authorization": f"Bearer {key}"} if len(key) > 40 else {}
            r = requests.get("https://api.themoviedb.org/3/configuration", headers=hdr,
                             params={} if hdr else {"api_key": key}, timeout=8)
            return {"ok": r.status_code < 400, "message": "TMDB key works ✓" if r.status_code < 400 else "TMDB rejected that key"}
        return {"ok": False, "message": "Nothing to test"}
    except ApiError as e:
        return {"ok": False, "message": e.message}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "message": "Couldn't reach it at that address"}
    except requests.exceptions.Timeout:
        return {"ok": False, "message": "It didn't answer in time"}
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {e}"[:200]}


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
    LIBS.clear()
    if HUB:
        for inst, name, icon, kind in (("movies", "Movies", "movie", "radarr"), ("tv", "TV", "tv", "sonarr")):
            if any(x.kind == kind for x in SERVICES.values()):
                LIBS[("lib", inst)] = Library(inst, name, icon, kind)
    for key, cls in (("nzbget", NzbGet), ("qbittorrent", QBittorrent)):
        conf = CFG.get(key)
        for c in (conf if isinstance(conf, list) else [conf] if conf else []):
            cl = cls(c)
            CLIENTS[cl.id] = cl
    log.info("Loaded %d services, %d download clients, File Station %s",
             len(SERVICES), len(CLIENTS), "on" if FILES else "off")


# ---------------------------------------------------------------- Calendar

CAL_RANK = {"downloaded": 0, "downloading": 1, "airing": 2, "missing": 3, "upcoming": 4, "cinema": 5, "unmonitored": 6}


def _clock(d):
    return d.strftime("%I:%M %p").lstrip("0").lower() if d else None


def calendar(start, end, unmonitored=False):
    """Every Sonarr episode and Radarr release between two local dates (inclusive)."""
    from datetime import date as _date
    s0, s1 = _date.fromisoformat(start), _date.fromisoformat(end)
    if (s1 - s0).days > 70:
        raise ApiError("Calendar range is too long")
    q0, q1 = (s0 - timedelta(days=1)).isoformat(), (s1 + timedelta(days=2)).isoformat()
    now = datetime.now().astimezone()
    sonarrs = [x for x in SERVICES.values() if x.kind == "sonarr"]
    radarrs = [x for x in SERVICES.values() if x.kind == "radarr"]
    um = "true" if unmonitored else "false"

    def fetch_sonarr(so):
        d = par(
            cal=lambda: so.get("/calendar", start=q0, end=q1, unmonitored=um, includeSeries="true"),
            queue=lambda: safe(lambda: so.get("/queue", page=1, pageSize=200).get("records", []),
                               f"{so.name} queue", []),
        )
        return so, d["cal"], {r.get("episodeId") for r in d["queue"]}

    def fetch_radarr(ra):
        d = par(
            cal=lambda: ra.get("/calendar", start=q0, end=q1, unmonitored=um),
            queue=lambda: safe(lambda: ra.get("/queue", page=1, pageSize=200).get("records", []),
                               f"{ra.name} queue", []),
        )
        return ra, d["cal"], {r.get("movieId") for r in d["queue"]}

    futs = [POOL.submit(safe, (lambda so=so: fetch_sonarr(so)), f"{so.name} calendar", None) for so in sonarrs]
    futs += [POOL.submit(safe, (lambda ra=ra: fetch_radarr(ra)), f"{ra.name} calendar", None) for ra in radarrs]
    out, sources = [], []
    for f in futs:
        res = f.result()
        if not res:
            continue
        svc_, cal, queued = res
        sources.append({"svc": f"{svc_.kind}/{svc_.inst}", "kind": svc_.kind, "name": svc_.name})
        if svc_.kind == "sonarr":
            out += _cal_episodes(svc_, cal, queued, s0, s1, now)
        else:
            out += _cal_movies(svc_, cal, queued, s0, s1, now)
    out.sort(key=lambda e: (e["date"], e.get("sort") or "", e["title"]))
    return {"start": start, "end": end, "sources": sources, "entries": out}


def _cal_episodes(so, cal, queued, s0, s1, now):
    out = []
    for e in cal:
        at = parse_dt(e.get("airDateUtc"))
        if not at:
            continue
        day = at.date()
        if not (s0 <= day <= s1):
            continue
        s = e.get("series") or {}
        runtime = timedelta(minutes=s.get("runtime") or 30)
        if e.get("hasFile"):
            st, label = "downloaded", "Downloaded"
        elif e.get("id") in queued:
            st, label = "downloading", "Downloading"
        elif at > now:
            st, label = "upcoming", f"Airs {_clock(at)}"
        elif at + runtime > now:
            st, label = "airing", "On air now"
        elif not e.get("monitored"):
            st, label = "unmonitored", "Unmonitored"
        else:
            st, label = "missing", "Missing"
        tag = None
        fin = (e.get("finaleType") or "").lower()
        if e.get("episodeNumber") == 1:
            tag = "Series premiere" if e.get("seasonNumber") == 1 else "New season"
        elif fin == "series":
            tag = "Series finale"
        elif fin == "season":
            tag = "Season finale"
        imgs = s.get("images")
        out.append(clean({
            "key": f"{so.kind}:{so.inst}:e{e.get('id')}",
            "date": day.isoformat(), "time": _clock(at), "sort": at.isoformat(),
            "kind": "tv", "svc": f"{so.kind}/{so.inst}", "source": so.name,
            "season": e.get("seasonNumber"), "episode": e.get("episodeNumber"), "episodeId": e.get("id"),
            "id": str(e.get("seriesId")), "open": f"tv-{s['tmdbId']}" if s.get("tmdbId") else None,
            "title": s.get("title") or "?",
            "subtitle": join(Sonarr.sxe(e), e.get("title")),
            "poster": so.image(imgs, "poster", "w185"), "backdrop": so.image(imgs, "fanart", "w780"),
            "overview": e.get("overview") or s.get("overview"),
            "status": st, "label": label, "tag": tag, "network": s.get("network"),
        }))
    return out


def _cal_movies(ra, cal, queued, s0, s1, now):
    from datetime import date as _date
    out = []
    today = now.date()
    for m in cal:
        for field, what in (("inCinemas", "In cinemas"), ("digitalRelease", "Digital release"),
                            ("physicalRelease", "Physical release")):
            raw = m.get(field)
            if not raw:
                continue
            try:
                day = _date.fromisoformat(raw[:10])
            except ValueError:
                continue
            if not (s0 <= day <= s1):
                continue
            if m.get("hasFile"):
                st, label = "downloaded", "Downloaded"
            elif m.get("id") in queued:
                st, label = "downloading", "Downloading"
            elif field == "inCinemas":
                st, label = "cinema", what
            elif day > today:
                st, label = "upcoming", what
            elif not m.get("monitored"):
                st, label = "unmonitored", "Unmonitored"
            else:
                st, label = "missing", "Missing"
            imgs = m.get("images")
            out.append(clean({
                "key": f"{ra.kind}:{ra.inst}:{m.get('id')}:{field}",
                "date": day.isoformat(), "sort": "~" + field,
                "kind": "movie", "svc": f"{ra.kind}/{ra.inst}", "source": ra.name,
                "id": str(m.get("id")), "open": f"movie-{m['tmdbId']}" if m.get("tmdbId") else None,
                "title": m.get("title") or "?",
                "subtitle": join(what, m.get("year"), fmt_runtime(m.get("runtime"))),
                "poster": ra.image(imgs, "poster", "w185"), "backdrop": ra.image(imgs, "fanart", "w780"),
                "overview": m.get("overview"),
                "status": st, "label": label, "tag": what if st in ("downloaded", "downloading", "missing") else None,
                "quality": Radarr.quality(m) if m.get("hasFile") else None,
            }))
    return out


def load_config():
    """The saved settings; an empty config (first run) until the setup page saves one."""
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def svc(kind, inst):
    s = SERVICES.get((kind, inst)) or LIBS.get((kind, inst))
    if not s:
        raise ApiError(f"No service {kind}/{inst}", 404)
    return s


def warm_loop():
    """Keep every home page warm so switching tabs on the TV is instant."""
    time.sleep(2)
    while True:
        for s in list(SERVICES.values()):
            s.refresh_home()
        if HUB:
            HUB.refresh_dash()
        time.sleep(240)


PUBLIC = ("/", "/apk", "/Harbour.apk", "/HarbourV2.apk", "/HarbourV3.apk", "/v3", "/setup",
          "/api/pair/start", "/api/pair/status", "/setup/api/login", "/favicon.ico")

import web_access  # noqa: E402  (iPhone web app: login, settings, /m)

WEB = web_access.install(app, get_cfg=lambda: CFG, config_path=CONFIG_PATH, valid_token=valid_token,
                         save_config=lambda c: save_config(normalise_config(c)), test_service=test_service,
                         ApiError=ApiError, FileStation=FileStation)


@app.before_request
def check_token():
    """Home network: the gateway PIN or a paired device key, as before. Through the reverse proxy:
    the iPhone web app's password login (the PIN and device keys aren't accepted from outside)."""
    path = request.path.lower()
    if path in [p.lower() for p in PUBLIC]:
        return WEB["remote_guard"](path)
    if path.startswith("/setup/"):
        blocked = WEB["remote_guard"](path)
        if blocked:
            return blocked
        return None if setup_authed() else (jsonify(error="Log in to the setup page first"), 401)
    return WEB["check_access"]()


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
    return jsonify(error=f"Gateway error: {type(e).__name__} {e}".strip()), 500


@app.get("/")
def root():
    # Apps look for this answer when they search the network for a gateway.
    # Browsers: the iPhone app once the gateway is set up (or from outside), otherwise the setup page.
    if "text/html" in (request.headers.get("Accept") or "") and not request.args.get("json"):
        to = "/m/" if CFG.get("token") or WEB["is_remote"]() else "/setup"
        return Response(status=302, headers={"Location": to})
    return jsonify(name="Harbour gateway", app="harbour-gateway", version=VERSION, ok=True,
                   setup=bool(CFG.get("token")))


@app.get("/setup")
def setup_page():
    with open(SETUP_HTML_PATH, encoding="utf-8") as f:
        html = f.read()
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.post("/setup/api/login")
def setup_login():
    b = request.get_json(silent=True) or {}
    if CFG.get("token") and not hmac.compare_digest(CFG["token"], str(b.get("pin", ""))):
        time.sleep(1)
        return jsonify(error="That's not the gateway PIN"), 401
    resp = jsonify(ok=True)
    resp.set_cookie("hb_setup", setup_cookie_value(), httponly=True, samesite="Strict", max_age=30 * 86400)
    return resp


@app.get("/setup/api/state")
def setup_state():
    cfg = json.loads(json.dumps(CFG))
    devices = [{"index": i, "name": d.get("name"), "added": d.get("added")} for i, d in enumerate(cfg.pop("devices", []) or [])]
    with _PAIR_LOCK:
        pending = [{"id": p["id"], "code": p["code"], "name": p["name"], "ip": p["ip"]}
                   for p in _PAIRS.values() if p["status"] == "pending" and time.time() - p["at"] < 600]
    writable = os.access(os.path.dirname(CONFIG_PATH) or ".", os.W_OK) and \
        (not os.path.exists(CONFIG_PATH) or os.access(CONFIG_PATH, os.W_OK))
    return jsonify(config=cfg, devices=devices, pending=pending, version=VERSION, update=gateway_update(),
                   writable=writable, stack=os.path.isdir(STACK_DIR), firstRun=not CFG.get("token"),
                   host=request.host.split(":")[0])


@app.post("/setup/api/save")
def setup_save():
    b = request.get_json(silent=True) or {}
    new = b.get("config")
    if not isinstance(new, dict):
        raise ApiError("Nothing to save")
    pin = str(new.get("token") or "").strip()
    if len(pin) < 4:
        raise ApiError("Choose a gateway PIN of at least 4 characters")
    new["token"] = pin
    new["devices"] = CFG.get("devices") or []
    save_config(normalise_config(new))
    resp = jsonify(ok=True, message="Saved — the gateway is using your new settings")
    resp.set_cookie("hb_setup", setup_cookie_value(), httponly=True, samesite="Strict", max_age=30 * 86400)
    return resp


@app.post("/setup/api/test")
def setup_test():
    b = request.get_json(silent=True) or {}
    return jsonify(test_service(b.get("kind"), b.get("conf") or {}))


@app.get("/setup/api/detect")
def setup_detect():
    return jsonify(detect_apps(request.host.split(":")[0]))


@app.post("/setup/api/pair")
def setup_pair():
    b = request.get_json(silent=True) or {}
    return jsonify(pair_decide(b.get("id"), bool(b.get("approve"))))


@app.post("/setup/api/device/remove")
def setup_device_remove():
    b = request.get_json(silent=True) or {}
    new = json.loads(json.dumps(CFG))
    devs = new.get("devices") or []
    i = b.get("index")
    if not isinstance(i, int) or not 0 <= i < len(devs):
        raise ApiError("No such device")
    devs.pop(i)
    save_config(new)
    return jsonify(ok=True)


@app.post("/api/pair/start")
def api_pair_start():
    b = request.get_json(silent=True) or {}
    if not CFG.get("token"):
        raise ApiError("Finish setting up the gateway first: open http://<your NAS>:8765/setup", 503)
    return jsonify(pair_start(b.get("name")))


@app.get("/api/pair/status")
def api_pair_status():
    return jsonify(pair_status(request.args.get("id", "")))


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
                   calendar=any(s.kind in ("radarr", "sonarr") for s in SERVICES.values()),
                   libraries=[l.info() for l in LIBS.values()],
                   dashboard=HUB is not None,
                   trailers=trailers_available(),
                   indexers=bool(CFG.get("prowlarr") or CFG.get("newznab")),
                   xdl=x_available(),
                   activity=bool((CFG.get("tautulli") or {}).get("api_key")),
                   discuss=trakt_on(),
                   comics=bool((CFG.get("komga") or {}).get("api_key")),
                   gatewayUpdate=gateway_update(),
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
    if ref.startswith("file:"):
        return jsonify(file_play_info(ref[5:]))
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


@app.get("/HarbourV3.apk")
@app.get("/harbourv3.apk")
@app.get("/v3")
def apk_v3():
    """Newest Harbour V3 build (tags v3.*) — for Downloader on a TV: http://<nas>:8765/v3"""
    repo = CFG.get("github_repo") or DEFAULT_REPO
    rels = requests.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 100}, timeout=20).json()
    asset = next((a for r in rels if str(r.get("tag_name", "")).startswith("v3.")
                  for a in r.get("assets") or [] if a.get("name", "").endswith(".apk")), None)
    if not asset:
        return jsonify(error="No Harbour V3 build published yet"), 404
    r = requests.get(asset["browser_download_url"], stream=True, timeout=60)
    headers = {"Content-Disposition": 'attachment; filename="HarbourV3.apk"'}
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
    d = svc(kind, inst).detail(item_id)
    if trakt_on() and isinstance(d, dict):
        ref = safe(lambda: discuss_ref(kind, inst, item_id), "discussion link", None)
        if ref:
            add_discussion(d, ref)
    return jsonify(d)


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
    if hasattr(s, "grab_for"):  # Home / libraries pass on to the right Radarr/Sonarr
        return jsonify(s.grab_for(item_id, b.get("guid"), b.get("indexerId"), b.get("arg")))
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


# ---------------------------------------------------------------- Tautulli (Plex activity)
#
# Who's watching right now, the watch history and the top titles/users, from Tautulli's API v2.

class Tautulli:
    def __init__(self, conf):
        self.url = conf["url"].rstrip("/")
        self.key = conf.get("api_key", "")

    def api(self, cmd, timeout=15, **params):
        r = requests.get(f"{self.url}/api/v2", params={"apikey": self.key, "cmd": cmd, **params}, timeout=timeout)
        if r.status_code in (401, 403):
            raise ApiError("Tautulli rejected the API key", 502)
        r.raise_for_status()
        resp = (r.json() or {}).get("response") or {}
        if resp.get("result") != "success":
            raise ApiError(f"Tautulli: {resp.get('message') or 'request failed'}", 502)
        return resp.get("data")

    @staticmethod
    def img(path, w=300, h=450):
        return f"/api/activity/img?{urlencode({'img': path, 'w': w, 'h': h})}" if path else None

    @staticmethod
    def _titles(x):
        mt = x.get("media_type")
        if mt == "episode":
            s, e = x.get("parent_media_index"), x.get("media_index")
            ep = f"S{int(s):02d}E{int(e):02d}" if str(s).isdigit() and str(e).isdigit() else ""
            return x.get("grandparent_title") or x.get("full_title"), join(ep, x.get("title"))
        if mt == "track":
            return x.get("title"), join(x.get("grandparent_title"), x.get("parent_title"))
        return x.get("title") or x.get("full_title"), str(x.get("year") or "") or None

    @staticmethod
    def _decision(x):
        d = (x.get("transcode_decision") or "").lower()
        return {"direct play": "Direct play", "copy": "Direct stream", "transcode": "Transcode"}.get(d, d.title() or None)

    def activity(self):
        d = self.api("get_activity", timeout=10) or {}
        streams = []
        for x in d.get("sessions") or []:
            title, sub = self._titles(x)
            poster = x.get("grandparent_thumb") if x.get("media_type") == "episode" else x.get("thumb")
            res = x.get("stream_video_full_resolution") or x.get("video_full_resolution")
            streams.append(clean({
                "id": str(x.get("session_key") or x.get("session_id") or len(streams)),
                "user": x.get("friendly_name") or x.get("user"),
                "title": title, "subtitle": sub, "kind": x.get("media_type"),
                "poster": self.img(poster), "art": self.img(x.get("art"), 1280, 720),
                "state": x.get("state"), "progress": int(x.get("progress_percent") or 0),
                "position": int(x.get("view_offset") or 0) // 1000, "duration": int(x.get("duration") or 0) // 1000,
                "player": x.get("player") or x.get("device"), "platform": x.get("platform"), "product": x.get("product"),
                "quality": join(res, x.get("quality_profile") if x.get("quality_profile") != "Original" else None),
                "decision": self._decision(x),
                "bandwidth": int(x.get("bandwidth") or 0),
                "location": (x.get("location") or "").upper() or None,
            }))
        return {"streams": streams, "count": int(d.get("stream_count") or len(streams)),
                "bandwidth": int(d.get("total_bandwidth") or 0), "lan": int(d.get("lan_bandwidth") or 0),
                "wan": int(d.get("wan_bandwidth") or 0)}

    def history(self, start=0, length=50, user_id=None, media_type=None):
        params = {"start": start, "length": length, "order_column": "date", "order_dir": "desc"}
        if user_id:
            params["user_id"] = user_id
        if media_type in ("movie", "episode", "track"):
            params["media_type"] = media_type
        d = self.api("get_history", **params) or {}
        items = []
        for x in d.get("data") or []:
            title, sub = self._titles(x)
            poster = x.get("grandparent_thumb") if x.get("media_type") == "episode" else x.get("thumb")
            items.append(clean({
                "id": str(x.get("id") or x.get("reference_id") or len(items)),
                "user": x.get("friendly_name") or x.get("user"), "userId": x.get("user_id"),
                "title": title, "subtitle": sub, "kind": x.get("media_type"),
                "poster": self.img(poster or x.get("thumb")),
                "started": x.get("started") or x.get("date"), "stopped": x.get("stopped"),
                "watched": int(x.get("play_duration") or x.get("duration") or 0),
                "percent": int(x.get("percent_complete") or 0),
                "complete": (x.get("watched_status") or 0) >= 1,
                "player": x.get("player"), "platform": x.get("platform"),
                "decision": self._decision(x), "location": (x.get("location") or "").upper() or None,
            }))
        return {"items": items, "total": int(d.get("recordsFiltered") or d.get("recordsTotal") or len(items)),
                "duration": d.get("filter_duration") or d.get("total_duration")}

    def stats(self, days=30):
        rows = self.api("get_home_stats", time_range=days, stats_count=6,
                        stat_type="plays") or []
        by = {r.get("stat_id"): r.get("rows") or [] for r in rows if isinstance(r, dict)}

        def titles(key):
            return [clean({"title": x.get("title"), "plays": x.get("total_plays"),
                           "duration": x.get("total_duration"), "users": x.get("users_watched"),
                           "poster": self.img(x.get("grandparent_thumb") or x.get("thumb"))}) for x in by.get(key, [])]
        users = [clean({"name": x.get("friendly_name") or x.get("user"), "id": x.get("user_id"),
                        "plays": x.get("total_plays"), "duration": x.get("total_duration")})
                 for x in by.get("top_users", [])]
        platforms = [clean({"name": x.get("platform"), "plays": x.get("total_plays"),
                            "duration": x.get("total_duration")}) for x in by.get("top_platforms", [])]
        return {"days": days, "movies": titles("top_movies"), "shows": titles("top_tv"),
                "users": users, "platforms": platforms}

    def users(self):
        return [{"id": u.get("user_id"), "name": u.get("friendly_name") or u.get("username")}
                for u in (self.api("get_users") or []) if u.get("is_active", 1)]


def tautulli():
    t = CFG.get("tautulli") or {}
    if not t.get("url") or not t.get("api_key"):
        raise ApiError("Tautulli isn't set up — add it on the setup page", 404)
    return Tautulli(t)


@app.get("/api/activity")
def activity_now():
    return jsonify(tautulli().activity())


@app.get("/api/activity/history")
def activity_history():
    a = request.args
    return jsonify(tautulli().history(int(a.get("start") or 0), min(int(a.get("length") or 50), 200),
                                      a.get("user") or None, a.get("type") or None))


@app.get("/api/activity/stats")
def activity_stats():
    return jsonify(tautulli().stats(max(1, min(int(request.args.get("days") or 30), 3650))))


@app.get("/api/activity/users")
def activity_users():
    return jsonify(users=tautulli().users())


@app.get("/api/activity/img")
def activity_img():
    t = tautulli()
    a = request.args
    r = requests.get(f"{t.url}/api/v2", params={"apikey": t.key, "cmd": "pms_image_proxy", "img": a.get("img", ""),
                                                "width": a.get("w", 300), "height": a.get("h", 450),
                                                "fallback": "poster"}, timeout=20)
    if r.status_code >= 400 or not r.headers.get("Content-Type", "").startswith("image"):
        return Response(status=404)
    return Response(r.content, mimetype=r.headers["Content-Type"], headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------- Trakt (discussion)
#
# Comments on movies, shows and single episodes from Trakt — the TV Time-style conversation.
# Read-only, so only the app's Client ID is needed (no sign-in).

class Trakt:
    API = "https://api.trakt.tv"

    def __init__(self, conf):
        self.headers = {"Content-Type": "application/json", "trakt-api-version": "2",
                        "trakt-api-key": (conf.get("client_id") or "").strip(),
                        "User-Agent": f"Harbour/{VERSION}"}

    def req(self, path, **params):
        r = requests.get(self.API + path, params=params, headers=self.headers, timeout=15)
        if r.status_code in (401, 403):
            raise ApiError("Trakt rejected the Client ID — check it in Settings → Trakt", 502)
        if r.status_code == 404:
            raise ApiError("Trakt doesn't know this title", 404)
        r.raise_for_status()
        return r

    def find(self, ref):
        """'movie-603' / 'tv-1396' (TMDB ids) or 'show/tvdb/81189' → (type, trakt slug, title, year)."""
        hit = _TRAKT_IDS.get(ref)
        if hit:
            return hit
        m = re.fullmatch(r"(movie|tv)-(\d+)", ref)
        if m:
            typ, src, ident = ("movie" if m.group(1) == "movie" else "show"), "tmdb", m.group(2)
        else:
            m = re.fullmatch(r"(movie|show)/(tmdb|tvdb|imdb)/([\w]+)", ref)
            if not m:
                raise ApiError("Bad discussion link")
            typ, src, ident = m.groups()
        res = self.req(f"/search/{src}/{ident}", type=typ).json()
        obj = next((x.get(typ) for x in res if x.get(typ)), None)
        if not obj:
            raise ApiError("Trakt doesn't know this title", 404)
        hit = (typ, obj["ids"].get("slug") or obj["ids"]["trakt"], obj.get("title"), obj.get("year"))
        _TRAKT_IDS[ref] = hit
        return hit

    @staticmethod
    def _comment(c):
        text = c.get("comment") or ""
        spoiler = bool(c.get("spoiler")) or "[spoiler]" in text.lower()
        text = re.sub(r"\[/?spoiler\]", "", text, flags=re.I).strip()
        u = c.get("user") or {}
        return clean({"id": c.get("id"), "user": u.get("username") or u.get("name") or "someone",
                      "name": u.get("name") if u.get("name") and u.get("name") != u.get("username") else None,
                      "text": text, "spoiler": spoiler, "review": bool(c.get("review")),
                      "likes": c.get("likes") or 0, "replies": c.get("replies") or 0,
                      "rating": (c.get("user_stats") or {}).get("rating"),
                      "created": c.get("created_at")})

    def discussion(self, ref, season=None, episode=None, sort="likes", page=1):
        typ, slug, title, year = self.find(ref)
        base = f"/{typ}s/{slug}"
        web = f"https://trakt.tv/{typ}s/{slug}"
        label = f"{title} ({year})" if year and typ == "movie" else title
        ep_title = None
        if typ == "show" and season is not None and episode is not None:
            base += f"/seasons/{int(season)}/episodes/{int(episode)}"
            web += f"/seasons/{int(season)}/episodes/{int(episode)}"
            label = f"{title} · S{int(season):02d}E{int(episode):02d}"
        sort = sort if sort in ("likes", "newest", "replies", "highest", "lowest", "oldest") else "likes"
        got = par(comments=lambda: self.req(f"{base}/comments/{sort}", page=max(1, int(page)), limit=25),
                  ratings=lambda: safe(lambda: self.req(f"{base}/ratings").json(), "trakt ratings", {}),
                  ep=lambda: safe(lambda: self.req(base).json(), "trakt episode", {})
                  if base.count("/episodes/") else {})
        r = got["comments"]
        ep_title = (got["ep"] or {}).get("title")
        rating = got["ratings"] or {}
        return {"title": label, "episodeTitle": ep_title, "url": web, "sort": sort,
                "rating": round(rating["rating"] * 10) if rating.get("rating") else None,
                "votes": rating.get("votes") or 0,
                "page": int(r.headers.get("X-Pagination-Page") or page),
                "pages": int(r.headers.get("X-Pagination-Page-Count") or 1),
                "total": int(r.headers.get("X-Pagination-Item-Count") or 0),
                "comments": [self._comment(c) for c in r.json() or []]}

    def replies(self, cid):
        return {"comments": [self._comment(c) for c in self.req(f"/comments/{int(cid)}/replies", limit=100).json() or []]}


_TRAKT_IDS = {}


def trakt_on():
    return bool((CFG.get("trakt") or {}).get("client_id"))


def trakt():
    if not trakt_on():
        raise ApiError("Trakt isn't set up — add its Client ID in the phone app: Settings → Trakt", 404)
    return Trakt(CFG["trakt"])


def discuss_ref(kind, inst, item_id):
    """Which title on Trakt a detail page is about: 'movie-<tmdb>', 'tv-<tmdb>' or 'show/tvdb/<id>'."""
    s = svc(kind, inst)
    if isinstance(s, (Hub, Seerr)) or kind in ("hub", "seerr"):
        return item_id if re.fullmatch(r"(movie|tv)-\d+", item_id) else None
    if kind == "lib":
        parts = item_id.split("~", 2)
        if len(parts) == 3:
            return discuss_ref(parts[0], parts[1], parts[2])
        return item_id if re.fullmatch(r"(movie|tv)-\d+", item_id) else None
    if kind == "radarr":
        m = next((x for x in s.movies() if str(x.get("id")) == str(item_id)), None)
        return f"movie-{m['tmdbId']}" if m and m.get("tmdbId") else None
    if kind == "sonarr":
        x = next((x for x in s.series() if str(x.get("id")) == str(item_id)), None)
        if not x:
            return None
        return f"tv-{x['tmdbId']}" if x.get("tmdbId") else f"show/tvdb/{x['tvdbId']}" if x.get("tvdbId") else None
    return None


def add_discussion(d, ref):
    """A Discussion button on the page, and on every episode of a Sonarr page."""
    d.setdefault("actions", []).append(action("discuss", "Discussion", opens="discuss", arg=ref))
    if ref.startswith(("tv-", "show/")):
        for sn in d.get("seasons") or []:
            for ep in sn.get("episodes") or []:
                if ep.get("number") is not None and sn.get("number") is not None:
                    ep.setdefault("actions", []).append(
                        action("discuss", "Discussion", opens="discuss", arg=f"{ref}|s{sn['number']}e{ep['number']}"))


@app.get("/api/discuss")
def discuss_route():
    a = request.args
    ref = a.get("ref", "")
    season, episode = a.get("season"), a.get("episode")
    m = re.fullmatch(r"(.+)\|s(\d+)e(\d+)", ref)
    if m:
        ref, season, episode = m.groups()
    return jsonify(trakt().discussion(ref, season, episode, a.get("sort") or "likes", a.get("page") or 1))


@app.get("/api/discuss/replies/<int:cid>")
def discuss_replies(cid):
    return jsonify(trakt().replies(cid))


# ---------------------------------------------------------------- Komga (comics)
#
# Your CBZ/CBR library through Komga: series, issues, pages and reading progress. The phone's
# reader loads every image through the gateway, so it works at home and away the same way.

class Komga:
    def __init__(self, conf):
        self.url = conf["url"].rstrip("/")
        self.headers = {"X-API-Key": (conf.get("api_key") or "").strip(), "Accept": "application/json"}

    def req(self, method, path, timeout=20, **kw):
        r = requests.request(method, self.url + path, headers=self.headers, timeout=timeout, **kw)
        if r.status_code in (401, 403):
            raise ApiError("Komga rejected the API key — make a new one in Komga → Account → API keys", 502)
        if r.status_code == 404:
            raise ApiError("Komga doesn't have that", 404)
        r.raise_for_status()
        return r

    def get(self, path, **params):
        return self.req("GET", path, params=params).json()

    @staticmethod
    def book(b):
        md = b.get("metadata") or {}
        rp = b.get("readProgress") or {}
        media = b.get("media") or {}
        num = md.get("number") or ""
        return clean({
            "id": b["id"], "seriesId": b.get("seriesId"), "series": b.get("seriesTitle"),
            "title": md.get("title") or b.get("name"),
            "number": num, "label": f"#{num}" if num and str(num)[:1].isdigit() else (num or None),
            "pages": media.get("pagesCount") or 0,
            "page": rp.get("page") or 0, "done": bool(rp.get("completed")),
            "date": md.get("releaseDate"), "summary": md.get("summary") or None,
            "size": b.get("sizeBytes"),
            "cover": f"/api/comics/img/book/{b['id']}",
            "unreadable": media.get("status") not in (None, "READY"),
        })

    @staticmethod
    def series(x):
        md = x.get("metadata") or {}
        bmd = x.get("booksMetadata") or {}
        date = bmd.get("releaseDate") or ""
        return clean({
            "id": x["id"], "title": md.get("title") or x.get("name"), "libraryId": x.get("libraryId"),
            "books": x.get("booksCount") or 0, "read": x.get("booksReadCount") or 0,
            "unread": x.get("booksUnreadCount") or 0, "inProgress": x.get("booksInProgressCount") or 0,
            "publisher": md.get("publisher") or None, "year": date[:4] or None,
            "status": (md.get("status") or "").title() or None,
            "summary": md.get("summary") or bmd.get("summary") or None,
            "cover": f"/api/comics/img/series/{x['id']}",
        })

    def home(self):
        got = par(
            reading=lambda: safe(lambda: self.get("/api/v1/books", read_status="IN_PROGRESS", size=20,
                                                  sort="readProgress.readDate,desc"), "komga reading", {}),
            deck=lambda: safe(lambda: self.get("/api/v1/books/ondeck", size=20), "komga on deck", {}),
            new=lambda: safe(lambda: self.get("/api/v1/series/new", size=20), "komga new", {}),
            updated=lambda: safe(lambda: self.get("/api/v1/series/updated", size=20), "komga updated", {}),
            libs=lambda: safe(lambda: self.get("/api/v1/libraries"), "komga libraries", []),
        )
        reading = [self.book(b) for b in (got["reading"] or {}).get("content") or []]
        seen = {b["id"] for b in reading}
        deck = [self.book(b) for b in (got["deck"] or {}).get("content") or [] if b["id"] not in seen]
        new = [self.series(x) for x in (got["new"] or {}).get("content") or []]
        new_ids = {x["id"] for x in new}
        updated = [self.series(x) for x in (got["updated"] or {}).get("content") or [] if x["id"] not in new_ids]
        return {"reading": reading, "deck": deck, "new": new, "updated": updated,
                "libraries": [{"id": l["id"], "name": l.get("name")} for l in got["libs"] or []]}

    def series_list(self, search="", page=0, library=None, size=60):
        params = {"page": max(0, int(page)), "size": size, "sort": "metadata.titleSort,asc"}
        if search:
            params["search"] = search
        if library:
            params["library_id"] = library
        d = self.get("/api/v1/series", **params)
        return {"items": [self.series(x) for x in d.get("content") or []],
                "page": d.get("number", 0), "pages": d.get("totalPages", 1), "total": d.get("totalElements", 0)}

    def series_page(self, sid):
        got = par(s=lambda: self.get(f"/api/v1/series/{sid}"),
                  b=lambda: self.get(f"/api/v1/series/{sid}/books", size=2000, sort="metadata.numberSort,asc"))
        books = [self.book(b) for b in got["b"].get("content") or []]
        nxt = next((b for b in books if 0 < b.get("page", 0) and not b.get("done")), None) or \
            next((b for b in books if not b.get("done")), None)
        return {"series": self.series(got["s"]), "books": books, "next": nxt["id"] if nxt else None}

    def book_page(self, bid):
        def sibling(which):
            try:
                return self.get(f"/api/v1/books/{bid}/{which}").get("id")
            except ApiError:
                return None
        got = par(b=lambda: self.get(f"/api/v1/books/{bid}"),
                  pages=lambda: self.get(f"/api/v1/books/{bid}/pages"),
                  prev=lambda: safe(lambda: sibling("previous"), "komga previous", None),
                  next=lambda: safe(lambda: sibling("next"), "komga next", None))
        pages = [clean({"n": p.get("number"), "w": p.get("width"), "h": p.get("height")}) for p in got["pages"] or []]
        return {"book": self.book(got["b"]), "pages": pages, "prev": got["prev"], "next": got["next"]}

    def progress(self, bid, page, completed=False):
        self.req("PATCH", f"/api/v1/books/{bid}/read-progress",
                 json={"page": max(1, int(page)), "completed": bool(completed)})
        return {"ok": True}

    def image(self, path):
        r = self.req("GET", path, timeout=60, stream=True)
        headers = {"Cache-Control": "private, max-age=604800"}
        if r.headers.get("Content-Length"):
            headers["Content-Length"] = r.headers["Content-Length"]
        return Response(stream_with_context(r.iter_content(128 * 1024)),
                        mimetype=r.headers.get("Content-Type", "image/jpeg"), headers=headers)


def komga():
    k = CFG.get("komga") or {}
    if not k.get("url") or not k.get("api_key"):
        raise ApiError("Comics need Komga — add it in Settings → Komga", 404)
    return Komga(k)


KOMGA_ID = re.compile(r"^[A-Za-z0-9]{6,40}$")


def komga_id(x):
    if not KOMGA_ID.match(x or ""):
        raise ApiError("Bad id")
    return x


@app.get("/api/comics/home")
def comics_home():
    return jsonify(komga().home())


@app.get("/api/comics/series")
def comics_series():
    a = request.args
    lib = a.get("library") or None
    return jsonify(komga().series_list(a.get("search", "").strip(), a.get("page") or 0,
                                       komga_id(lib) if lib else None))


@app.get("/api/comics/series/<sid>")
def comics_series_page(sid):
    return jsonify(komga().series_page(komga_id(sid)))


@app.get("/api/comics/book/<bid>")
def comics_book(bid):
    return jsonify(komga().book_page(komga_id(bid)))


@app.post("/api/comics/book/<bid>/progress")
def comics_progress(bid):
    b = request.get_json(silent=True) or {}
    return jsonify(komga().progress(komga_id(bid), b.get("page") or 1, b.get("completed")))


@app.get("/api/comics/img/<kind>/<xid>")
def comics_thumb(kind, xid):
    if kind not in ("series", "book"):
        raise ApiError("Bad image")
    path = "series" if kind == "series" else "books"
    return komga().image(f"/api/v1/{path}/{komga_id(xid)}/thumbnail")


@app.get("/api/comics/page/<bid>/<int:n>")
def comics_page(bid, n):
    small = request.args.get("thumb") == "1"
    return komga().image(f"/api/v1/books/{komga_id(bid)}/pages/{max(1, n)}" + ("/thumbnail" if small else ""))


def files():
    if not FILES:
        raise ApiError("File Station isn't configured", 404)
    return FILES


@app.get("/api/files/list")
def files_list():
    return jsonify(files().listing(request.args.get("path", "")))


MEDIA_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm",
               ".avi": "video/x-msvideo", ".mov": "video/quicktime", ".ts": "video/mp2t", ".m2ts": "video/mp2t",
               ".wmv": "video/x-ms-wmv", ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".mp3": "audio/mpeg",
               ".m4a": "audio/mp4", ".flac": "audio/flac", ".aac": "audio/aac", ".ogg": "audio/ogg",
               ".opus": "audio/ogg", ".wav": "audio/wav"}


@app.get("/api/files/stream")
def files_stream():
    """Stream a video/audio file from the NAS (the TV player seeks with Range requests)."""
    path = request.args.get("path", "")
    rng = request.headers.get("Range")
    r = files().stream(path, rng)
    headers = {"Accept-Ranges": "bytes"}
    for h in ("Content-Length", "Content-Range"):
        if r.headers.get(h):
            headers[h] = r.headers[h]
    ext = posixpath.splitext(path)[1].lower()
    mime = MEDIA_TYPES.get(ext) or r.headers.get("Content-Type") or "application/octet-stream"
    m = re.fullmatch(r"bytes=(\d+)-(\d*)", (rng or "").strip())
    total = int(r.headers.get("Content-Length") or 0)
    if m and r.status_code == 200 and total:
        # File Station ignored the Range. Safari (iPhone) won't play video without proper partial
        # responses, so cut the requested slice out of the full stream ourselves.
        start = int(m.group(1))
        end = min(int(m.group(2)) if m.group(2) else total - 1, total - 1)
        if start >= total:
            r.close()
            return Response(status=416, headers={"Content-Range": f"bytes */{total}"})

        def sliced():
            pos = 0
            try:
                for chunk in r.iter_content(256 * 1024):
                    nxt = pos + len(chunk)
                    if nxt > start:
                        yield chunk[max(0, start - pos): end + 1 - pos]
                    pos = nxt
                    if pos > end:
                        break
            finally:
                r.close()
        headers.update({"Content-Range": f"bytes {start}-{end}/{total}", "Content-Length": str(end - start + 1)})
        return Response(stream_with_context(sliced()), status=206, mimetype=mime, headers=headers,
                        direct_passthrough=True)
    return Response(stream_with_context(r.iter_content(256 * 1024)), status=r.status_code,
                    mimetype=mime, headers=headers, direct_passthrough=True)


def file_play_info(path):
    files().check(path)
    name = posixpath.basename(path)
    token = request.headers.get("X-Token") or request.args.get("t") or ""
    url = request.host_url.rstrip("/") + "/api/files/stream?" + urlencode({"path": path, "t": token})
    return {"ref": "file:" + path, "ratingKey": "", "url": url, "hls": False, "offset": 0, "duration": 0,
            "title": posixpath.splitext(name)[0], "subtitle": posixpath.dirname(path), "badges": []}


@app.post("/api/files/op")
def files_op():
    return jsonify(files().op(request.get_json(silent=True) or {}))


@app.get("/api/dash")
def dash_route():
    if not HUB:
        raise ApiError("The dashboard needs Jellyseerr configured", 404)
    return jsonify(HUB.dash())


@app.get("/api/svc/<kind>/<inst>/season/<item_id>/<int:n>")
def svc_season(kind, inst, item_id, n):
    s = svc(kind, inst)
    if not hasattr(s, "season"):
        raise ApiError("Season pages aren't available here")
    page = s.season(item_id, n)
    ref = ((page.get("show") or {}).get("id") or "") if isinstance(page, dict) else ""
    if trakt_on() and re.fullmatch(r"tv-\d+", ref):
        for ep in page.get("episodes") or []:
            if ep.get("number") is not None:
                ep.setdefault("actions", []).append(
                    action("discuss", "Discussion", opens="discuss", arg=f"{ref}|s{n}e{ep['number']}"))
    return jsonify(page)


@app.get("/api/trailer/<key>")
def trailer(key):
    if not trailers_available():
        raise ApiError("In-app trailers aren't set up on the gateway", 404)
    t = yt_resolve(key)
    st = t["streams"]
    return jsonify(clean({"title": t.get("title"), "duration": t.get("duration"), "height": st[0].get("height"),
                          "video": f"/yt/{key}/0", "audio": f"/yt/{key}/1" if len(st) > 1 else None}))


@app.get("/yt/<key>/<int:i>")
def yt_stream(key, i):
    """Relay one trailer stream to the TV. Whatever range the player asks for, the gateway fetches
    it from YouTube in 8 MB pieces — YouTube stalls long single reads, which froze trailers."""
    rng = request.headers.get("Range", "")
    m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng.strip())
    start = int(m.group(1)) if m else 0
    want_end = int(m.group(2)) if m and m.group(2) else None

    def fetch(st, a, b):
        h = dict(st["headers"])
        h["Range"] = f"bytes={a}-{b}"
        return requests.get(st["url"], headers=h, stream=True, timeout=30)

    for attempt in (0, 1):
        t = yt_resolve(key, fresh=attempt == 1)
        if i >= len(t["streams"]):
            return Response(status=404)
        st = t["streams"][i]
        first_end = start + YT_CHUNK - 1 if want_end is None else min(want_end, start + YT_CHUNK - 1)
        r = fetch(st, start, first_end)
        if r.status_code == 403 and attempt == 0:  # links expire — look it up again once
            r.close()
            continue
        if r.status_code not in (200, 206):
            r.close()
            return Response(status=r.status_code)
        total = None
        cr = r.headers.get("Content-Range", "")
        if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
            total = int(cr.rsplit("/", 1)[1])
        elif r.status_code == 200 and r.headers.get("Content-Length", "").isdigit():
            total = int(r.headers["Content-Length"])
        end = (min(want_end, total - 1) if want_end is not None else total - 1) if total else first_end
        ctype = r.headers.get("Content-Type", "video/mp4")

        def body(r=r, st=st, end=end):
            pos = start
            resp = r
            while True:
                for chunk in resp.iter_content(256 * 1024):
                    pos += len(chunk)
                    yield chunk
                resp.close()
                if pos > end:
                    return
                resp = fetch(st, pos, min(end, pos + YT_CHUNK - 1))
                if resp.status_code not in (200, 206):
                    resp.close()
                    return

        headers = {"Content-Type": ctype, "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
        status = 200
        if m:
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{total if total else '*'}"
        return Response(stream_with_context(body()), status=status, headers=headers)
    return Response(status=502)


@app.post("/api/recording")
def save_recording():
    """Harbour's screen recordings: the TV streams the video here, the gateway saves it on the NAS."""
    if not FILES:
        raise ApiError("File Station isn't configured on the gateway", 404)
    rc = CFG.get("recordings") or {}
    folder = (rc.get("folder") if isinstance(rc, dict) else rc) or \
        (f"{FILES.allowed[0]}/Harbour recordings" if FILES.allowed else None)
    if not folder:
        raise ApiError('Set "recordings": {"folder": "/share/folder"} in the gateway config')
    name = re.sub(r"[^\w .()-]", "_", request.args.get("name") or
                  f"Harbour {datetime.now():%Y-%m-%d %H.%M.%S}.mp4")[:120]
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".mp4", dir=os.environ.get("TMPDIR", "/tmp"))
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = request.stream.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                out.write(chunk)
        if size == 0:
            raise ApiError("The recording was empty")
        path = FILES.upload(folder, name, tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    log.info("saved screen recording %s (%s)", path, fmt_size(size))
    return jsonify(path=path, size=size)


@app.get("/api/indexers/search")
def indexers_search():
    a = request.args
    return jsonify(indexer_search(a.get("q", ""), a.get("kind", "all"), a.get("protocol", "all")))


@app.get("/api/indexers/targets")
def indexers_targets():
    return jsonify(grab_targets())


@app.post("/api/indexers/grab")
def indexers_grab():
    b = request.get_json(silent=True) or {}
    return jsonify(indexer_grab(b.get("id"), b.get("target")))


@app.get("/api/calendar")
def calendar_route():
    a = request.args
    if not a.get("start") or not a.get("end"):
        raise ApiError("start and end are required")
    return jsonify(calendar(a["start"], a["end"], a.get("unmonitored") in ("1", "true")))


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


@app.post("/api/x/lookup")
def x_lookup_route():
    if not x_available():
        raise ApiError("X downloads need File Station set up on the gateway", 404)
    return jsonify(x_lookup((request.get_json(silent=True) or {}).get("url")))


def x_cookie_file():
    return os.path.join(os.path.dirname(CONFIG_PATH) or ".", "x-cookies.txt")


def _raw_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return json.loads(json.dumps(CFG))


def _cookie_value(v, name):
    """Accept the bare value, 'name=value', or a whole pasted Cookie header."""
    v = (v or "").strip().strip('"').strip("'")
    m = re.search(rf"(?:^|[;\s]){name}=([^;\s]+)", v)
    if m:
        v = m.group(1)
    if not re.fullmatch(r"[A-Za-z0-9%_.-]{16,400}", v):
        raise ApiError(f"That doesn't look like an X {name} value — copy just the Value column")
    return v


@app.get("/api/x/cookies")
def x_cookies_status():
    path = x_conf().get("cookies") or ""
    ok = bool(path) and os.path.isfile(path)
    return jsonify(set=ok, updated=int(os.path.getmtime(path)) if ok else None)


@app.post("/api/x/cookies")
def x_cookies_save():
    b = request.get_json(silent=True) or {}
    auth = _cookie_value(b.get("auth_token"), "auth_token")
    ct0 = _cookie_value(b.get("ct0"), "ct0")
    expires = int(time.time()) + 5 * 365 * 86400
    lines = ["# Netscape HTTP Cookie File", "# Written by Harbour from Settings → X sign-in"]
    for dom in (".x.com", ".twitter.com"):
        for k, v in (("auth_token", auth), ("ct0", ct0)):
            lines.append("\t".join([dom, "TRUE", "/", "TRUE", str(expires), k, v]))
    path = x_cookie_file()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError as e:
        raise ApiError(f"Couldn't save the X sign-in ({e.strerror}). The gateway's config folder must be writable.", 500)
    c = _raw_config()
    tw = c.get("twitter") if isinstance(c.get("twitter"), dict) else {}
    if tw.get("cookies") != path:
        tw["cookies"] = path
        c["twitter"] = tw
        save_config(c)
    _X_LOOKUPS.clear()
    return jsonify(ok=True, message="X sign-in saved")


@app.delete("/api/x/cookies")
def x_cookies_clear():
    path = x_conf().get("cookies") or x_cookie_file()
    try:
        os.remove(path)
    except OSError:
        pass
    c = _raw_config()
    if isinstance(c.get("twitter"), dict) and "cookies" in c["twitter"]:
        c["twitter"].pop("cookies")
        save_config(c)
    _X_LOOKUPS.clear()
    return jsonify(ok=True, message="X sign-in removed")


@app.post("/api/x/download")
def x_download_route():
    b = request.get_json(silent=True) or {}
    return jsonify(x_download(b.get("url"), b.get("index"), b.get("format")))


@app.get("/api/x/jobs")
def x_jobs_route():
    return jsonify(x_jobs())


@app.delete("/api/x/jobs")
def x_jobs_clear():
    return jsonify(x_forget())


@app.delete("/api/x/jobs/<job_id>")
def x_job_delete(job_id):
    return jsonify(x_forget(job_id))


init()
if not CFG.get("token"):
    log.warning("Not set up yet — open http://<your NAS>:8765/setup in a browser")

if __name__ == "__main__":
    threading.Thread(target=warm_loop, daemon=True).start()
    threading.Thread(target=yt_update_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8765")), threaded=True)
