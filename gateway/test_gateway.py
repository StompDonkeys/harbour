"""Run: pip install pytest responses flask requests && pytest -q test_gateway.py"""
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import responses

CFG = {
    "token": "tok",
    "radarr": [{"id": "radarr", "name": "Movies", "url": "http://nas:7878", "api_key": "rk"},
               {"id": "radarr-truehd", "name": "Movies 4K", "url": "http://nas:7879", "api_key": "rk4", "icon": "4k"}],
    "sonarr": [{"id": "sonarr", "name": "TV", "url": "http://nas:8989", "api_key": "sk"}],
    "seerr": {"url": "http://nas:5055", "api_key": "ok", "four_k": True},
    "filestation": {"url": "http://nas:5000", "username": "u", "password": "p",
                    "allowed_shares": ["/video", "/downloads"]},
}
_tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
json.dump(CFG, _tmp)
_tmp.close()
os.environ["HARBOUR_CONFIG"] = _tmp.name

import app as gw  # noqa: E402

H = {"X-Token": "tok"}
now = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")


def movie(i, **kw):
    m = {"id": i, "title": f"Movie {i}", "year": 2020 + i % 5, "runtime": 100 + i, "monitored": True,
         "hasFile": i % 2 == 0, "isAvailable": True, "genres": ["Action", "Drama"] if i % 3 else ["Comedy"],
         "added": iso(now - timedelta(days=i)),
         "images": [{"coverType": "poster", "remoteUrl": f"https://image.tmdb.org/t/p/original/p{i}.jpg"},
                    {"coverType": "fanart", "url": f"/MediaCover/{i}/fanart.jpg?lastWrite=1"}],
         "ratings": {"imdb": {"value": 5 + i % 4}}}
    if m["hasFile"]:
        m["movieFile"] = {"dateAdded": m["added"], "quality": {"quality": {"name": "Bluray-2160p"}},
                          "mediaInfo": {"audioCodec": "TrueHD Atmos", "audioChannels": 7.1,
                                        "videoCodec": "x265", "resolution": "3840x2160",
                                        "videoDynamicRangeType": "DV HDR10"}}
        m["sizeOnDisk"] = 60e9
    m.update(kw)
    return m


MOVIES = [movie(i) for i in range(1, 13)]
SERIES = [{"id": i, "title": f"Show {i}", "year": 2019, "status": "continuing" if i % 2 else "ended",
           "nextAiring": iso(now + timedelta(days=i)) if i % 2 else None, "network": "HBO",
           "monitored": True, "added": iso(now - timedelta(days=i)), "genres": ["Drama"],
           "images": [{"coverType": "poster", "remoteUrl": "https://artworks.thetvdb.com/p.jpg"}],
           "seasons": [{"seasonNumber": 0, "monitored": False}, {"seasonNumber": 1, "monitored": True}],
           "statistics": {"episodeFileCount": i, "episodeCount": 4, "seasonCount": 1, "sizeOnDisk": 1e9}}
          for i in range(1, 6)]


@pytest.fixture
def client():
    gw.init()
    return gw.app.test_client()


