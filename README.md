# Harbour

A Fire TV app for your Synology stack — Radarr, Sonarr, Seerr and File Station — designed for the remote, not a mouse.

```
 Fire TV Stick 4K Max                 Synology (192.168.4.68)
┌────────────────────┐   LAN    ┌──────────────────────────────┐
│ Harbour.apk        │ ───────▶ │ harbour-gateway  :8765       │
│ (Kotlin / Compose) │  token   │  holds every API key/password│
└────────────────────┘          │   ├─▶ Radarr  (+ radarr-truehd)│
                                │   ├─▶ Sonarr                  │
                                │   ├─▶ Seerr / Jellyseerr      │
                                │   └─▶ File Station (DSM API)  │
                                └──────────────────────────────┘
```

The stick only knows the gateway's address and one token. The gateway is a single-file Flask app; it's also where the Docker manager (phase 2) will live.

## What's in it

| Menu item | What you get |
|---|---|
| **Radarr / Radarr 4K** (one per Radarr) | Netflix-style home: focused title's fanart fills the top, rows for Downloading, Recently added, Coming soon, Missing, Top rated and your top genres. Detail page with quality, size, video/audio, **Auto search**, **Manual search** (pick a release yourself), Monitor, Remove, Delete + files. A–Z grid with filter. |
| **Sonarr** | Same layout. Detail page with season chips; per season: auto search, manual search, monitor. Press OK on an episode for its menu: auto search, manual search, monitor, delete file. |
| **Jellyseerr** | Trending / Popular / Upcoming, search, Request (you pick the root folder), Request in 4K only when Seerr has a 4K server for that type, per-season requests. |
| **Downloads** | NZBGet + qBittorrent live: speed, ETA, size, status, queue. OK on an item: pause/resume, move to top (NZBGet), delete, delete + files (qBittorrent). Pause/resume a whole client. |
| **Files** | Browse shared folders; ☰ Menu on any item for Move to…, Copy to…, Rename, Delete; New folder. |
| **Settings** | Gateway address + token, and **in-app updates** from GitHub releases. |

Menu names come from the `name` of each service in `config.json`.

