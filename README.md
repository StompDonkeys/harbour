# Harbour V3

A Fire TV app for your Synology *arr stack — Radarr, Sonarr, Jellyseerr, Plex, Prowlarr, NZBGet, qBittorrent and File Station — designed for the remote, not a mouse.

```
 Fire TV / phone                      Synology NAS
┌──────────────────┐   LAN    ┌───────────────────────────────────┐
│ Harbour V3       │ ───────▶ │ harbour-gateway  :8765            │
│ (just a shell)   │ device   │  holds every address & API key    │
└──────────────────┘  key     │  /setup  ← set everything up here │
                              │   ├─▶ Radarr (+ 4K)  ├─▶ Plex      │
                              │   ├─▶ Sonarr         ├─▶ Prowlarr  │
                              │   ├─▶ Jellyseerr     ├─▶ NZBGet/qBit│
                              │   └─▶ File Station (DSM API)      │
                              └───────────────────────────────────┘
```

The apps only know the gateway's address and their own device key. Everything else lives in the gateway, which you set up in a browser.

## Install

**1. Gateway.** In Synology **Container Manager → Project → Create**: name it `harbour-gateway`, pick a folder (e.g. `/volume1/docker/harbour-gateway`), choose *Create docker-compose.yml* and paste:

```yaml
services:
  harbour-gateway:
    image: ghcr.io/stompdonkeys/harbour-gateway:latest
    container_name: harbour-gateway
    user: "1035:100"            # your Docker user; change if yours differs
    restart: unless-stopped
    ports:
      - "8765:8765"
    volumes:
      - ./config:/config            # settings saved from the setup page
      - /volume1/docker:/stack:ro   # optional: lets setup find your apps' API keys
    environment:
      - TZ=Australia/Sydney
```

Make sure your Docker user has **Read/Write** on that folder (File Station → folder → Properties → Permission).

**2. Setup page.** Open `http://<your NAS>:8765/setup` on your phone or computer. Choose a PIN, press **Find my apps** (fills in addresses and API keys from your apps' own settings files), test, **Save**.

**3. TV.** Install *Downloader* from the Amazon app store and enter `http://<your NAS>:8765/v3`. Open Harbour V3 — it finds the gateway and shows a 4-digit code. Approve that code on the setup page. Done.

## Update

- **Gateway:** Container Manager → **Image** → `ghcr.io/stompdonkeys/harbour-gateway` → **Update**. Settings are kept. The setup page and the TV tell you when one's available.
- **TV app:** Harbour → **Settings → Install**.

## What's in it

| Menu | What you get |
|---|---|
| **Home** | Today's and this week's episodes and movies, library stats, Ready to watch, Coming up, New movies, Trending. |
| **Search / Discover** | Find any movie or show (TMDB via Jellyseerr); trending, popular, upcoming. |
| **Movies / TV** | Your Radarr and Sonarr libraries merged. One page per title: ratings, trailers (played in Harbour), cast, where it streams, what you own in HD/4K, play in Plex, auto/manual search, monitor, delete, request. Seasons → episodes with per-episode actions. |
| **Calendar** | Month view of releases; click an episode to manage it. |
| **Indexers** | Search Prowlarr and your Newznab indexers directly; send to NZBGet or qBittorrent. |
| **Downloads** | NZBGet + qBittorrent queues: pause, resume, delete. |
| **Files** | Browse, move, copy, rename and delete on the NAS. |
| **Settings** | Pairing, Plex profile, updates, demo screen recording (saved to the NAS). |

## Folders

- `gateway/` — the gateway (Flask, `app.py` + `setup.html`), built into the image by GitHub Actions on every change.
- `android-v3/` — Harbour V3 (Kotlin / Compose for TV). Same app id as v2, so it installs over it.
- `android/` — the original Harbour (v1), kept as is.

## Developing

```bash
cd gateway && pip install flask requests pytest responses && pytest -q
HARBOUR_CONFIG=./config/config.json python app.py   # then open http://localhost:8765/setup
```

Most new services are a Python class in `app.py` plus a section on the setup page — screens are driven by the gateway's JSON.

## Roadmap

Docker manager: pick a compose file on the NAS, install it, watch status and logs, open its web UI.
