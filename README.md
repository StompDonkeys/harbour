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

- **Gateway:** Container Manager → **Project** → harbour-gateway → **Action → Stop**, then **Action → Clean**. Then **Image** → delete `ghcr.io/stompdonkeys/harbour-gateway`, and back in **Project** → **Action → Build** (downloads the newest). Settings are kept. The setup page and the TV tell you when one's available.
- **TV app:** Harbour → **Settings → Install**.

## What's in it

| Menu | What you get |
|---|---|
| **Home** | Today's and this week's episodes and movies, library stats, Ready to watch, Coming up, New movies, Trending. |
| **Search** | Pick **Movies**, **TV** (TMDB via Jellyseerr) or **Indexers** first, then type. Indexers searches (Prowlarr and your Newznab indexers directly; send a release to NZBGet or qBittorrent). |
| **Discover** | Trending, popular, upcoming. |
| **Movies / TV** | Your Radarr and Sonarr libraries merged. One page per title: ratings, trailers (played in Harbour), cast, where it streams, what you own in HD/4K, play in Plex, auto/manual search, monitor, delete, request. Seasons → episodes with per-episode actions. |
| **Calendar** | Month view of releases; click an episode to manage it. |
| **Downloads** | NZBGet + qBittorrent queues: pause, resume, delete. |
| **Files** | Browse, move, copy, rename and delete on the NAS. Press OK on a video or audio file to play it on the TV. |
| **Settings** | Pairing, Plex profile, updates, demo screen recording (saved to the NAS). |

## iPhone app

The gateway also serves an iPhone app at `/m/`: Library (Radarr, Radarr 4K, Sonarr), Discover (Jellyseerr requests), Calendar, Downloads, Files and Settings, with manual search, per-episode actions and trailers.

**X / Twitter videos:** Downloads → *X / Twitter video*. Paste a post link (e.g. `https://x.com/user/status/123…?s=46`), tap **Get sizes**, pick one, and the gateway downloads it and saves it to `/data/media/twitter` on the NAS (`/volume1/data/media/twitter`) through File Station, named `handle date postid 720p.mp4`. It needs File Station set up, and the File Station user needs write access there. Change the folder with `"twitter": {"folder": "/data/media/twitter"}` in the gateway config. For adult or sign-in-only posts, open **Settings → X sign-in** and paste the `auth_token` and `ct0` cookies from a signed-in x.com (the screen shows where to find them in Safari). **Settings** edits every service's address and API key (with **Test connection**), and saving takes effect straight away for the TVs too. Nothing to install from the App Store.

1. **At home**, open `http://<your NAS>:8765/` in Safari (it opens the app once the gateway is set up). Enter your **gateway PIN** once, then choose a password (8+ characters). Every phone uses that password from then on.
2. **Add to Home Screen:** Safari → Share → *Add to Home Screen*. It opens full screen with the Harbour icon.
3. **Away from home (optional):** DSM → Control Panel → Login Portal → Advanced → **Reverse Proxy** → Create:
   - Source: `HTTPS`, hostname e.g. `harbour.plexstation.synology.me`, port `443`
   - Destination: `HTTP`, `localhost`, port `8765`
   - Security → Certificate: make sure it covers that hostname and assign it to this entry.
   - **Check it:** `https://harbour.plexstation.synology.me/api/login/status` must say `"remote":true`. If it says `false`, add a custom header on the proxy entry: `X-Forwarded-For` = `$proxy_add_x_forwarded_for`.
   Then add *that* address to your Home Screen instead; it works at home and away.

**What's allowed from outside.** Through the reverse proxy only the iPhone app's password works. The gateway PIN and paired-TV keys are refused, and the setup page and TV pairing are home-network only, so the TVs never need to be reachable from the internet. Never forward port 8765 on your router directly.

**Security notes.** The password is stored hashed in `config.json` (`"web"`). Sign-ins last 180 days per phone; *Settings → Change password* signs out every other phone. Five wrong passwords in 15 minutes locks sign-in for 15 minutes. API keys and passwords are never sent to the phone; Settings only shows whether each one is set.

**Bigger screens and the iPhone Duo.** On a normal phone, or the Duo's cover screen, every page fills the screen. Open the Duo (or turn a phone sideways, or use an iPad) and Library, Discover, Calendar, Files and Settings go side by side: the list on the left half, what you opened on the right, split at the fold. Folding and unfolding keeps your place. If Safari reports the hinge position (Viewport Segments API), the split follows it exactly.

## Folders

- `gateway/` — the gateway (Flask, `app.py` + `setup.html`, plus the iPhone app: `web_access.py` + `web/`), built into the image by GitHub Actions on every change.
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