def test_auth(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/config").status_code == 401
    r = client.get("/api/config", headers=H).get_json()
    assert [s["inst"] for s in r["services"]] == ["radarr", "radarr-truehd", "sonarr", "seerr"]
    assert r["files"] is True


@responses.activate
def test_radarr_home_detail_actions(client):
    responses.get("http://nas:7878/api/v3/movie", json=MOVIES)
    responses.get("http://nas:7878/api/v3/queue", json={"records": [
        {"id": 9, "movieId": 3, "size": 100, "sizeleft": 25, "status": "downloading", "timeleft": "00:10:00",
         "quality": {"quality": {"name": "WEBDL-2160p"}}}]})
    responses.get("http://nas:7878/api/v3/calendar", json=[
        movie(50, hasFile=False, isAvailable=False, digitalRelease=iso(now + timedelta(days=10)))])
    r = client.get("/api/svc/radarr/radarr/home", headers=H)
    assert r.status_code == 200, r.data
    page = r.get_json()
    titles = [row["title"] for row in page["rows"]]
    assert titles[:2] == ["Downloading", "Recently added"] and "Coming soon" in titles and "Action" in titles
    dl = page["rows"][0]["items"][0]
    assert dl["progress"] == 0.75 and dl["badge"] == "75%"
    rec = page["rows"][1]["items"][0]
    assert "/t/p/w342/" in rec["poster"] and rec["backdrop"].startswith("/img/radarr/radarr/MediaCover/")
    soon = next(r for r in page["rows"] if r["title"] == "Coming soon")["items"][0]
    assert soon["subtitle"].startswith("Digital")

    responses.get("http://nas:7878/api/v3/movie/2", json=MOVIES[1])
    d = client.get("/api/svc/radarr/radarr/item/2", headers=H).get_json()
    labels = {f["label"]: f["value"] for f in d["facts"]}
    assert labels["Audio"].startswith("TrueHD Atmos") and labels["Size"] == "55.9 GB"
    assert [a["id"] for a in d["actions"]] == ["search", "unmonitor", "delete", "delete_files"]

    responses.post("http://nas:7878/api/v3/command", json={})
    r = client.post("/api/svc/radarr/radarr/item/2/action", json={"action": "search"}, headers=H).get_json()
    assert r["message"].startswith("Searching")
    assert json.loads(responses.calls[-1].request.body) == {"name": "MoviesSearch", "movieIds": [2]}

    responses.delete("http://nas:7878/api/v3/movie/2", json={})
    r = client.post("/api/svc/radarr/radarr/item/2/action", json={"action": "delete_files"}, headers=H).get_json()
    assert r["close"] is True and "deleteFiles=true" in responses.calls[-1].request.url

    s = client.get("/api/svc/radarr/radarr/search?q=movie%201", headers=H).get_json()
    assert {i["title"] for i in s["items"]} == {"Movie 1", "Movie 10", "Movie 11", "Movie 12"}


@responses.activate
def test_radarr_upstream_error_is_friendly(client):
    responses.get("http://nas:7879/api/v3/movie", status=401, json={"message": "Unauthorized"})
    r = client.get("/api/svc/radarr/radarr-truehd/home", headers=H)
    assert r.status_code == 502 and "Movies 4K" in r.get_json()["error"]


@responses.activate
def test_sonarr(client):
    responses.get("http://nas:8989/api/v3/series", json=SERIES)
    responses.get("http://nas:8989/api/v3/queue", json={"records": []})
    responses.get("http://nas:8989/api/v3/calendar", json=[
        {"id": 77, "seriesId": 1, "seasonNumber": 2, "episodeNumber": 3, "title": "Ep",
         "airDateUtc": iso(now + timedelta(days=2))}])
    responses.get("http://nas:8989/api/v3/wanted/missing", status=500)  # broken row must not break page
    page = client.get("/api/svc/sonarr/sonarr/home", headers=H).get_json()
    titles = [r["title"] for r in page["rows"]]
    assert titles == ["Airing this fortnight", "Recently added", "Returning soon", "Drama"]
    assert page["rows"][0]["items"][0]["subtitle"] == "S02E03 · Ep"

    responses.get("http://nas:8989/api/v3/series/1", json=SERIES[0])
    responses.get("http://nas:8989/api/v3/episode", json=[
        {"id": 5, "seasonNumber": 1, "episodeNumber": 2, "title": "B", "hasFile": False,
         "airDateUtc": iso(now - timedelta(days=3))},
        {"id": 4, "seasonNumber": 1, "episodeNumber": 1, "title": "A", "hasFile": True,
         "airDateUtc": iso(now - timedelta(days=10))},
        {"id": 6, "seasonNumber": 1, "episodeNumber": 3, "title": "C", "hasFile": False,
         "airDateUtc": iso(now + timedelta(days=3))},
        {"id": 9, "seasonNumber": 0, "episodeNumber": 1, "title": "Sp", "hasFile": False}])
    d = client.get("/api/svc/sonarr/sonarr/item/1", headers=H).get_json()
    assert [s["label"] for s in d["seasons"]] == ["Season 1", "Specials"]
    eps = d["seasons"][0]["episodes"]
    assert [e["label"] for e in eps] == ["E01", "E02", "E03"]
    assert eps[1]["subtitle"].endswith("Missing") and eps[2]["subtitle"].endswith("Not aired")
    assert d["seasons"][0]["action"] == {"id": "season_search", "label": "Search season", "arg": "1"}

    responses.post("http://nas:8989/api/v3/command", json={})
    client.post("/api/svc/sonarr/sonarr/item/1/action", json={"action": "season_search", "arg": "1"}, headers=H)
    assert json.loads(responses.calls[-1].request.body) == {"name": "SeasonSearch", "seriesId": 1, "seasonNumber": 1}

    responses.put("http://nas:8989/api/v3/series/1", json={})
    client.post("/api/svc/sonarr/sonarr/item/1/action", json={"action": "unmonitor"}, headers=H)
    assert json.loads(responses.calls[-1].request.body)["monitored"] is False


@responses.activate
def test_seerr(client):
    res = lambda mt: {"results": [
        {"id": 10, "mediaType": mt, "title": "T", "name": "N", "posterPath": "/a.jpg",
         "backdropPath": "/b.jpg", "voteAverage": 7.25, "releaseDate": "2025-01-02",
         "mediaInfo": {"status": 5}},
        {"id": 11, "mediaType": "person", "name": "Someone"}]}
    responses.get("http://nas:5055/api/v1/discover/trending", json=res("movie"))
    responses.get("http://nas:5055/api/v1/discover/movies", json={"results": [{"id": 12, "title": "X"}]})
    responses.get("http://nas:5055/api/v1/discover/tv", json=res("tv"))
    responses.get("http://nas:5055/api/v1/discover/movies/upcoming", status=500)
    responses.get("http://nas:5055/api/v1/discover/tv/upcoming", json={"results": []})
    page = client.get("/api/svc/seerr/seerr/home", headers=H).get_json()
    assert [r["title"] for r in page["rows"]] == ["Trending", "Popular movies", "Popular TV"]
    t = page["rows"][0]["items"]
    assert len(t) == 1 and t[0]["id"] == "movie-10" and t[0]["badge"] == "Available"
    assert t[0]["subtitle"] == "2025 · Movie · ★ 7.2" or t[0]["subtitle"] == "2025 · Movie · ★ 7.3"
    assert page["rows"][1]["items"][0]["id"] == "movie-12"  # mediaType filled from endpoint

    responses.get(re.compile(r"http://nas:5055/api/v1/search\?query=the%20bear&page=1"), json=res("tv"))
    s = client.get("/api/svc/seerr/seerr/search?q=the bear", headers=H).get_json()
    assert s["items"][0]["id"] == "tv-10"

    responses.get("http://nas:5055/api/v1/tv/10", json={
        "id": 10, "name": "The Bear", "numberOfSeasons": 2, "firstAirDate": "2022-06-23",
        "seasons": [{"seasonNumber": 0, "episodeCount": 2}, {"seasonNumber": 1, "episodeCount": 8, "name": "Season 1"},
                    {"seasonNumber": 2, "episodeCount": 10, "name": "Season 2"}],
        "mediaInfo": {"status": 4, "seasons": [{"seasonNumber": 1, "status": 5}]}})
    d = client.get("/api/svc/seerr/seerr/item/tv-10", headers=H).get_json()
    assert [a["id"] for a in d["actions"]] == ["request", "request4k"]
    assert [s.get("action", {}).get("id") for s in d["seasons"]] == [None, "request_season"]

    responses.post("http://nas:5055/api/v1/request", json={})
    client.post("/api/svc/seerr/seerr/item/tv-10/action", json={"action": "request"}, headers=H)
    assert json.loads(responses.calls[-1].request.body) == {"mediaType": "tv", "mediaId": 10, "is4k": False,
                                                            "seasons": [1, 2]}
    client.post("/api/svc/seerr/seerr/item/tv-10/action", json={"action": "request_season", "arg": "2"}, headers=H)
    assert json.loads(responses.calls[-1].request.body)["seasons"] == [2]


def fs_ok(data):
    return {"success": True, "data": data}


@responses.activate
def test_filestation(client):
    calls = []

    def entry(req):
        p = dict(x.split("=", 1) for x in req.body.split("&"))
        from urllib.parse import unquote_plus
        p = {k: unquote_plus(v) for k, v in p.items()}
        calls.append(p)
        api, method = p["api"], p["method"]
        if p["_sid"] == "old":
            return 200, {}, json.dumps({"success": False, "error": {"code": 119}})
        if method == "list_share":
            return 200, {}, json.dumps(fs_ok({"shares": [{"name": "video", "path": "/video"},
                                                         {"name": "homes", "path": "/homes"}]}))
        if method == "list":
            return 200, {}, json.dumps(fs_ok({"total": 2, "files": [
                {"name": "z.mkv", "path": "/video/z.mkv", "isdir": False,
                 "additional": {"size": 1234, "time": {"mtime": 1700000000}}},
                {"name": "Movies", "path": "/video/Movies", "isdir": True, "additional": {"time": {"mtime": 1}}}]}))
        if method == "start":
            return 200, {}, json.dumps(fs_ok({"taskid": "T1"}))
        if method == "status":
            return 200, {}, json.dumps(fs_ok({"finished": True}))
        if api == "SYNO.FileStation.Rename" and json.loads(p["name"]) == ["exists"]:
            return 200, {}, json.dumps({"success": False, "error": {"code": 1200, "errors": [{"code": 414}]}})
        return 200, {}, json.dumps(fs_ok({}))

    logins = []

    def auth(req):
        logins.append(1)
        return 200, {}, json.dumps(fs_ok({"sid": "new"}))

    responses.add_callback(responses.POST, "http://nas:5000/webapi/auth.cgi", callback=auth)
    responses.add_callback(responses.POST, "http://nas:5000/webapi/entry.cgi", callback=entry)
    gw.FILES.sid = "old"  # expired session → should re-login transparently

    root = client.get("/api/files/list?path=", headers=H).get_json()
    assert [i["path"] for i in root["items"]] == ["/video"] and len(logins) == 1

    l = client.get("/api/files/list?path=/video", headers=H).get_json()
    assert [i["name"] for i in l["items"]] == ["Movies", "z.mkv"] and l["parent"] == ""
    assert client.get("/api/files/list?path=/homes", headers=H).status_code == 403
    assert client.get("/api/files/list?path=/video/../homes", headers=H).status_code == 400

    r = client.post("/api/files/op", json={"op": "move", "paths": ["/video/z.mkv"], "dest": "/video/Movies"}, headers=H)
    assert r.get_json()["message"] == "Moved 1 item"
    start = [c for c in calls if c["method"] == "start"][-1]
    assert start["remove_src"] == "true" and json.loads(start["path"]) == ["/video/z.mkv"]

    bad = [
        {"op": "move", "paths": ["/video/Movies"], "dest": "/video/Movies/sub"},
        {"op": "move", "paths": ["/video/z.mkv"], "dest": "/video"},
        {"op": "delete", "paths": ["/video"]},
        {"op": "rename", "path": "/video/z.mkv", "name": "a/b"},
    ]
    for b in bad:
        assert client.post("/api/files/op", json=b, headers=H).status_code in (400, 403), b

    r = client.post("/api/files/op", json={"op": "rename", "path": "/video/z.mkv", "name": "exists"}, headers=H)
    assert r.status_code == 400 and r.get_json()["error"] == "That name already exists"

    assert client.post("/api/files/op", json={"op": "delete", "paths": ["/video/z.mkv"]},
                       headers=H).get_json()["message"] == "Deleted"
    assert client.post("/api/files/op", json={"op": "mkdir", "path": "/video", "name": "New"},
                       headers=H).status_code == 200