**Remote:** D-pad moves, OK selects, Back goes up/back (and from a tab's first screen, jumps to the menu), ☰ opens options in Files.

---

## 1. Set up the gateway on the Synology

1. **API keys** — Radarr / Sonarr: *Settings → General → API Key*. Seerr: *Settings → General → API Key*.
2. **DSM user for File Station** — use your existing `dockerlimited` account (UID 1035 / GID 100), so Harbour sees exactly the shares your containers already use. Check in Control Panel → User → `dockerlimited`:
   - it has a password set (File Station's API logs in with username + password),
   - **Applications** tab: *File Station* is allowed (service accounts often have every app denied),
   - **Permissions** tab: Read/Write on the shares you want on the TV,
   - 2-step verification is off for this account.
   The gateway container also runs as `1035:100`, so make sure `dockerlimited` can read the `harbour-gateway/config` folder.
3. Copy the `gateway/` folder to `/volume1/docker/harbour-gateway/`.
4. Create `/volume1/docker/harbour-gateway/config/config.json` from `config.example.json`:
   - set `token` — anything you like; a 6–8 digit PIN is easy to type on the remote and fine on a home network,
   - fill in URLs and keys (fix the ports — I guessed `7879` for `radarr-truehd`),
   - `allowed_shares` limits what the TV can touch; delete the key to allow every share the user can see,
   - `four_k: true` shows *Request in 4K* (needs a 4K Radarr server set up in Seerr).
5. **Start the gateway.** In DSM open Container Manager → **Project** → **Create**:
   - Project name: `harbour-gateway`
   - Path: `/volume1/docker/harbour-gateway` (the folder from step 3)
   - Source: *Use existing docker-compose.yml* — Container Manager finds it in that folder.
   - Click through and **Done**. It builds the image (a minute or two the first time) and starts the container. The project should show as *Running*.
6. **Test that it works** before going near the Fire Stick, so any problem shows up here rather than as a vague error on the TV. On any computer on your home network, open this in a web browser (put your token on the end):
   ```
   http://192.168.4.68:8765/api/config?t=YOUR_TOKEN
   ```
   - **Working:** a short block of text listing your services, e.g. `{"name":"Harbour","services":[{"name":"Movies",…},{"name":"TV Shows",…}],"files":true}`.
   - **"Wrong access token":** the token in the address doesn't match `config.json`.
   - **Page won't load:** the container isn't running. In Container Manager open the project → **Log** tab for the reason (usually a typo in `config.json`).

   That page only checks the gateway started and read your config. To check a service's API key, swap the address for one service's page, e.g. `http://192.168.4.68:8765/api/svc/radarr/radarr/home?t=YOUR_TOKEN`. A long block of movie data means the key works; an error message names the service that failed.

**Changing settings later.** The gateway reads `config.json` once, when it starts. If you change anything in it afterwards — a new password, an API key, adding a service — restart the project in Container Manager (**Action → Restart**) so it picks up the change. No rebuild needed.

**Keep it on your home network.** Don't add it to the DSM reverse proxy or forward port 8765 on your eero. Anyone who can reach it with the token can delete files on the NAS.

## 2. Build the APK with GitHub Actions

1. Create a GitHub repo (e.g. `harbour`) and push this whole folder to `main`.
2. **Signing key (needed for in-app updates).** Every build must be signed with the same key or Android refuses to update. Repo → Settings → Secrets and variables → Actions → **New repository secret**, add the two values from `harbour-signing-secrets.txt`:
   `HARBOUR_KEYSTORE_B64` and `HARBOUR_KEYSTORE_PASSWORD`. Keep that file (it *is* the key) somewhere safe, not in the repo.
3. Every push to `android/**` builds the APK, attaches it to the build run, and publishes a GitHub Release with `Harbour.apk`.

Without the secrets the build still works but is debug-signed with a throwaway key — you'd have to uninstall before each update.

## 3. Install on the Fire TV Stick 4K Max

**First install** (once):
1. Fire TV → Settings → My Fire TV → Developer options → *Install unknown apps* → turn on for **Downloader** (if Developer options is hidden: Settings → My Fire TV → About → click the device name 7 times).
2. In Downloader, enter the short link served by your gateway: `192.168.4.68:8765/apk`
   (it redirects to the newest `Harbour.apk` on GitHub).
3. Open Harbour → enter `http://192.168.4.68:8765` and your token → **Test & save**.

**Updates:** Harbour checks GitHub at launch. When a newer build exists you get a message and a dot on Settings; go to **Settings → Install**. The first time, Fire OS asks you to allow Harbour to install apps (Developer options → Install unknown apps → Harbour → On). Your settings are kept.

**Crashes:** if Harbour closes unexpectedly, the next launch sends the error to the gateway, so it appears in Container Manager → harbour-gateway → Log as `APP REPORT`.

## Developing

Gateway tests (mocked Radarr/Sonarr/Seerr/File Station):
```bash
cd gateway && pip install flask requests pytest responses && pytest -q
```
Run the gateway on your Mac: `HARBOUR_CONFIG=./config/config.json python app.py`.

The app opens fine in Android Studio (`android/` folder) — use the *Television 1080p* emulator to try it with a D-pad.

### Adding a service later
Every screen is driven by the gateway's JSON (`home` rows, `detail` facts/actions/seasons, `action` results), so most new services (Lidarr, qBittorrent, Docker…) are a new Python class in `app.py` plus an entry in `config.json` — no app rebuild needed unless you want a new kind of screen.

## Roadmap — phase 2: Docker manager
Gateway gets read/write access to the Docker socket and the compose folders; the app gets a **Containers** tab: list projects with status, pick a `docker-compose.yml` from the NAS, review it, *Install*, watch logs, and open the app's web UI to test it.
