"""Run: pip install pytest responses flask requests && pytest -q test_gateway.py"""
import json
import os
import re
import tempfile
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

import pytest
import responses

CFG = {
    "token": "tok",
    "radarr": [{"id": "radarr", "name": "Movies", "url": "http://nas:7878", "api_key": "rk"},
               {"id": "radarr-truehd", "name": "Movies 4K", "url": "http://nas:7879", "api_key": "rk4", "icon": "4k"}],
    "sonarr": [{"id": "sonarr", "name": "TV", "url": "http://nas:8989", "api_key": "sk"}],
    "seerr": {"url": "http://nas:5055", "api_key": "ok", "four_k": True},
    "plex": {"url": "http://nas:32400", "token": "admintok"},
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
    assert [s["inst"] for s in r["services"]] == ["hub", "radarr", "radarr-truehd", "sonarr", "seerr"]
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
    assert [a["id"] for a in d["actions"]] == ["search", "releases", "unmonitor", "delete", "delete_files"]
    assert d["actions"][1]["opens"] == "releases"

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
    responses.get("http://nas:8989/api/v3/wanted/missing", json={"records": []})
    d = client.get("/api/svc/sonarr/sonarr/item/1", headers=H).get_json()
    assert [s["label"] for s in d["seasons"]] == ["Season 1", "Specials"]
    eps = d["seasons"][0]["episodes"]
    assert [e["label"] for e in eps] == ["E01", "E02", "E03"]
    assert "Missing" in eps[1]["subtitle"] and "Not aired" in eps[2]["subtitle"]
    assert [a["id"] for a in d["seasons"][0]["actions"]] == ["season_search", "releases", "season_unmonitor"]
    assert [a["id"] for a in eps[0]["actions"]] == ["episode_search", "releases", "episode_monitor"]

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
    assert [r["key"] for r in page["rows"]] == ["trending", "movies", "tv"]
    t = page["rows"][0]["items"]
    assert len(t) == 1 and t[0]["id"] == "movie-10" and t[0]["badge"] == "Available"
    assert t[0]["subtitle"] == "2025 · Movie · ★ 7.2" or t[0]["subtitle"] == "2025 · Movie · ★ 7.3"
    assert page["rows"][1]["items"][0]["id"] == "movie-12"  # mediaType filled from endpoint

    responses.get(re.compile(r"http://nas:5055/api/v1/search\?query=the%20bear&page=1"), json=res("tv"))
    s = client.get("/api/svc/seerr/seerr/search?q=the bear", headers=H).get_json()
    assert s["items"][0]["id"] == "tv-10"

    responses.get("http://nas:5055/api/v1/service/sonarr", json=[{"id": 0, "name": "Sonarr", "is4k": False,
        "isDefault": True, "activeDirectory": "/data/media/tv", "activeProfileId": 4}])
    responses.get("http://nas:5055/api/v1/service/sonarr/0", json={"rootFolders": [
        {"id": 1, "path": "/data/media/kids", "freeSpace": 2e12}, {"id": 2, "path": "/data/media/tv", "freeSpace": 2e12}]})
    responses.get("http://nas:5055/api/v1/tv/10", json={
        "id": 10, "name": "The Bear", "numberOfSeasons": 2, "firstAirDate": "2022-06-23",
        "seasons": [{"seasonNumber": 0, "episodeCount": 2}, {"seasonNumber": 1, "episodeCount": 8, "name": "Season 1"},
                    {"seasonNumber": 2, "episodeCount": 10, "name": "Season 2"}],
        "mediaInfo": {"status": 4, "seasons": [{"seasonNumber": 1, "status": 5}]}})
    d = client.get("/api/svc/seerr/seerr/item/tv-10", headers=H).get_json()
    assert [a["id"] for a in d["actions"]] == ["request"]  # no 4K Sonarr → no 4K button
    opts = d["actions"][0]["choose"]["options"]
    assert opts[0]["value"] == "0|4|/data/media/tv" and opts[0]["label"].endswith("(default)")
    assert [[a["id"] for a in s.get("actions", [])] for s in d["seasons"]] == [[], ["request_season"]]

    responses.post("http://nas:5055/api/v1/request", json={})
    client.post("/api/svc/seerr/seerr/item/tv-10/action",
                json={"action": "request", "choice": "0|4|/data/media/kids"}, headers=H)
    assert json.loads(responses.calls[-1].request.body) == {"mediaType": "tv", "mediaId": 10, "is4k": False,
                                                            "seasons": [1, 2], "serverId": 0, "profileId": 4,
                                                            "rootFolder": "/data/media/kids"}
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


# ---------------------------------------------------------------- v0.2 features

@responses.activate
def test_manual_search_and_grab(client):
    rel = [{"guid": "g1", "indexerId": 3, "title": "Movie.2160p", "size": 50e9, "indexer": "NZBgeek",
            "protocol": "usenet", "approved": False, "rejections": ["Not an upgrade"], "age": 3,
            "quality": {"quality": {"name": "Bluray-2160p"}}, "customFormatScore": 100},
           {"guid": "g2", "indexerId": 4, "title": "Movie.1080p", "size": 9e9, "indexer": "TL",
            "protocol": "torrent", "seeders": 42, "approved": True, "rejections": [], "ageHours": 5.5,
            "customFormatScore": 10}]
    responses.get("http://nas:7878/api/v3/release", json=rel)
    r = client.get("/api/svc/radarr/radarr/item/2/releases?arg=movie", headers=H).get_json()
    assert [i["guid"] for i in r["items"]] == ["g2", "g1"]  # approved first
    assert r["items"][0]["peers"] == "42 seeders" and r["items"][0]["age"] == "5h"
    assert r["items"][1]["rejections"] == ["Not an upgrade"] and r["items"][1]["age"] == "3d"
    assert "movieId=2" in responses.calls[-1].request.url

    responses.post("http://nas:7878/api/v3/release", json={})
    r = client.post("/api/svc/radarr/radarr/item/2/grab", json={"guid": "g2", "indexerId": 4}, headers=H)
    assert r.status_code == 200 and json.loads(responses.calls[-1].request.body) == {"guid": "g2", "indexerId": 4}

    responses.get("http://nas:8989/api/v3/release", json=[])
    client.get("/api/svc/sonarr/sonarr/item/1/releases?arg=episode:55", headers=H)
    assert "episodeId=55" in responses.calls[-1].request.url
    client.get("/api/svc/sonarr/sonarr/item/1/releases?arg=season:2", headers=H)
    assert "seasonNumber=2" in responses.calls[-1].request.url and "seriesId=1" in responses.calls[-1].request.url


@responses.activate
def test_episode_and_season_actions(client):
    responses.put("http://nas:8989/api/v3/episode/monitor", json={})
    client.post("/api/svc/sonarr/sonarr/item/1/action", json={"action": "episode_unmonitor", "arg": "77"}, headers=H)
    assert json.loads(responses.calls[-1].request.body) == {"episodeIds": [77], "monitored": False}

    responses.delete("http://nas:8989/api/v3/episodefile/900", json={})
    r = client.post("/api/svc/sonarr/sonarr/item/1/action", json={"action": "episode_delete_file", "arg": "900"},
                    headers=H)
    assert r.status_code == 200

    responses.get("http://nas:8989/api/v3/series/1", json=SERIES[0])
    responses.put("http://nas:8989/api/v3/series/1", json={})
    client.post("/api/svc/sonarr/sonarr/item/1/action", json={"action": "season_monitor", "arg": "0"}, headers=H)
    seasons = json.loads(responses.calls[-1].request.body)["seasons"]
    assert seasons[0] == {"seasonNumber": 0, "monitored": True}


@responses.activate
def test_seerr_movie_4k_and_folders(client):
    responses.get("http://nas:5055/api/v1/service/radarr", json=[
        {"id": 0, "name": "Radarr", "is4k": False, "isDefault": True, "activeDirectory": "/data/media/movies",
         "activeProfileId": 1},
        {"id": 1, "name": "Radarr 4K", "is4k": True, "isDefault": True, "activeDirectory": "/data/media/4k",
         "activeProfileId": 7}])
    responses.get("http://nas:5055/api/v1/service/radarr/0", json={"rootFolders": [{"path": "/data/media/movies"}]})
    responses.get("http://nas:5055/api/v1/service/radarr/1", json={"rootFolders": [{"path": "/data/media/4k"}]})
    responses.get("http://nas:5055/api/v1/movie/5", json={"id": 5, "title": "M", "mediaInfo": {"status": 5}})
    d = client.get("/api/svc/seerr/seerr/item/movie-5", headers=H).get_json()
    assert [a["id"] for a in d["actions"]] == ["request4k"]  # HD already available
    assert d["actions"][0]["choose"]["options"][0]["value"] == "1|7|/data/media/4k"


@responses.activate
def test_home_cache_single_flight(client):
    calls = []

    def movies(req):
        calls.append(1)
        import time as _t
        _t.sleep(0.3)
        return 200, {}, json.dumps(MOVIES)

    responses.add_callback(responses.GET, "http://nas:7878/api/v3/movie", callback=movies)
    responses.get("http://nas:7878/api/v3/queue", json={"records": []})
    responses.get("http://nas:7878/api/v3/calendar", json=[])
    import threading as th
    res = []
    ts = [th.Thread(target=lambda: res.append(client.get("/api/svc/radarr/radarr/home", headers=H).status_code))
          for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res == [200, 200, 200] and len(calls) == 1
    client.get("/api/svc/radarr/radarr/home", headers=H)
    assert len(calls) == 1  # served from cache


@responses.activate
def test_downloads(client):
    gw.CFG["nzbget"] = {"url": "http://nas:6789", "username": "u", "password": "p"}
    gw.CFG["qbittorrent"] = {"url": "http://nas:8080", "username": "u", "password": "p"}
    # re-init with the extra clients, but keep the in-memory config
    orig = gw.load_config
    gw.load_config = lambda: gw.CFG
    try:
        gw.init()
    finally:
        gw.load_config = orig
    rpc = []

    def nzb(req):
        b = json.loads(req.body)
        rpc.append(b)
        res = {"status": {"DownloadRate": 10_000_000, "DownloadPaused": False},
               "listgroups": [{"NZBID": 7, "NZBName": "Show.S01E01", "Status": "DOWNLOADING",
                               "FileSizeLo": 1000_000_000, "FileSizeHi": 0, "RemainingSizeLo": 400_000_000,
                               "RemainingSizeHi": 0, "Category": "tv"},
                              {"NZBID": 8, "NZBName": "Other", "Status": "QUEUED", "FileSizeLo": 10,
                               "FileSizeHi": 0, "RemainingSizeLo": 10, "RemainingSizeHi": 0}],
               "editqueue": True}.get(b["method"], True)
        return 200, {}, json.dumps({"result": res})

    responses.add_callback(responses.POST, "http://nas:6789/jsonrpc", callback=nzb)
    responses.post("http://nas:8080/api/v2/auth/login", body="Ok.")
    responses.get("http://nas:8080/api/v2/transfer/info", json={"dl_info_speed": 2_000_000})
    responses.get("http://nas:8080/api/v2/torrents/info", json=[
        {"hash": "abc", "name": "Linux.iso", "progress": 0.5, "size": 100, "amount_left": 50, "dlspeed": 2_000_000,
         "eta": 25, "state": "downloading", "priority": 1},
        {"hash": "def", "name": "Done", "progress": 1, "size": 5, "state": "uploading"},
        {"hash": "ghi", "name": "Stopped", "progress": 0.1, "size": 5, "state": "stoppedDL", "eta": 8640000,
         "priority": 2}])
    d = client.get("/api/downloads", headers=H).get_json()
    assert [c["id"] for c in d["clients"]] == ["nzbget", "qbittorrent"]
    assert d["clients"][1]["seeding"] == 1 and d["clients"][0]["speed"] == 10_000_000
    names = [i["name"] for i in d["items"]]
    assert names[:2] == ["Show.S01E01", "Linux.iso"] and names[-1] == "Stopped"
    nz = d["items"][0]
    assert nz["progress"] == 0.6 and nz["eta"] == 40 and nz["status"] == "Downloading"
    assert d["items"][-1]["paused"] is True and d["items"][-1]["eta"] == -1

    client.post("/api/downloads/action", json={"client": "nzbget", "id": "7", "action": "top"}, headers=H)
    assert rpc[-1] == {"method": "editqueue", "params": ["GroupMoveTop", "", [7]], "id": 1}
    client.post("/api/downloads/action", json={"client": "nzbget", "action": "pause"}, headers=H)
    assert rpc[-1]["method"] == "pausedownload"

    # qBittorrent 4.x: /stop is 404 → falls back to /pause
    responses.post("http://nas:8080/api/v2/torrents/stop", status=404)
    responses.post("http://nas:8080/api/v2/torrents/pause", body="")
    r = client.post("/api/downloads/action", json={"client": "qbittorrent", "id": "abc", "action": "pause"}, headers=H)
    assert r.status_code == 200 and responses.calls[-1].request.url.endswith("/torrents/pause")
    responses.post("http://nas:8080/api/v2/torrents/delete", body="")
    client.post("/api/downloads/action", json={"client": "qbittorrent", "id": "abc", "action": "delete_files"},
                headers=H)
    assert "deleteFiles=true" in responses.calls[-1].request.body

    # one client down → page still loads with an error for that client
    responses.replace(responses.GET, "http://nas:8080/api/v2/transfer/info", status=500)
    d = client.get("/api/downloads", headers=H).get_json()
    assert d["clients"][1]["error"] and len(d["items"]) == 2
    del gw.CFG["nzbget"], gw.CFG["qbittorrent"]


@responses.activate
def test_apk_and_log(client):
    responses.get("https://github.com/StompDonkeys/harbour/releases/latest/download/Harbour.apk",
                  body=b"PK-fake-apk", headers={"Content-Length": "11"})
    for path in ("/apk", "/Harbour.apk"):
        r = client.get(path)
        assert r.status_code == 200 and r.data == b"PK-fake-apk"
        assert r.mimetype == "application/vnd.android.package-archive"
        assert "Harbour.apk" in r.headers["Content-Disposition"]
    assert client.post("/api/log", json={"text": "boom"}).status_code == 401
    assert client.post("/api/log", json={"text": "boom"}, headers=H).get_json() == {"ok": True}


@responses.activate
def test_qbittorrent_api_key():
    q = gw.QBittorrent({"url": "http://nas:8080", "api_key": "qbt_abc"})
    responses.get("http://nas:8080/api/v2/transfer/info", json={"dl_info_speed": 5})
    responses.get("http://nas:8080/api/v2/torrents/info", json=[])
    client, items = q.snapshot()
    assert client["speed"] == 5 and items == []
    assert all(c.request.headers["Authorization"] == "Bearer qbt_abc" for c in responses.calls)
    assert not any("auth/login" in c.request.url for c in responses.calls)
    responses.replace(responses.GET, "http://nas:8080/api/v2/transfer/info", status=401)
    with pytest.raises(gw.ApiError) as e:
        q.snapshot()
    assert "API key rejected" in e.value.message


@responses.activate
def test_seerr_library_facts_and_clear(client):
    m = dict(MOVIES[1], tmdbId=777)   # Movie 2 has a 4K file
    responses.get("http://nas:7878/api/v3/movie", json=[m])
    responses.get("http://nas:7879/api/v3/movie", json=[])
    responses.get("http://nas:5055/api/v1/service/radarr", json=[])
    responses.get("http://nas:5055/api/v1/movie/777", json={"id": 777, "title": "Dune",
                  "mediaInfo": {"id": 55, "status": 5}})
    d = client.get("/api/svc/seerr/seerr/item/movie-777", headers=H).get_json()
    labels = {f["label"]: f["value"] for f in d["facts"]}
    assert labels["Movies"].startswith("Downloaded · Bluray-2160p") and "TrueHD" in labels["Movies audio"]
    ids = [a["id"] for a in d["actions"]]
    assert ids == ["open", "clear"]
    assert d["actions"][0] == {"id": "open", "label": "Open in Movies", "arg": "radarr/radarr/2", "opens": "item"}

    responses.delete("http://nas:5055/api/v1/media/55", status=204)
    r = client.post("/api/svc/seerr/seerr/item/movie-777/action", json={"action": "clear"}, headers=H).get_json()
    assert r["message"] == "Cleared from Discover"
    assert responses.calls[-1].request.method == "DELETE"


@responses.activate
def test_seerr_row_paging(client):
    responses.get("http://nas:5055/api/v1/discover/movies",
                  json={"page": 2, "totalPages": 3, "results": [{"id": 30, "title": "P2", "mediaType": "movie"}]})
    r = client.get("/api/svc/seerr/seerr/row?key=movies&page=2", headers=H).get_json()
    assert r == {"items": [r["items"][0]], "page": 2, "hasMore": True} and r["items"][0]["id"] == "movie-30"
    assert "page=2" in responses.calls[-1].request.url
    assert client.get("/api/svc/seerr/seerr/row?key=nope", headers=H).status_code == 400
    assert client.get("/api/svc/radarr/radarr/row?key=movies", headers=H).status_code == 400


# ---------------------------------------------------------------- hub + plex

USERS_XML = b"""<MediaContainer><User id="1" title="Matt" admin="1" protected="0"/>
<User id="22" title="Kids" admin="0" protected="1" thumb="https://plex.tv/k.png"/></MediaContainer>"""


def plex_common():
    responses.get("http://nas:32400/identity", json={"MediaContainer": {"machineIdentifier": "MID"}})
    responses.get("https://plex.tv/api/home/users", body=USERS_XML)


@responses.activate
def test_plex_users_and_switch(client):
    plex_common()
    r = client.get("/api/plex/users", headers=H).get_json()
    assert [u["title"] for u in r["users"]] == ["Matt", "Kids"] and r["users"][1]["protected"] is True
    responses.post("https://plex.tv/api/home/users/22/switch", body=b'<user authenticationToken="kidtok"/>')
    responses.get("https://plex.tv/api/v2/resources", json=[{"clientIdentifier": "MID", "accessToken": "kidserver"}])
    assert gw.PLEX.token_for("Kids", "1234") == "kidserver"
    assert any("switch?pin=1234" in c.request.url for c in responses.calls)
    assert gw.PLEX.token_for("1") == "admintok"  # admin uses the configured token


@responses.activate
def test_hub_home(client):
    plex_common()
    responses.get("http://nas:32400/library/onDeck", json={"MediaContainer": {"Metadata": [
        {"type": "episode", "ratingKey": "9", "grandparentRatingKey": "5", "grandparentTitle": "Severance",
         "title": "Hello", "parentIndex": 2, "index": 3, "viewOffset": 600000, "duration": 3000000,
         "art": "/library/metadata/5/art/1"},
        {"type": "movie", "ratingKey": "7", "title": "Dune", "year": 2021, "Guid": [{"id": "tmdb://438631"}]}]}})
    responses.get("http://nas:32400/library/metadata/5", json={"MediaContainer": {"Metadata": [
        {"Guid": [{"id": "imdb://tt1"}, {"id": "tmdb://95396"}]}]}})
    m = dict(MOVIES[1], tmdbId=438631)
    responses.get("http://nas:7878/api/v3/movie", json=[m])
    responses.get("http://nas:7879/api/v3/movie", json=[])
    responses.get("http://nas:7878/api/v3/calendar", json=[])
    responses.get("http://nas:7879/api/v3/calendar", json=[])
    responses.get("http://nas:8989/api/v3/series", json=[dict(SERIES[0], tmdbId=95396)])
    responses.get("http://nas:8989/api/v3/history", json={"records": [
        {"date": iso(now), "series": dict(SERIES[0], tmdbId=95396), "episode": {"seasonNumber": 2, "episodeNumber": 3}}]})
    responses.get("http://nas:8989/api/v3/calendar", json=[])
    responses.get("http://nas:5055/api/v1/discover/trending", json={"totalPages": 5, "results": [
        {"id": 438631, "mediaType": "movie", "title": "Dune", "posterPath": "/d.jpg"}]})
    responses.get("http://nas:5055/api/v1/tv/95396/recommendations", json={"results": [
        {"id": 1, "mediaType": "tv", "name": "Rec"}]})
    page = client.get("/api/svc/hub/hub/home", headers=H).get_json()
    titles = [r["title"] for r in page["rows"]]
    assert titles == ["Continue watching", "Recently added movies", "Recently added TV", "Trending",
                      "Because you watched Severance"]
    cw = page["rows"][0]["items"]
    assert cw[0]["id"] == "tv-95396" and cw[0]["progress"] == 0.2 and "40m left" in cw[0]["subtitle"]
    assert cw[0]["backdrop"].startswith("/plexart?") and cw[1]["id"] == "movie-438631"
    assert page["rows"][1]["items"][0]["id"] == "movie-438631"
    assert page["rows"][3]["items"][0]["badge"] == "In library" and page["rows"][3]["key"] == "trending"


@responses.activate
def test_hub_title_and_person(client):
    plex_common()
    responses.get("http://nas:5055/api/v1/movie/438631", json={
        "id": 438631, "title": "Dune", "tagline": "Beyond fear", "overview": "Spice.",
        "mediaInfo": {"id": 3, "status": 5},
        "credits": {"cast": [{"id": 10, "name": "Timothée", "character": "Paul", "profilePath": "/t.jpg", "order": 0}],
                    "crew": [{"name": "Denis", "job": "Director"}]},
        "relatedVideos": [{"site": "YouTube", "type": "Trailer", "key": "abc"}]})
    responses.get("http://nas:5055/api/v1/movie/438631/ratingscombined",
                  json={"imdb": {"criticsScore": 8.0}, "rt": {"criticsScore": 83}})
    responses.get("http://nas:5055/api/v1/movie/438631/recommendations", json={"results": [
        {"id": 2, "mediaType": "movie", "title": "Arrival"}]})
    responses.get("http://nas:5055/api/v1/movie/438631/similar", json={"results": []})
    responses.get("http://nas:5055/api/v1/service/radarr", json=[])
    responses.get("http://nas:7878/api/v3/movie", json=[])
    responses.get("http://nas:7879/api/v3/movie", json=[])
    responses.get("http://nas:8989/api/v3/series", json=[])
    responses.get("http://nas:32400/hubs/search", json={"MediaContainer": {"Hub": [{"Metadata": [
        {"type": "movie", "ratingKey": "7", "viewCount": 1, "Guid": [{"id": "tmdb://438631"}]}]}]}})
    d = client.get("/api/svc/hub/hub/item/movie-438631", headers=H).get_json()
    f = {x["label"]: x["value"] for x in d["facts"]}
    assert f["Plex"] == "Watched" and f["Director"] == "Denis" and "Rotten Tomatoes 83%" in f["Ratings"]
    ids = [a["id"] for a in d["actions"]]
    assert ids[0] == "plex" and d["actions"][0]["arg"] == "MID|7" and "trailer" in ids and "clear" in ids
    assert d["people"][0] == {"id": "person-10", "name": "Timothée", "role": "Paul",
                              "photo": "https://image.tmdb.org/t/p/w185/t.jpg"}
    assert d["rows"][0]["title"] == "More like this" and d["item"]["overview"].startswith("“Beyond fear”")
    # Jellyseerr's own page now gets the same rich detail
    assert client.get("/api/svc/seerr/seerr/item/movie-438631", headers=H).get_json()["people"]

    responses.get("http://nas:5055/api/v1/person/10", json={"name": "Timothée", "birthday": "1995-12-27",
                                                            "biography": "Actor.", "knownForDepartment": "Acting"})
    responses.get("http://nas:5055/api/v1/person/10/combined_credits", json={"cast": [
        {"id": 438631, "mediaType": "movie", "title": "Dune", "character": "Paul", "popularity": 90},
        {"id": 5, "mediaType": "tv", "name": "Homeland", "character": "Finn", "episodeCount": 8, "popularity": 5}],
        "crew": []})
    p = client.get("/api/svc/hub/hub/item/person-10", headers=H).get_json()
    assert p["item"]["title"] == "Timothée" and [r["title"] for r in p["rows"]] == ["Movies", "TV"]
    assert p["rows"][0]["items"][0]["subtitle"].endswith("as Paul")


# ---------------------------------------------------------------- v2 playback

def _meta(rk, **kw):
    return {"MediaContainer": {"Metadata": [dict({"ratingKey": rk}, **kw)]}}


@responses.activate
def test_play_episode_resume_and_next(client):
    plex_common()
    responses.get("http://nas:32400/library/metadata/9", json=_meta(
        "9", type="episode", grandparentRatingKey="5", grandparentTitle="Severance", title="Hello",
        parentIndex=2, index=3, viewOffset=600000, duration=3000000,
        Media=[{"videoResolution": "4k", "videoDynamicRange": "Dolby Vision", "audioCodec": "truehd",
                "Part": [{"key": "/library/parts/77/123/file.mkv"}]}]))
    responses.get("http://nas:32400/library/metadata/5/allLeaves", json={"MediaContainer": {"Metadata": [
        {"ratingKey": "9"}, {"ratingKey": "10", "title": "Next one", "parentIndex": 2, "index": 4}]}})
    p = client.get("/api/play?ref=plex:9", headers=H).get_json()
    assert p["url"] == "http://nas:32400/library/parts/77/123/file.mkv?X-Plex-Token=admintok"
    assert p["offset"] == 600000 and p["duration"] == 3000000 and p["subtitle"] == "S2 E3 · Hello"
    assert p["badges"] == ["DIRECT PLAY", "4K", "DOLBY VISION", "TRUEHD"]
    assert p["next"]["ref"] == "plex:10"
    assert client.get("/api/play?ref=plex:9|start", headers=H).get_json()["offset"] == 0
    t = client.get("/api/play?ref=plex:9&transcode=1", headers=H).get_json()
    assert t["hls"] is True and "/video/:/transcode/universal/start.m3u8?" in t["url"] and "offset=600" in t["url"]


@responses.activate
def test_play_show_and_episode_refs(client):
    plex_common()
    responses.get("http://nas:5055/api/v1/tv/95396", json={"id": 95396, "name": "Severance"})
    responses.get("http://nas:32400/hubs/search", json={"MediaContainer": {"Hub": [{"Metadata": [
        {"type": "show", "ratingKey": "5", "Guid": [{"id": "tmdb://95396"}]}]}]}})
    # Plex returns OnDeck.Metadata as a single object for shows
    responses.get("http://nas:32400/library/metadata/5", json=_meta("5", type="show",
                  OnDeck={"Metadata": {"ratingKey": "9"}}))
    responses.get("http://nas:32400/library/metadata/5/allLeaves", json={"MediaContainer": {"Metadata": [
        {"ratingKey": "8", "parentIndex": 1, "index": 1}, {"ratingKey": "9", "parentIndex": 2, "index": 3}]}})
    responses.get("http://nas:32400/library/metadata/9", json=_meta(
        "9", type="episode", grandparentRatingKey="5", parentIndex=2, index=3, Media=[{"Part": [{"key": "/p/9"}]}]))
    responses.get("http://nas:32400/library/metadata/8", json=_meta(
        "8", type="episode", grandparentRatingKey="5", parentIndex=1, index=1, Media=[{"Part": [{"key": "/p/8"}]}]))
    assert client.get("/api/play?ref=tv-95396", headers=H).get_json()["ratingKey"] == "9"   # on deck
    assert client.get("/api/play?ref=tv-95396:s1e1", headers=H).get_json()["ratingKey"] == "8"
    r = client.get("/api/play?ref=tv-95396:s9e9", headers=H)
    assert r.status_code == 404 and "episode" in r.get_json()["error"]


@responses.activate
def test_progress_and_v2_actions(client):
    plex_common()
    responses.get("http://nas:32400/:/timeline", body="")
    client.post("/api/plex/progress", json={"ratingKey": "9", "state": "paused", "time": 1000, "duration": 5000},
                headers=H)
    u = responses.calls[-1].request.url
    assert "state=paused" in u and "key=%2Flibrary%2Fmetadata%2F9" in u
    m = dict(MOVIES[1], tmdbId=777)
    responses.get("http://nas:7878/api/v3/movie/2", json=m)
    v1 = client.get("/api/svc/radarr/radarr/item/2", headers=H).get_json()
    v2 = client.get("/api/svc/radarr/radarr/item/2", headers=dict(H, **{"X-Harbour-App": "2"})).get_json()
    assert "play" not in [a["id"] for a in v1["actions"]]
    assert v2["actions"][0]["id"] == "play" and v2["actions"][0]["arg"] == "movie-777"
    assert v2["item"]["ref"] == "movie-777"


@responses.activate
def test_apk_v2(client):
    responses.get("https://api.github.com/repos/StompDonkeys/harbour/releases", json=[
        {"tag_name": "v0.1.9", "assets": [{"name": "Harbour.apk", "browser_download_url": "https://x/v1"}]},
        {"tag_name": "v2.0.3", "assets": [{"name": "HarbourV2.apk", "browser_download_url": "https://x/v2"}]}])
    responses.get("https://x/v2", body=b"V2APK")
    r = client.get("/HarbourV2.apk")
    assert r.status_code == 200 and r.data == b"V2APK"


@responses.activate
def test_calendar(client):
    local = datetime.now().astimezone()
    today = local.date()
    past = local - timedelta(days=2)
    fut = local + timedelta(days=3)
    show = dict(SERIES[0], tmdbId=95396, runtime=45)
    responses.get("http://nas:8989/api/v3/calendar", json=[
        {"id": 1, "seriesId": 1, "seasonNumber": 2, "episodeNumber": 1, "title": "Pilot2", "hasFile": True,
         "monitored": True, "airDateUtc": iso(past.astimezone(timezone.utc)), "series": show},
        {"id": 2, "seriesId": 1, "seasonNumber": 2, "episodeNumber": 2, "title": "Gone", "hasFile": False,
         "monitored": True, "airDateUtc": iso((past + timedelta(hours=1)).astimezone(timezone.utc)), "series": show},
        {"id": 3, "seriesId": 1, "seasonNumber": 2, "episodeNumber": 3, "title": "Soon", "hasFile": False,
         "monitored": True, "airDateUtc": iso(fut.astimezone(timezone.utc)), "series": show,
         "finaleType": "season"},
        {"id": 4, "seriesId": 1, "seasonNumber": 2, "episodeNumber": 4, "title": "Way out", "hasFile": False,
         "monitored": True, "airDateUtc": iso((local + timedelta(days=60)).astimezone(timezone.utc)), "series": show},
    ])
    responses.get("http://nas:8989/api/v3/queue", json={"records": [{"episodeId": 2}]})
    responses.get("http://nas:7878/api/v3/calendar", json=[
        movie(60, tmdbId=600, hasFile=False, inCinemas=(today - timedelta(days=1)).isoformat() + "T00:00:00Z",
              digitalRelease=(today + timedelta(days=5)).isoformat() + "T00:00:00Z")])
    responses.get("http://nas:7878/api/v3/queue", json={"records": []})
    responses.get("http://nas:7879/api/v3/calendar", status=500, json={"message": "down"})
    start, end = (today - timedelta(days=7)).isoformat(), (today + timedelta(days=14)).isoformat()
    r = client.get(f"/api/calendar?start={start}&end={end}", headers=H)
    assert r.status_code == 200, r.data
    d = r.get_json()
    ents = d["entries"]
    by = {e["key"]: e for e in ents}
    assert by["sonarr:sonarr:e1"]["status"] == "downloaded" and by["sonarr:sonarr:e1"]["tag"] == "New season"
    assert by["sonarr:sonarr:e2"]["status"] == "downloading"
    assert by["sonarr:sonarr:e3"]["status"] == "upcoming" and by["sonarr:sonarr:e3"]["tag"] == "Season finale"
    assert by["sonarr:sonarr:e3"]["label"].startswith("Airs ") and by["sonarr:sonarr:e3"]["open"] == "tv-95396"
    assert "sonarr:sonarr:e4" not in by
    cin = by["radarr:radarr:60:inCinemas"]
    assert cin["status"] == "cinema" and cin["kind"] == "movie" and cin["open"] == "movie-600"
    assert by["radarr:radarr:60:digitalRelease"]["status"] == "upcoming"
    assert [e["date"] for e in ents] == sorted(e["date"] for e in ents)
    assert {s["name"] for s in d["sources"]} == {"TV", "Movies"}
    assert client.get("/api/config", headers=H).get_json()["calendar"] is True
    assert client.get("/api/calendar?start=2026-01-01&end=2026-06-01", headers=H).status_code == 400


# ---------------------------------------------------------------- v2: one page per title, seasons, dashboard

V2 = dict(H, **{"X-Harbour-App": "2"})


def _dune_movie_mocks():
    plex_common()
    responses.get("http://nas:5055/api/v1/movie/438631", json={
        "id": 438631, "title": "Dune", "tagline": "Beyond fear", "overview": "Spice.", "releaseDate": "2021-09-15",
        "runtime": 155, "voteAverage": 7.8, "genres": [{"name": "Sci-Fi"}, {"name": "Adventure"}],
        "releases": {"results": [{"iso_3166_1": "AU", "release_dates": [{"certification": "M"}]}]},
        "collection": {"id": 99, "name": "Dune Collection"},
        "watchProviders": [{"iso_3166_1": "AU", "flatrate": [{"name": "Netflix", "logoPath": "/n.png"}]}],
        "mediaInfo": {"id": 3, "status": 5},
        "credits": {"cast": [], "crew": []},
        "relatedVideos": [{"site": "YouTube", "type": "Featurette", "key": "fff", "name": "Making of"},
                          {"site": "YouTube", "type": "Trailer", "key": "abc", "name": "Official trailer"}]})
    responses.get("http://nas:5055/api/v1/movie/438631/ratingscombined",
                  json={"imdb": {"criticsScore": 8.0}, "rt": {"criticsScore": 83, "audienceScore": 90}})
    responses.get("http://nas:5055/api/v1/movie/438631/recommendations", json={"results": []})
    responses.get("http://nas:5055/api/v1/movie/438631/similar", json={"results": []})
    responses.get("http://nas:5055/api/v1/collection/99", json={"parts": [
        {"id": 2, "title": "Dune: Part Two", "releaseDate": "2024-03-01"},
        {"id": 438631, "title": "Dune", "releaseDate": "2021-09-15"}]})
    responses.get("http://nas:5055/api/v1/service/radarr", json=[{"id": 1, "is4k": False}, {"id": 2, "is4k": True}])
    hd = dict(MOVIES[1], tmdbId=438631, id=40, movieFile={"id": 400, "quality": {"quality": {"name": "WEBDL-1080p"}}})
    responses.get("http://nas:7878/api/v3/movie", json=[hd])
    responses.get("http://nas:7879/api/v3/movie", json=[dict(MOVIES[2], tmdbId=438631, id=41, hasFile=False,
                                                             monitored=True, isAvailable=True)])
    responses.get("http://nas:8989/api/v3/series", json=[])
    responses.get("http://nas:32400/hubs/search", json={"MediaContainer": {"Hub": []}})


@responses.activate
def test_v2_movie_page(client):
    _dune_movie_mocks()
    d = client.get("/api/svc/hub/hub/item/movie-438631", headers=V2).get_json()
    assert d["meta"] == ["2021", "M", "2h 35m", "Sci-Fi · Adventure"]
    assert [r["label"] for r in d["ratings"]] == ["IMDb", "Rotten Tomatoes", "Audience", "TMDB"]
    assert d["tagline"] == "Beyond fear" and not d["item"]["overview"].startswith("“")
    assert d["videos"][0]["key"] == "abc" and d["videos"][0]["thumb"].endswith("/abc/mqdefault.jpg")
    assert d["providers"] == [{"name": "Netflix", "logo": "https://image.tmdb.org/t/p/w92/n.png"}]
    assert d["rows"][0]["title"] == "Dune Collection" and d["rows"][0]["items"][0]["title"] == "Dune"
    tones = [c["tone"] for c in d["status"]]
    assert tones[:2] == ["ok", "warn"] and "WEBDL-1080p" in d["status"][0]["text"]
    acts = d["actions"]
    by = [(a["id"], a["label"]) for a in acts]
    assert by[0] == ("trailer", "Trailer") and acts[0]["opens"] == "trailer" and acts[0]["arg"] == "abc"
    # in both HD and 4K: no "Add" buttons; the 4K copy is missing so there's a direct search for it
    assert ("lib_search", "Search · Movies 4K") in by and not any(i == "request" for i, _ in by)
    menus = [a for a in acts if a["id"] == "menu"]
    assert [m["label"] for m in menus] == ["Movies", "Movies 4K", "More"]
    hd_items = {a["id"]: a for a in menus[0]["items"]}
    assert hd_items["lib_delete_file"]["arg"] == "radarr/radarr/40|400"
    assert hd_items["releases"]["arg"] == "radarr/radarr/40|movie"

    # actions pass through to the right Radarr and the page stays open
    responses.delete("http://nas:7878/api/v3/moviefile/400", json={})
    r = client.post("/api/svc/hub/hub/item/movie-438631/action", headers=V2,
                    json={"action": "lib_delete_file", "arg": "radarr/radarr/40|400"}).get_json()
    assert r["message"] == "File deleted" and r["refresh"] is True and r["close"] is False
    responses.delete("http://nas:7879/api/v3/movie/41", json={})
    r = client.post("/api/svc/hub/hub/item/movie-438631/action", headers=V2,
                    json={"action": "lib_delete", "arg": "radarr/radarr-truehd/41"}).get_json()
    assert r["close"] is False and "deleteFiles=false" in responses.calls[-1].request.url
    # manual search + grab go to that Radarr too
    responses.get("http://nas:7879/api/v3/release", json=[{"guid": "g1", "indexerId": 3, "title": "Dune.2160p",
                                                            "quality": {"quality": {"name": "Bluray-2160p"}}}])
    rel = client.get("/api/svc/hub/hub/item/movie-438631/releases?arg=radarr/radarr-truehd/41|movie", headers=V2)
    assert rel.status_code == 200 and rel.get_json()["items"][0]["guid"] == "g1"
    assert "movieId=41" in responses.calls[-1].request.url
    responses.post("http://nas:7879/api/v3/release", json={})
    g = client.post("/api/svc/hub/hub/item/movie-438631/grab", headers=V2,
                    json={"guid": "g1", "indexerId": 3, "arg": "radarr/radarr-truehd/41|movie"})
    assert g.status_code == 200, g.data
    # v1 still gets the old page
    old = client.get("/api/svc/hub/hub/item/movie-438631", headers=H).get_json()
    assert "meta" not in old and any(a["opens"] == "youtube" for a in old["actions"] if "opens" in a)


def _show_mocks():
    plex_common()
    responses.get("http://nas:5055/api/v1/tv/95396", json={
        "id": 95396, "name": "Severance", "firstAirDate": "2022-02-17", "status": "Returning Series",
        "numberOfSeasons": 2, "externalIds": {"tvdbId": 371980}, "genres": [{"name": "Drama"}],
        "contentRatings": {"results": [{"iso_3166_1": "AU", "rating": "MA15+"}]},
        "nextEpisodeToAir": {"airDate": "2099-01-01", "seasonNumber": 2, "episodeNumber": 3},
        "seasons": [{"seasonNumber": 0, "episodeCount": 1, "name": "Specials"},
                    {"seasonNumber": 1, "episodeCount": 2, "name": "Season 1", "posterPath": "/s1.jpg", "airDate": "2022-02-17"},
                    {"seasonNumber": 2, "episodeCount": 3, "name": "Season 2", "airDate": "2025-01-17"}],
        "mediaInfo": {"id": 8, "status": 4}, "credits": {"cast": [], "crew": []}, "relatedVideos": []})
    responses.get("http://nas:5055/api/v1/tv/95396/ratings", json={"criticsScore": 97})
    responses.get("http://nas:5055/api/v1/tv/95396/recommendations", json={"results": []})
    responses.get("http://nas:5055/api/v1/tv/95396/similar", json={"results": []})
    responses.get("http://nas:5055/api/v1/service/sonarr", json=[])
    ser = dict(SERIES[0], id=7, tmdbId=95396, tvdbId=371980, runtime=55,
               seasons=[{"seasonNumber": 1, "monitored": True, "statistics": {"episodeFileCount": 2, "totalEpisodeCount": 2}},
                        {"seasonNumber": 2, "monitored": True, "statistics": {"episodeFileCount": 1, "totalEpisodeCount": 3}}],
               statistics={"episodeFileCount": 3, "episodeCount": 5, "sizeOnDisk": 5e9})
    responses.get("http://nas:8989/api/v3/series", json=[ser])
    responses.get("http://nas:7878/api/v3/movie", json=[])
    responses.get("http://nas:7879/api/v3/movie", json=[])


@responses.activate
def test_v2_show_and_season(client):
    _show_mocks()
    responses.get("http://nas:32400/hubs/search", json={"MediaContainer": {"Hub": [{"Metadata": [
        {"type": "show", "ratingKey": "50", "leafCount": 5, "viewedLeafCount": 2, "Guid": [{"id": "tmdb://95396"}]}]}]}})
    d = client.get("/api/svc/hub/hub/item/tv-95396", headers=V2).get_json()
    assert d["meta"] == ["2022–", "MA15+", "2 seasons", "Drama"]
    assert [c["title"] for c in d["seasonCards"]] == ["Season 1", "Season 2", "Specials"]
    s1, s2 = d["seasonCards"][0], d["seasonCards"][1]
    assert s1["id"] == "tv-95396:s1" and s1["badge"] == "2/2" and s1["badgeTone"] == "ok" and s2["badge"] == "1/3"
    assert any(c["text"].startswith("Next: S02E03") for c in d["status"])
    ids = [a["id"] for a in d["actions"]]
    assert ids[0] == "play" and "lib_search" in ids and ids.count("menu") == 2

    now_ = datetime.now(timezone.utc)
    responses.get("http://nas:5055/api/v1/tv/95396/season/2", json={"name": "Season 2", "overview": "Back.", "episodes": [
        {"episodeNumber": 1, "name": "Hello, Ms. Cobel", "overview": "Mark returns.", "stillPath": "/e1.jpg",
         "airDate": "2025-01-17", "voteAverage": 8.4, "runtime": 50},
        {"episodeNumber": 2, "name": "Goodbye", "airDate": "2025-01-24"},
        {"episodeNumber": 3, "name": "Future", "airDate": "2099-01-01"}]})
    responses.get("http://nas:8989/api/v3/episode", json=[
        {"id": 101, "seasonNumber": 2, "episodeNumber": 1, "hasFile": True, "monitored": True, "episodeFileId": 9001,
         "airDateUtc": iso(now_ - timedelta(days=300)),
         "episodeFile": {"size": 2e9, "quality": {"quality": {"name": "WEBDL-2160p"}}}},
        {"id": 102, "seasonNumber": 2, "episodeNumber": 2, "hasFile": False, "monitored": True,
         "airDateUtc": iso(now_ - timedelta(days=290))},
        {"id": 103, "seasonNumber": 2, "episodeNumber": 3, "hasFile": False, "monitored": True,
         "airDateUtc": iso(now_ + timedelta(days=30))}])
    responses.get("http://nas:8989/api/v3/queue", json={"records": [{"episodeId": 102}]})
    responses.get("http://nas:32400/library/metadata/50/allLeaves", json={"MediaContainer": {"Metadata": [
        {"ratingKey": "60", "parentIndex": 2, "index": 1, "viewCount": 1, "duration": 3000000},
        {"ratingKey": "59", "parentIndex": 1, "index": 1, "viewCount": 1}]}})
    p = client.get("/api/svc/hub/hub/season/tv-95396/2", headers=V2)
    assert p.status_code == 200, p.data
    s = p.get_json()
    assert s["season"]["label"] == "Season 2" and "1 downloaded" in s["season"]["meta"]
    assert [x["number"] for x in s["seasons"]] == [1, 2, 0]
    e1, e2, e3 = s["episodes"]
    assert e1["status"] == "downloaded" and "WEBDL-2160p" in e1["statusLabel"] and e1["watched"] is True
    assert e1["still"] == "https://image.tmdb.org/t/p/w780/e1.jpg" and e1["rating"] == "8.4"
    a1 = {a["id"]: a for a in e1["actions"]}
    assert a1["play"]["arg"] == "plex:60" and a1["lib_episode_delete_file"]["arg"] == "sonarr/sonarr/7|9001"
    assert a1["releases"]["arg"] == "sonarr/sonarr/7|episode:101"
    assert e2["status"] == "downloading" and e3["status"] == "upcoming" and e3["statusLabel"].startswith("Airs")
    assert [a["id"] for a in s["actions"]] == ["lib_season_search", "releases", "lib_season_unmonitor"]

    responses.post("http://nas:8989/api/v3/command", json={})
    r = client.post("/api/svc/hub/hub/item/tv-95396/action", headers=V2,
                    json={"action": "lib_episode_search", "arg": "sonarr/sonarr/7|102"}).get_json()
    assert r["refresh"] is True
    assert json.loads(responses.calls[-1].request.body) == {"name": "EpisodeSearch", "episodeIds": [102]}
    responses.get("http://nas:8989/api/v3/release", json=[])
    rel = client.get("/api/svc/hub/hub/item/tv-95396/releases?arg=sonarr/sonarr/7|episode:102", headers=V2)
    assert rel.status_code == 200 and "episodeId=102" in responses.calls[-1].request.url


@responses.activate
def test_v2_unowned_show_season_offers_request(client):
    _show_mocks()
    responses.replace(responses.GET, "http://nas:8989/api/v3/series", json=[])
    responses.get("http://nas:32400/hubs/search", json={"MediaContainer": {"Hub": []}})
    responses.get("http://nas:5055/api/v1/tv/95396/season/1", json={"episodes": [
        {"episodeNumber": 1, "name": "Good News About Hell", "airDate": "2022-02-17"}]})
    s = client.get("/api/svc/hub/hub/season/tv-95396/1", headers=V2).get_json()
    assert s["episodes"][0]["status"] == "none" and not s["episodes"][0].get("actions")
    assert s["actions"][0]["id"] == "request_season" and s["actions"][0]["arg"] == "1"
    d = client.get("/api/svc/hub/hub/item/tv-95396", headers=V2).get_json()
    assert d["status"][-1]["text"] in ("Partly available", "Not in your library")
    assert "badge" not in d["seasonCards"][0]


@responses.activate
def test_v2_libraries_and_dashboard(client):
    today = datetime.now().astimezone()
    m_new = dict(MOVIES[1], tmdbId=438631, movieFile={"dateAdded": iso((today - timedelta(days=2)).astimezone(timezone.utc)),
                                                       "quality": {"quality": {"name": "WEBDL-1080p"}}})
    responses.get("http://nas:7878/api/v3/movie", json=[m_new, dict(MOVIES[3], tmdbId=55)])
    responses.get("http://nas:7879/api/v3/movie", json=[dict(m_new, id=99)])
    show = dict(SERIES[0], tmdbId=95396, tvdbId=1, runtime=30)
    responses.get("http://nas:8989/api/v3/series", json=[show])
    responses.get("http://nas:7878/api/v3/queue", json={"records": []})
    responses.get("http://nas:7879/api/v3/queue", json={"records": []})
    responses.get("http://nas:8989/api/v3/queue", json={"records": []})
    responses.get("http://nas:7878/api/v3/calendar", json=[])
    responses.get("http://nas:7879/api/v3/calendar", json=[])
    responses.get("http://nas:8989/api/v3/calendar", json=[
        {"id": 5, "seriesId": 1, "seasonNumber": 2, "episodeNumber": 3, "title": "Out now", "hasFile": True,
         "monitored": True, "airDateUtc": iso(today.replace(hour=0, minute=1, second=0).astimezone(timezone.utc)),
         "series": show},
        {"id": 6, "seriesId": 1, "seasonNumber": 2, "episodeNumber": 4, "title": "Next", "hasFile": False,
         "monitored": True, "airDateUtc": iso((today + timedelta(days=2)).astimezone(timezone.utc)), "series": show}])
    responses.get("http://nas:8989/api/v3/wanted/missing", json={"records": []})
    responses.get("http://nas:8989/api/v3/history", json={"records": []})
    responses.get("http://nas:7878/api/v3/diskspace", json=[{"path": "/data", "freeSpace": 1e12, "totalSpace": 2e13},
                                                             {"path": "/", "freeSpace": 5e9, "totalSpace": 6e10}])
    responses.get("http://nas:7878/api/v3/rootfolder", json=[{"path": "/data/media/movies"}])
    responses.get("http://nas:7878/api/v3/health", json=[{"type": "warning", "message": "Indexer down"}])
    responses.get("http://nas:7879/api/v3/health", json=[])
    responses.get("http://nas:8989/api/v3/health", json=[{"type": "notice", "message": "fine"}])
    responses.get("http://nas:5055/api/v1/discover/trending", json={"totalPages": 1, "results": [
        {"id": 7, "mediaType": "movie", "title": "Big", "posterPath": "/b.jpg"}]})

    cfg = client.get("/api/config", headers=V2).get_json()
    assert [l["inst"] for l in cfg["libraries"]] == ["movies", "tv"] and cfg["dashboard"] is True
    assert [s["inst"] for s in cfg["services"]][:1] == ["hub"]  # v1's menu is unchanged

    lib = client.get("/api/svc/lib/movies/home", headers=V2)
    assert lib.status_code == 200, lib.data
    rows = {r["title"]: r for r in lib.get_json()["rows"]}
    rec = rows["Recently added"]["items"]
    assert [c["id"] for c in rec].count("movie-438631") == 1  # HD + 4K copies merged
    allm = client.get("/api/svc/lib/movies/search?q=", headers=V2).get_json()["items"]
    assert {c["id"] for c in allm} >= {"movie-438631", "movie-55"}

    d = client.get("/api/dash", headers=V2)
    assert d.status_code == 200, d.data
    d = d.get_json()
    labels = {s["label"]: s for s in d["stats"]}
    assert labels["Movies"]["value"] == "2" and labels["TV shows"]["value"] == "1"
    assert labels["Free space"]["value"] == "931.3 GB" and "18.2 TB" in labels["Free space"]["sub"]
    assert d["today"][0]["openId"] == "tv-95396:s2e3"
    assert d["week"][0]["dow"] == "Today" and d["week"][0]["count"] == 1 and len(d["week"]) == 7
    titles = [r["title"] for r in d["rows"]]
    assert titles[0] == "Ready to watch" and "Coming up this week" in titles and "New movies" in titles
    assert d["rows"][0]["items"][0]["id"] == "tv-95396:s2e3" and d["rows"][0]["items"][0]["badge"] == "New"
    assert d["alerts"] == [{"text": "Movies: Indexer down", "tone": "warn"}]


@responses.activate
def test_trailer_resolve_and_proxy(client, monkeypatch):
    class P:
        returncode = 0
        stderr = ""
        stdout = json.dumps({"title": "Trailer", "duration": 120, "requested_formats": [
            {"url": "https://rr1.googlevideo.com/v", "height": 1080, "http_headers": {"User-Agent": "UA"}},
            {"url": "https://rr1.googlevideo.com/a", "http_headers": {"User-Agent": "UA"}}]})
    calls = []
    monkeypatch.setattr(gw.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or P())
    monkeypatch.setattr(gw, "trailers_available", lambda: True)
    gw._YT.clear()
    t = client.get("/api/trailer/abcDEF12345", headers=V2).get_json()
    assert t == {"title": "Trailer", "duration": 120, "height": 1080, "video": "/yt/abcDEF12345/0",
                 "audio": "/yt/abcDEF12345/1"}
    assert "yt_dlp" in calls[0] and calls[0][-1].endswith("v=abcDEF12345")
    responses.get("https://rr1.googlevideo.com/v", body=b"0123456789", status=206,
                  headers={"Content-Range": "bytes 0-9/100", "Content-Type": "video/mp4"})
    r = client.get("/yt/abcDEF12345/0?t=tok", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.data == b"0123456789" and r.headers["Content-Range"] == "bytes 0-9/100"
    assert responses.calls[-1].request.headers["Range"] == "bytes=0-9"
    assert responses.calls[-1].request.headers["User-Agent"] == "UA"
    assert client.get("/api/trailer/bad!id", headers=V2).status_code in (400, 404)
    assert len(calls) == 1  # cached


@responses.activate
def test_trailer_proxy_fetches_in_chunks(client, monkeypatch):
    gw._YT["chunkKEY123"] = (__import__("time").time(), {"title": "T", "duration": 1, "streams": [
        {"url": "https://rr2.googlevideo.com/v", "headers": {}, "height": 720}]})
    data = bytes(range(26))
    seen = []

    def cb(req):
        a, b = map(int, req.headers["Range"].split("=")[1].split("-"))
        seen.append((a, b))
        part = data[a:b + 1]
        return 206, {"Content-Range": f"bytes {a}-{a + len(part) - 1}/{len(data)}", "Content-Type": "video/mp4"}, part

    responses.add_callback(responses.GET, "https://rr2.googlevideo.com/v", callback=cb)
    monkeypatch.setattr(gw, "YT_CHUNK", 10)
    r = client.get("/yt/chunkKEY123/0?t=tok", headers={"Range": "bytes=3-"})
    assert r.status_code == 206 and r.data == data[3:]
    assert r.headers["Content-Range"] == "bytes 3-25/26" and r.headers["Content-Length"] == "23"
    assert seen == [(3, 12), (13, 22), (23, 25)]
    r = client.get("/yt/chunkKEY123/0?t=tok")
    assert r.status_code == 200 and r.data == data


@responses.activate
def test_recording_saved_to_nas(client):
    responses.post("http://nas:5000/webapi/auth.cgi", json={"success": True, "data": {"sid": "S1"}})
    got = {}

    def up(req):
        body = req.body if isinstance(req.body, (bytes, bytearray)) else b"".join(iter(lambda: req.body.read(7), b""))
        got["body"], got["url"], got["ct"] = body, req.url, req.headers["Content-Type"]
        return 200, {}, json.dumps({"success": True})

    responses.add_callback(responses.POST, "http://nas:5000/webapi/entry.cgi", callback=up)
    video = os.urandom(3000)
    r = client.post("/api/recording?name=Demo%201.mp4", data=video, headers=dict(H, **{"Content-Type": "video/mp4"}))
    assert r.status_code == 200, r.data
    assert r.get_json() == {"path": "/video/Harbour recordings/Demo 1.mp4", "size": 3000}
    assert "SYNO.FileStation.Upload" in got["url"] and "_sid=S1" in got["url"]
    b = got["body"]
    assert b'name="path"\r\n\r\n/video/Harbour recordings\r\n' in b and b'filename="Demo 1.mp4"' in b
    assert video in b and got["ct"].startswith("multipart/form-data; boundary=")
    assert b.index(b'name="create_parents"') < b.index(b'name="file"')


NZB_XML = b"""<?xml version="1.0"?><rss xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel>
<item><title>Dune.Part.Two.2024.2160p.WEB-DL</title><link>https://idx.example/getnzb/abc?apikey=K</link>
<pubDate>Mon, 01 Apr 2024 10:00:00 +0000</pubDate><enclosure url="https://idx.example/getnzb/abc?apikey=K" length="20000000000"/>
<newznab:attr name="category" value="2045"/><newznab:attr name="grabs" value="321"/></item>
</channel></rss>"""


@responses.activate
def test_indexer_search_and_grab(client, monkeypatch):
    monkeypatch.setitem(gw.CFG, "prowlarr", {"url": "http://nas:9696", "api_key": "PK"})
    monkeypatch.setitem(gw.CFG, "newznab", [{"name": "NZBgeek", "url": "https://idx.example", "api_key": "K"}])
    qb = gw.QBittorrent({"id": "qb", "url": "http://nas:8080", "api_key": "QK"})
    nz = gw.NzbGet({"id": "nz", "url": "http://nas:6789"})
    monkeypatch.setattr(gw, "CLIENTS", {"qb": qb, "nz": nz})
    responses.get("http://nas:9696/api/v1/search", json=[
        {"guid": "g1", "indexerId": 4, "indexer": "1337x", "title": "Dune Part Two 2024 1080p", "protocol": "torrent",
         "size": 9000000000, "seeders": 900, "leechers": 20, "publishDate": iso(now - timedelta(days=3)),
         "downloadUrl": "http://nas:9696/4/download?apikey=PK&link=x", "categories": [{"id": 2040}]},
        {"guid": "g2", "indexerId": 5, "indexer": "TPB", "title": "Dune Part Two 2024 720p", "protocol": "torrent",
         "size": 3000000000, "seeders": 50, "magnetUrl": "magnet:?xt=urn:btih:AAA", "categories": [{"id": 2030}]}])
    responses.get("https://idx.example/api", body=NZB_XML)
    r = client.get("/api/indexers/search?q=dune%20part%20two&kind=movies", headers=H)
    assert r.status_code == 200, r.data
    res = r.get_json()["results"]
    assert [x["indexer"] for x in res] == ["1337x", "TPB", "NZBgeek"] or res[0]["indexer"] == "1337x"
    tor = next(x for x in res if x["indexer"] == "1337x")
    nzb = next(x for x in res if x["indexer"] == "NZBgeek")
    assert tor["protocol"] == "torrent" and tor["category"] == "Movies" and tor["age"] == "3d"
    assert nzb["protocol"] == "usenet" and nzb["grabs"] == 321 and nzb["size"] == 20000000000
    assert "apikey" not in json.dumps(res) and "url" not in tor
    assert "categories=2000" in [c.request.url for c in responses.calls if "9696" in c.request.url][0]

    responses.get("http://nas:8080/api/v2/torrents/categories", json={"radarr": {}, "tv-sonarr": {}})
    responses.post("http://nas:6789/jsonrpc", json={"result": [{"Name": "Category1.Name", "Value": "movies"},
                                                               {"Name": "Other", "Value": "x"}]})
    t = client.get("/api/indexers/targets", headers=H).get_json()
    assert [o["value"] for o in t["torrent"]] == ["qb|", "qb|radarr", "qb|tv-sonarr", "prowlarr|"]
    assert [o["value"] for o in t["usenet"]][:2] == ["nz|", "nz|movies"]

    # torrent: the download link redirects to a magnet → magnet goes to qBittorrent
    responses.get("http://nas:9696/4/download", status=301, headers={"Location": "magnet:?xt=urn:btih:BBB"})
    responses.post("http://nas:8080/api/v2/torrents/add", body="Ok.")
    g = client.post("/api/indexers/grab", headers=H, json={"id": tor["id"], "target": "qb|radarr"}).get_json()
    assert g["message"] == "Sent to qBittorrent ✓"
    body = responses.calls[-1].request.body
    assert "magnet%3A%3Fxt%3Durn%3Abtih%3ABBB" in body and "category=radarr" in body

    responses.replace(responses.POST, "http://nas:6789/jsonrpc", json={"result": 77})
    g = client.post("/api/indexers/grab", headers=H, json={"id": nzb["id"], "target": "nz|movies"}).get_json()
    assert g["message"] == "Sent to NZBGet ✓"
    sent = json.loads(responses.calls[-1].request.body)
    assert sent["method"] == "append" and sent["params"][1].startswith("https://idx.example/getnzb") \
        and sent["params"][2] == "movies"

    responses.post("http://nas:9696/api/v1/search", json={})
    g = client.post("/api/indexers/grab", headers=H, json={"id": tor["id"], "target": "prowlarr|"}).get_json()
    assert "Prowlarr" in g["message"] and json.loads(responses.calls[-1].request.body) == {"guid": "g1", "indexerId": 4}
    assert client.post("/api/indexers/grab", headers=H, json={"id": "nope", "target": "qb|"}).status_code == 410
    assert client.post("/api/indexers/grab", headers=H, json={"id": nzb["id"], "target": "qb|"}).status_code == 400


# ---------------------------------------------------------------- V3: setup page, pairing, auto-detect

@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A gateway with no config yet (first run), saving into a temp folder."""
    monkeypatch.setattr(gw, "CONFIG_PATH", str(tmp_path / "config.json"))
    gw.init()
    yield gw.app.test_client(), tmp_path
    monkeypatch.undo()
    gw.init()


def test_first_run_setup_and_save(fresh):
    c, tmp = fresh
    assert c.get("/?json=1").get_json()["setup"] is False
    assert c.get("/", headers={"Accept": "text/html"}).status_code == 302
    assert c.get("/api/config").status_code == 503
    page = c.get("/setup")
    assert page.status_code == 200 and b"Harbour setup" in page.data
    st = c.get("/setup/api/state").get_json()
    assert st["firstRun"] is True and st["writable"] is True
    assert c.post("/setup/api/save", json={"config": {"token": "12"}}).status_code == 400
    r = c.post("/setup/api/save", json={"config": {
        "token": "4321",
        "radarr": [{"name": "Radarr", "url": "http://nas:7878/", "api_key": "a"},
                   {"name": "Radarr 4K", "url": "http://nas:7879", "api_key": "b", "icon": "4k"},
                   {"name": "", "url": ""}],
        "sonarr": [], "plex": {"url": ""}, "qbittorrent": {"url": "http://nas:8080", "api_key": "q"},
        "filestation": {"url": "http://nas:5000", "username": "u", "password": "p", "allowed_shares": "/data, /video"}}})
    assert r.status_code == 200, r.data
    saved = json.load(open(tmp / "config.json"))
    assert [x["id"] for x in saved["radarr"]] == ["radarr", "radarr-4k"] and saved["radarr"][0]["url"] == "http://nas:7878"
    assert "sonarr" not in saved and "plex" not in saved and saved["filestation"]["allowed_shares"] == ["/data", "/video"]
    # now it's protected: a new browser must log in, the cookie from saving works
    assert c.get("/setup/api/state").status_code == 200
    other = gw.app.test_client()
    assert other.get("/setup/api/state").status_code == 401
    assert other.post("/setup/api/login", json={"pin": "nope"}).status_code == 401
    assert other.post("/setup/api/login", json={"pin": "4321"}).status_code == 200
    assert other.get("/setup/api/state").get_json()["config"]["radarr"][1]["name"] == "Radarr 4K"
    assert other.get("/api/config", headers={"X-Token": "4321"}).status_code == 200
    assert {s["inst"] for s in other.get("/api/config", headers={"X-Token": "4321"}).get_json()["services"]} \
        == {"radarr", "radarr-4k"}


def test_pairing(fresh):
    c, tmp = fresh
    c.post("/setup/api/save", json={"config": {"token": "4321"}})
    tv = gw.app.test_client()
    p = tv.post("/api/pair/start", json={"name": "Lounge Fire TV"}).get_json()
    assert len(p["code"]) == 4
    assert tv.get(f"/api/pair/status?id={p['id']}").get_json() == {"status": "pending"}
    st = c.get("/setup/api/state").get_json()
    assert st["pending"][0]["code"] == p["code"] and st["pending"][0]["name"] == "Lounge Fire TV"
    assert tv.post("/setup/api/pair", json={"id": p["id"], "approve": True}).status_code == 401  # TV can't approve itself
    assert c.post("/setup/api/pair", json={"id": p["id"], "approve": True}).status_code == 200
    got = tv.get(f"/api/pair/status?id={p['id']}").get_json()
    assert got["status"] == "approved" and len(got["token"]) > 20
    assert tv.get(f"/api/pair/status?id={p['id']}").get_json()["status"] == "expired"  # handed over once
    assert tv.get("/api/config", headers={"X-Token": got["token"]}).status_code == 200
    devs = c.get("/setup/api/state").get_json()["devices"]
    assert devs[0]["name"] == "Lounge Fire TV" and "token" not in devs[0]
    assert "devices" not in c.get("/setup/api/state").get_json()["config"]
    # saving the page keeps paired devices; removing one revokes it
    c.post("/setup/api/save", json={"config": {"token": "4321"}})
    assert tv.get("/api/config", headers={"X-Token": got["token"]}).status_code == 200
    c.post("/setup/api/device/remove", json={"index": 0})
    assert tv.get("/api/config", headers={"X-Token": got["token"]}).status_code == 401


@responses.activate
def test_detect_apps(tmp_path, monkeypatch):
    stack = tmp_path / "docker"
    (stack / "radarr").mkdir(parents=True)
    (stack / "radarr" / "config.xml").write_text("<Config><Port>7878</Port><ApiKey>RK</ApiKey><InstanceName>Radarr</InstanceName></Config>")
    (stack / "radarr-truehd").mkdir()
    (stack / "radarr-truehd" / "config.xml").write_text("<Config><Port>7879</Port><ApiKey>RK4</ApiKey><UrlBase>r4k</UrlBase></Config>")
    (stack / "sonarr" / "MediaCover").mkdir(parents=True)
    (stack / "sonarr" / "MediaCover" / "config.xml").write_text("<Config><Port>1</Port><ApiKey>NO</ApiKey></Config>")
    (stack / "sonarr" / "config.xml").write_text("<Config><Port>8989</Port><ApiKey>SK</ApiKey></Config>")
    (stack / "jellyseerr" / "config").mkdir(parents=True)
    (stack / "jellyseerr" / "config" / "settings.json").write_text(json.dumps({"main": {"apiKey": "JK"}}))
    plex = stack / "plex" / "Library" / "Application Support" / "Plex Media Server"
    plex.mkdir(parents=True)
    (plex / "Preferences.xml").write_text('<Preferences PlexOnlineToken="PT" />')
    (stack / "nzbget").mkdir()
    (stack / "nzbget" / "nzbget.conf").write_text("ControlPort=6789\nControlUsername=nzb\nControlPassword=pw\n")
    (stack / "tautulli").mkdir()
    (stack / "tautulli" / "config.ini").write_text("[General]\nhttp_port = 8181\nhttp_root = \"\"\napi_key = TK\n")
    responses.get("http://192.168.4.68:8181/api/v2",
                  json={"response": {"result": "success", "data": {"tautulli_version": "v2.15"}}})
    monkeypatch.setattr(gw, "STACK_DIR", str(stack))
    responses.get("http://192.168.4.68:7878/api/v3/system/status", json={"appName": "Radarr", "version": "5"})
    responses.get("http://192.168.4.68:7879/r4k/api/v3/system/status", status=401)
    responses.get("http://192.168.4.68:8989/api/v3/system/status", json={"appName": "Sonarr", "version": "4"})
    responses.get("http://192.168.4.68:5055/api/v1/settings/main", json={})
    responses.get("http://192.168.4.68:5055/api/v1/status", json={"version": "2.7"})
    responses.get("http://192.168.4.68:32400/library/sections", json={"MediaContainer": {"Directory": [{}, {}]}})
    responses.post("http://192.168.4.68:6789/jsonrpc", json={"result": "25.0"})
    got = gw.detect_apps("192.168.4.68")
    assert got["mounted"] is True
    by = {(f["kind"], f["id"]): f for f in got["found"]}
    assert by[("radarr", "radarr")]["api_key"] == "RK" and by[("radarr", "radarr")]["verified"] is True
    r4 = by[("radarr", "radarr-truehd")]
    assert r4["url"] == "http://192.168.4.68:7879/r4k" and r4["verified"] is False and "key" in r4["message"]
    assert by[("sonarr", "sonarr")]["api_key"] == "SK"  # the one under MediaCover was skipped
    assert by[("seerr", "seerr")]["name"] == "Jellyseerr" and by[("seerr", "seerr")]["verified"]
    assert by[("plex", "plex")]["token"] == "PT" and "2 libraries" in by[("plex", "plex")]["message"]
    assert by[("nzbget", "nzbget")]["username"] == "nzb" and by[("nzbget", "nzbget")]["verified"]
    t = by[("tautulli", "tautulli")]
    assert t["url"] == "http://192.168.4.68:8181" and t["api_key"] == "TK" and "v2.15" in t["message"]
    monkeypatch.setattr(gw, "STACK_DIR", str(tmp_path / "missing"))
    assert gw.detect_apps("x") == {"mounted": False, "found": []}


# ---------------------------------------------------------------- iPhone web app: login + settings

REMOTE = {"Host": "harbour.example.com", "X-Forwarded-For": "203.0.113.9"}


@pytest.fixture
def web(client):
    """Each web test starts from the original config and puts it back afterwards."""
    with open(gw.CONFIG_PATH) as f:
        orig = f.read()
    yield client
    with open(gw.CONFIG_PATH, "w") as f:
        f.write(orig)
    gw.init()


def _setup(c, pw="harbour-pass"):
    return c.post("/api/login/setup", json={"token": "tok", "password": pw}, headers=REMOTE)


def test_web_remote_needs_login_and_lan_token_still_works(web):
    assert web.get("/api/config", headers=H).status_code == 200          # LAN + token: unchanged
    r = web.get("/api/config", headers={**H, **REMOTE})                 # remote: token isn't enough
    assert r.status_code == 401 and r.get_json()["login"] is True
    assert web.get("/api/login/status", headers=REMOTE).get_json()["setup_needed"] is True
    assert web.get("/m/", headers=REMOTE).status_code == 200            # app shell is public
    # browsers hitting / land in the app; the JSON hello is unchanged otherwise
    assert web.get("/", headers={"Accept": "text/html"}).status_code == 302
    assert web.get("/").get_json()["ok"] is True


def test_web_setup_login_session_and_csrf(web):
    assert web.post("/api/login/setup", json={"token": "nope", "password": "harbour-pass"},
                    headers=REMOTE).status_code == 401
    assert _setup(web, "short").status_code == 400
    r = _setup(web)
    assert r.status_code == 200 and "HttpOnly" in r.headers["Set-Cookie"] and "Secure" in r.headers["Set-Cookie"]
    with open(gw.CONFIG_PATH) as f:
        saved = json.load(f)
    assert saved["web"]["password"] != "harbour-pass" and saved["radarr"][0]["api_key"] == "rk"
    assert web.get("/api/config", headers=REMOTE).status_code == 200   # cookie session
    # unsafe requests with the cookie need the app's header
    assert web.post("/api/reload", headers=REMOTE).status_code == 403
    assert web.post("/api/reload", headers={**REMOTE, "X-Harbour-Web": "1"}).status_code == 200
    assert _setup(web).status_code == 409                               # only once
    web.post("/api/logout", headers={**REMOTE, "X-Harbour-Web": "1"})
    assert web.get("/api/config", headers=REMOTE).status_code == 401
    assert web.post("/api/login", json={"password": "harbour-pass"}, headers=REMOTE).status_code == 200
    assert web.get("/api/config", headers=REMOTE).status_code == 200


def test_web_login_rate_limited(web, monkeypatch):
    monkeypatch.setattr(web_time := __import__("web_access").time, "sleep", lambda s: None)
    _setup(web)
    web.post("/api/logout", headers={**REMOTE, "X-Harbour-Web": "1"})
    hdr = {**REMOTE, "X-Forwarded-For": "198.51.100.7"}
    codes = [web.post("/api/login", json={"password": "wrong"}, headers=hdr).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert web.post("/api/login", json={"password": "harbour-pass"}, headers=hdr).status_code == 429


def test_web_password_change_signs_out_others(web):
    _setup(web)
    other = gw.app.test_client()
    other.post("/api/login", json={"password": "harbour-pass"}, headers=REMOTE)
    assert other.get("/api/config", headers=REMOTE).status_code == 200
    r = web.post("/api/login/password", json={"current": "harbour-pass", "new": "new-password-1"},
                 headers={**REMOTE, "X-Harbour-Web": "1"})
    assert r.status_code == 200
    assert web.get("/api/config", headers=REMOTE).status_code == 200
    assert other.get("/api/config", headers=REMOTE).status_code == 401


def test_web_settings_masked_and_saved(web):
    s = web.get("/api/settings", headers=H).get_json()
    r0 = s["values"]["radarr"][0]
    assert r0["api_key"] == "" and r0["_set"] == ["api_key"] and r0["id"] == "radarr"
    assert s["values"]["filestation"]["allowed_shares"] == "/video, /downloads"
    assert "\"rk\"" not in json.dumps(s["values"]) and "admintok" not in json.dumps(s["values"])

    vals = s["values"]
    vals["radarr"][0]["url"] = "192.168.4.68:7878/"                    # scheme added, slash dropped
    vals["radarr"].append({"name": "Radarr Anime", "url": "http://nas:7880", "api_key": "rk-anime"})
    vals["sonarr"] = []                                                   # removed
    vals["filestation"]["allowed_shares"] = "data, /video/"
    r = web.put("/api/settings", json={"values": vals}, headers=H)
    assert r.status_code == 200, r.data
    with open(gw.CONFIG_PATH) as f:
        c = json.load(f)
    assert c["radarr"][0] == {"id": "radarr", "name": "Movies", "url": "http://192.168.4.68:7878", "api_key": "rk"}
    assert c["radarr"][2]["id"] == "radarr-anime" and c["radarr"][2]["api_key"] == "rk-anime"
    assert c["radarr"][1]["icon"] == "4k" and c["radarr"][1]["api_key"] == "rk4"
    assert "sonarr" not in c and c["filestation"]["allowed_shares"] == ["/data", "/video"]
    assert c["seerr"]["four_k"] is True and c["seerr"]["api_key"] == "ok"   # unknown keys + shape kept
    assert c["filestation"]["password"] == "p" and c["plex"]["token"] == "admintok"
    insts = [x["inst"] for x in web.get("/api/config", headers=H).get_json()["services"]]
    assert "radarr-anime" in insts and "sonarr" not in insts              # reloaded live


def test_web_settings_validation_and_rollback(web):
    vals = web.get("/api/settings", headers=H).get_json()["values"]
    vals["radarr"].append({"name": "No key", "url": "http://nas:1"})
    r = web.put("/api/settings", json={"values": vals}, headers=H)
    assert r.status_code == 400 and "API key" in r.get_json()["error"]
    with open(gw.CONFIG_PATH) as f:
        assert len(json.load(f)["radarr"]) == 2                           # untouched


@responses.activate
def test_web_settings_connection_tests(web):
    responses.get("http://nas:7878/api/v3/system/status", json={"appName": "Radarr", "version": "5.9"},
                  match=[responses.matchers.header_matcher({"X-Api-Key": "rk"})])
    r = web.post("/api/settings/test", json={"section": "radarr",
                                             "entry": {"id": "radarr", "name": "Movies", "url": "http://nas:7878", "api_key": ""}},
                 headers=H).get_json()
    assert r == {"ok": True, "message": "Radarr 5.9 ✓"}         # saved key used when left blank
    responses.get("http://nas:9999/api/v3/system/status", status=401)
    r = web.post("/api/settings/test", json={"section": "sonarr",
                                             "entry": {"name": "x", "url": "http://nas:9999", "api_key": "bad"}},
                 headers=H).get_json()
    assert r["ok"] is False and "API key" in r["message"]
    r = web.post("/api/settings/test", json={"section": "sonarr",
                                             "entry": {"name": "x", "url": "http://nas:1", "api_key": "k"}},
                 headers=H).get_json()
    assert r["ok"] is False and "reach" in r["message"].lower()


def test_web_rate_limit_ignores_spoofed_forwarded_for(web, monkeypatch):
    monkeypatch.setattr(__import__("web_access").time, "sleep", lambda s: None)
    _setup(web)
    web.post("/api/logout", headers={**REMOTE, "X-Harbour-Web": "1"})
    # attacker varies the first X-Forwarded-For entry; the proxy appends the real address last
    codes = [web.post("/api/login", json={"password": "x"},
                      headers={"Host": "harbour.example.com", "X-Forwarded-For": f"10.0.0.{n}, 198.51.100.50"}).status_code
             for n in range(6)]
    assert codes[5] == 429


def test_web_v3_device_keys_and_remote_setup(web):
    gw.CFG["devices"] = [{"name": "Lounge TV", "token": "dev-key"}]
    assert web.get("/api/config", headers={"X-Token": "dev-key"}).status_code == 200            # paired TV, LAN
    assert web.get("/api/config", headers={"X-Token": "dev-key", **REMOTE}).status_code == 401  # not from outside
    assert web.get("/setup", headers=REMOTE).status_code == 403                                 # setup: LAN only
    assert web.post("/setup/api/login", json={"pin": "tok"}, headers=REMOTE).status_code == 403
    assert web.post("/api/pair/start", json={"name": "x"}, headers=REMOTE).status_code == 403
    assert web.get("/setup").status_code == 200                                                  # fine at home
    assert web.get("/", headers={"Accept": "text/html"}).headers["Location"] == "/m/"           # set up → app
    _setup(web)
    assert web.get("/setup", headers=REMOTE).status_code == 200                                 # signed in → ok
    gw.CFG.pop("devices", None)


FAKE_YTDLP = r'''
import json, os, sys
a = sys.argv[1:]
url = a[-1]
if "/status/404" in url:
    print("ERROR: [twitter] 404: No video could be found in this tweet", file=sys.stderr); sys.exit(1)
fmts = [{"format_id": "hls-832", "ext": "mp4", "url": "https://video.twimg.com/x.m3u8", "height": 720, "width": 1280},
        {"format_id": "http-256", "ext": "mp4", "tbr": 256, "url": "https://video.twimg.com/ext_tw_video/1/pu/vid/480x270/a.mp4"},
        {"format_id": "http-2176", "ext": "mp4", "tbr": 2176, "url": "https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/b.mp4"},
        {"format_id": "http-832", "ext": "mp4", "tbr": 832, "url": "https://video.twimg.com/ext_tw_video/1/pu/vid/640x360/c.mp4"}]
if "-J" in a:
    print(json.dumps({"id": "2111925739440202230", "uploader": "Liam", "uploader_id": "liam_out_loud",
                      "upload_date": "20261018", "description": "Look at this https://t.co/abc", "duration": 20,
                      "thumbnail": "https://pbs.twimg.com/t.jpg", "formats": fmts}))
else:
    assert "http-2176" in a[a.index("-f") + 1], a
    out = a[a.index("-o") + 1].replace("%(ext)s", "mp4")
    for p in (10, 55.5, 100):
        print(f"[download]  {p}% of 5.00MiB at 1MiB/s ETA 00:01", flush=True)
    open(out, "wb").write(b"\0" * 1234)
'''


@responses.activate
def test_x_download(client, monkeypatch, tmp_path):
    import time as _t
    # The chosen size is fetched directly; the first time X refuses, so yt-dlp is the fallback.
    responses.get("https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/b.mp4", status=403)
    responses.get("https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/b.mp4", body=b"\1" * 5000)
    responses.head("https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/b.mp4", headers={"Content-Length": "5000"})
    (tmp_path / "yt_dlp").mkdir()
    (tmp_path / "yt_dlp" / "__init__.py").write_text("")
    (tmp_path / "yt_dlp" / "__main__.py").write_text(FAKE_YTDLP)
    monkeypatch.setattr(gw, "YT_DIR", str(tmp_path))
    monkeypatch.setitem(gw.CFG, "twitter", {"folder": "/downloads/twitter"})
    saved = {}

    def fake_upload(folder, name, src):
        saved.update(folder=folder, name=name, size=os.path.getsize(src))
        return f"{folder}/{name}"
    monkeypatch.setattr(gw.FILES, "upload", fake_upload)

    assert client.get("/api/config", headers=H).get_json()["xdl"] is True
    bad = client.post("/api/x/lookup", headers=H, json={"url": "https://example.com/a/status/1"})
    assert bad.status_code == 400
    none = client.post("/api/x/lookup", headers=H, json={"url": "https://x.com/a/status/40400000404"})
    assert none.status_code == 400 or "video" in none.get_json()["error"]

    r = client.post("/api/x/lookup", headers=H,
                    json={"url": "https://x.com/liam_out_loud/status/2111925739440202230?s=46"}).get_json()
    assert r["url"] == "https://x.com/liam_out_loud/status/2111925739440202230"
    assert r["handle"] == "liam_out_loud" and r["text"] == "Look at this"
    opts = r["videos"][0]["options"]
    assert [o["label"] for o in opts] == ["1280×720", "640×360", "480×270"]      # HLS left out, biggest first
    assert opts[0]["quality"] == "HD" and opts[0]["size"] == 5000 and opts[0]["exact"]   # real size from X
    assert opts[1]["size"] == 832 * 1000 // 8 * 20 and not opts[1].get("exact")           # no answer: estimate

    j = client.post("/api/x/download", headers=H, json={"url": r["url"], "index": 1, "format": "http-2176"}).get_json()["job"]
    for _ in range(100):
        job = next(x for x in client.get("/api/x/jobs", headers=H).get_json()["jobs"] if x["id"] == j["id"])
        if job["status"] in ("done", "failed"):
            break
        _t.sleep(0.1)
    assert job["status"] == "done", job
    assert saved == {"folder": "/downloads/twitter", "name": "liam_out_loud 2026-10-18 2111925739440202230 720p.mp4",
                     "size": 1234}
    stale = client.post("/api/x/download", headers=H, json={"url": r["url"], "format": "http-9999"})
    assert stale.status_code == 400
    assert "_url" not in opts[0]                                              # direct links stay on the gateway

    # Second time: the exact file the user picked is downloaded directly.
    j = client.post("/api/x/download", headers=H, json={"url": r["url"], "index": 1, "format": "http-2176"}).get_json()["job"]
    for _ in range(100):
        job = next(x for x in client.get("/api/x/jobs", headers=H).get_json()["jobs"] if x["id"] == j["id"])
        if job["status"] in ("done", "failed"):
            break
        _t.sleep(0.1)
    assert job["status"] == "done" and saved["size"] == 5000, job

    # History: remove one, then clear the rest.
    left = client.delete(f"/api/x/jobs/{j['id']}", headers=H).get_json()["jobs"]
    assert all(x["id"] != j["id"] for x in left) and left
    assert client.delete("/api/x/jobs", headers=H).get_json()["jobs"] == []


@responses.activate
def test_file_play_and_stream(client):
    responses.add(responses.POST, "http://nas:5000/webapi/auth.cgi",
                  json={"success": True, "data": {"sid": "S1"}})
    got = {}

    def dl(req):
        got["url"], got["range"] = req.url, req.headers.get("Range")
        return 206, {"Content-Type": "video/x-matroska", "Content-Range": "bytes 0-3/10",
                     "Content-Length": "4"}, b"abcd"
    responses.add_callback(responses.GET, "http://nas:5000/webapi/entry.cgi", callback=dl)
    p = client.get("/api/play?ref=" + quote("file:/video/Some Film.mkv"), headers=H).get_json()
    assert p["ratingKey"] == "" and p["title"] == "Some Film" and "/api/files/stream?" in p["url"]
    assert "t=" in p["url"]
    r = client.get("/api/files/stream?path=" + quote("/video/Some Film.mkv"), headers={**H, "Range": "bytes=0-3"})
    assert r.status_code == 206 and r.data == b"abcd" and r.headers["Content-Range"] == "bytes 0-3/10"
    assert "SYNO.FileStation.Download" in got["url"] and got["range"] == "bytes=0-3"
    # Outside the allowed shares is refused.
    assert client.get("/api/play?ref=" + quote("file:/homes/x.mkv"), headers=H).status_code == 403


def test_x_cookies(client, tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(CFG))
    monkeypatch.setattr(gw, "CONFIG_PATH", str(cfg_path))
    gw.init()
    assert client.get("/api/x/cookies", headers=H).get_json()["set"] is False
    bad = client.post("/api/x/cookies", headers=H, json={"auth_token": "nope", "ct0": "x"})
    assert bad.status_code == 400
    r = client.post("/api/x/cookies", headers=H, json={"auth_token": " \"" + "a1" * 20 + "\" ",
                                                       "ct0": "ct0=" + "b2" * 40 + "; foo=bar"})
    assert r.status_code == 200
    text = (tmp_path / "x-cookies.txt").read_text()
    assert "\tauth_token\t" + "a1" * 20 in text and "\tct0\t" + "b2" * 40 in text and ".twitter.com" in text
    assert json.loads(cfg_path.read_text())["twitter"]["cookies"] == str(tmp_path / "x-cookies.txt")
    assert client.get("/api/x/cookies", headers=H).get_json()["set"] is True
    assert client.delete("/api/x/cookies", headers=H).status_code == 200
    assert not (tmp_path / "x-cookies.txt").exists() and "cookies" not in json.loads(cfg_path.read_text())["twitter"]


@responses.activate
def test_tautulli_activity(client, monkeypatch):
    monkeypatch.setitem(gw.CFG, "tautulli", {"url": "http://nas:8181", "api_key": "tk"})
    assert client.get("/api/config", headers=H).get_json()["activity"] is True

    def api(req):
        from urllib.parse import parse_qs, urlsplit as us
        q = {k: v[0] for k, v in parse_qs(us(req.url).query).items()}
        assert q["apikey"] == "tk"
        cmd = q["cmd"]
        data = {
            "get_activity": {"stream_count": "1", "total_bandwidth": 12000, "lan_bandwidth": 12000, "wan_bandwidth": 0,
                             "sessions": [{"session_key": "7", "friendly_name": "Matt", "media_type": "episode",
                                           "grandparent_title": "Nathan For You", "title": "Smokers Allowed",
                                           "parent_media_index": "2", "media_index": "5", "grandparent_thumb": "/library/metadata/1/thumb/1",
                                           "state": "playing", "progress_percent": "42", "view_offset": "600000", "duration": "1320000",
                                           "player": "Lounge", "transcode_decision": "direct play",
                                           "stream_video_full_resolution": "1080p", "quality_profile": "Original",
                                           "bandwidth": "12000", "location": "lan"}]},
            "get_history": {"recordsFiltered": 1, "data": [{"id": 9, "friendly_name": "Dad", "user_id": 2, "media_type": "movie",
                                                           "title": "Dune", "year": 2024, "thumb": "/t", "started": 1700000000,
                                                           "play_duration": 2400, "percent_complete": 30, "watched_status": 0,
                                                           "transcode_decision": "transcode"}]},
            "get_home_stats": [{"stat_id": "top_movies", "rows": [{"title": "Dune", "total_plays": 4, "users_watched": 2}]},
                               {"stat_id": "top_users", "rows": [{"friendly_name": "Matt", "total_plays": 40}]}],
            "get_users": [{"user_id": 1, "friendly_name": "Matt"}],
        }[cmd]
        return 200, {}, json.dumps({"response": {"result": "success", "data": data}})
    responses.add_callback(responses.GET, "http://nas:8181/api/v2", callback=api)

    a = client.get("/api/activity", headers=H).get_json()
    s = a["streams"][0]
    assert a["count"] == 1 and s["title"] == "Nathan For You" and s["subtitle"] == "S02E05 · Smokers Allowed"
    assert s["decision"] == "Direct play" and s["quality"] == "1080p" and s["position"] == 600 and s["location"] == "LAN"
    assert s["poster"].startswith("/api/activity/img?img=%2Flibrary%2Fmetadata%2F1")
    hi = client.get("/api/activity/history?user=2", headers=H).get_json()
    assert hi["total"] == 1 and hi["items"][0]["decision"] == "Transcode" and hi["items"][0]["subtitle"] == "2024"
    st = client.get("/api/activity/stats?days=7", headers=H).get_json()
    assert st["movies"][0]["plays"] == 4 and st["users"][0]["name"] == "Matt" and st["shows"] == []
    assert client.get("/api/activity/users", headers=H).get_json()["users"] == [{"id": 1, "name": "Matt"}]


def test_tautulli_off(client):
    assert client.get("/api/config", headers=H).get_json()["activity"] is False
    assert client.get("/api/activity", headers=H).status_code == 404


@responses.activate
def test_trakt_discussion(client, monkeypatch):
    assert client.get("/api/config", headers=H).get_json()["discuss"] is False
    monkeypatch.setitem(gw.CFG, "trakt", {"client_id": "cid"})
    assert client.get("/api/config", headers=H).get_json()["discuss"] is True
    gw._TRAKT_IDS.clear()

    # A Radarr page gets a Discussion button pointing at its TMDB id.
    movies = [movie(2, tmdbId=603)]
    responses.get("http://nas:7878/api/v3/movie", json=movies)
    responses.get("http://nas:7878/api/v3/movie/2", json=movies[0])
    responses.get("http://nas:7878/api/v3/queue", json={"records": []})
    d = client.get("/api/svc/radarr/radarr/item/2", headers=H).get_json()
    disc = next(a for a in d["actions"] if a["id"] == "discuss")
    assert disc["opens"] == "discuss" and disc["arg"] == "movie-603"

    # Hub season pages: one per episode.
    monkeypatch.setattr(gw.HUB, "season", lambda item_id, n: {"show": {"id": "tv-95396"}, "season": {"number": n},
                                                             "episodes": [{"number": 1, "actions": []}, {"number": 2}]})
    s = client.get("/api/svc/hub/hub/season/tv-95396/2", headers=H).get_json()
    assert [a["arg"] for e in s["episodes"] for a in e["actions"] if a["id"] == "discuss"] == \
        ["tv-95396|s2e1", "tv-95396|s2e2"]

    def hdrs(req):
        assert req.headers["trakt-api-key"] == "cid" and req.headers["trakt-api-version"] == "2"
        return True
    responses.get("https://api.trakt.tv/search/tmdb/95396", match=[lambda r: (hdrs(r), "")],
                  json=[{"type": "show", "show": {"title": "Severance", "year": 2022, "ids": {"trakt": 1, "slug": "severance"}}}])
    responses.get("https://api.trakt.tv/shows/severance/seasons/2/episodes/1/comments/likes",
                  headers={"X-Pagination-Page-Count": "3", "X-Pagination-Item-Count": "61"},
                  json=[{"id": 5, "comment": "What a [spoiler]twist[/spoiler]!", "spoiler": False, "review": False,
                         "likes": 12, "replies": 2, "created_at": "2025-01-17T10:00:00Z",
                         "user_stats": {"rating": 9}, "user": {"username": "mark_s", "name": "Mark S"}}])
    responses.get("https://api.trakt.tv/shows/severance/seasons/2/episodes/1/ratings", json={"rating": 8.71, "votes": 900})
    responses.get("https://api.trakt.tv/shows/severance/seasons/2/episodes/1", json={"title": "Hello, Ms. Cobel"})
    r = client.get("/api/discuss?ref=" + quote("tv-95396|s2e1"), headers=H).get_json()
    assert r["title"] == "Severance · S02E01" and r["episodeTitle"] == "Hello, Ms. Cobel"
    assert r["rating"] == 87 and r["total"] == 61 and r["pages"] == 3
    assert r["url"] == "https://trakt.tv/shows/severance/seasons/2/episodes/1"
    c = r["comments"][0]
    assert c["spoiler"] is True and c["text"] == "What a twist!" and c["user"] == "mark_s" and c["rating"] == 9

    responses.get("https://api.trakt.tv/comments/5/replies", json=[{"id": 6, "comment": "Agreed", "user": {"username": "helly"}}])
    assert client.get("/api/discuss/replies/5", headers=H).get_json()["comments"][0]["user"] == "helly"


@responses.activate
def test_komga_comics(client, monkeypatch):
    assert client.get("/api/config", headers=H).get_json()["comics"] is False
    assert client.get("/api/comics/home", headers=H).status_code == 404
    monkeypatch.setitem(gw.CFG, "komga", {"url": "http://nas:25600", "api_key": "kk"})
    assert client.get("/api/config", headers=H).get_json()["comics"] is True
    K = "http://nas:25600/api/v1"

    def book(i, page=None, done=False, num="1"):
        return {"id": f"B{i}XXXXX", "seriesId": "S1XXXXX", "seriesTitle": "Saga", "name": f"Saga {num:0>3}",
                "metadata": {"title": f"Chapter {num}", "number": num, "releaseDate": "2012-03-14"},
                "media": {"pagesCount": 24, "status": "READY"}, "sizeBytes": 40_000_000,
                "readProgress": {"page": page, "completed": done} if page else None}
    series = {"id": "S1XXXXX", "name": "Saga", "booksCount": 3, "booksReadCount": 1, "booksUnreadCount": 1,
              "booksInProgressCount": 1, "metadata": {"title": "Saga", "publisher": "Image", "status": "ONGOING"},
              "booksMetadata": {"releaseDate": "2012-03-14", "summary": "Space opera."}}

    def check_key(req):
        return (req.headers.get("X-API-Key") == "kk", "api key")
    responses.get(f"{K}/books", match=[check_key], json={"content": [book(2, page=7, num="2")]})
    responses.get(f"{K}/books/ondeck", json={"content": [book(2, page=7, num="2"), book(3, num="3")]})
    responses.get(f"{K}/series/new", json={"content": [series]})
    responses.get(f"{K}/series/updated", json={"content": [series]})
    responses.get(f"{K}/libraries", json=[{"id": "L1XXXXX", "name": "Comics"}])
    h = client.get("/api/comics/home", headers=H).get_json()
    assert h["reading"][0]["page"] == 7 and h["reading"][0]["label"] == "#2"
    assert [b["id"] for b in h["deck"]] == ["B3XXXXX"]            # already in Continue reading
    assert h["new"][0]["publisher"] == "Image" and h["updated"] == [] and h["libraries"][0]["name"] == "Comics"

    responses.get(f"{K}/series", json={"content": [series], "number": 0, "totalPages": 1, "totalElements": 1})
    sl = client.get("/api/comics/series?search=sag", headers=H).get_json()
    assert sl["items"][0]["year"] == "2012" and sl["items"][0]["cover"] == "/api/comics/img/series/S1XXXXX"
    assert "search=sag" in responses.calls[-1].request.url

    responses.get(f"{K}/series/S1XXXXX", json=series)
    responses.get(f"{K}/series/S1XXXXX/books",
                  json={"content": [book(1, page=24, done=True), book(2, page=7, num="2"), book(3, num="3")]})
    sp = client.get("/api/comics/series/S1XXXXX", headers=H).get_json()
    assert sp["next"] == "B2XXXXX" and len(sp["books"]) == 3 and sp["books"][0]["done"] is True

    responses.get(f"{K}/books/B2XXXXX", json=book(2, page=7, num="2"))
    responses.get(f"{K}/books/B2XXXXX/pages", json=[{"number": 1, "width": 1988, "height": 3056},
                                                    {"number": 2, "width": 3976, "height": 3056}])
    responses.get(f"{K}/books/B2XXXXX/previous", json=book(1))
    responses.get(f"{K}/books/B2XXXXX/next", status=404)
    bp = client.get("/api/comics/book/B2XXXXX", headers=H).get_json()
    assert bp["pages"][1] == {"n": 2, "w": 3976, "h": 3056} and bp["prev"] == "B1XXXXX" and bp["next"] is None

    responses.patch(f"{K}/books/B2XXXXX/read-progress", status=204)
    assert client.post("/api/comics/book/B2XXXXX/progress", json={"page": 9}, headers=H).get_json() == {"ok": True}
    assert json.loads(responses.calls[-1].request.body) == {"page": 9, "completed": False}

    responses.get(f"{K}/books/B2XXXXX/pages/3", body=b"\xff\xd8jpeg", content_type="image/jpeg")
    r = client.get("/api/comics/page/B2XXXXX/3", headers=H)
    assert r.status_code == 200 and r.data == b"\xff\xd8jpeg" and r.mimetype == "image/jpeg"
    responses.get(f"{K}/books/B2XXXXX/thumbnail", body=b"png", content_type="image/png")
    assert client.get("/api/comics/img/book/B2XXXXX", headers=H).data == b"png"
    assert client.get("/api/comics/page/..%2Fx/1", headers=H).status_code in (400, 404)
