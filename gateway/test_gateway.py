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
