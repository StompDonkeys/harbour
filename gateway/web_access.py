"""
Harbour gateway — iPhone web app support: password login, in-app settings, and serving /m.

Kept in its own file so app.py's TV endpoints stay as they were. app.py imports
`install(app, ...)` once at start-up.

Access rules
  * Requests on the home network (Host is an IP / localhost / *.local) keep working with the
    gateway PIN or a paired device key, exactly as before — that's what the TV apps use.
  * Requests through the DSM reverse proxy (a domain-name Host, or forwarding headers) need a
    web-app login. PINs and device keys aren't accepted there unless config has "remote_token": true,
    and the setup page and pairing are home-network only.
  * The web app signs in with a password (stored hashed in config.json under "web") and gets a
    signed, HttpOnly cookie. Unsafe requests made with that cookie must also carry the header
    X-Harbour-Web: 1, which other sites can't add (a cheap CSRF guard on top of SameSite).
  * Failed logins / setup attempts are rate limited.
"""
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time

from flask import jsonify, redirect, request, send_from_directory
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
COOKIE = "harbour_s"
COOKIE_DAYS = 180
FAIL_WINDOW, FAIL_LIMIT = 15 * 60, 5   # 5 wrong passwords in 15 min → locked for 15 min

# Settings the web app can edit. Secrets are never sent back to the phone, only "set or not".
SCHEMA = {
    "general": {"title": "General", "single": True, "fields": [
        {"key": "name", "label": "Name", "type": "text", "placeholder": "Harbour"},
        {"key": "region", "label": "Region", "type": "text", "placeholder": "AU",
         "help": "Two-letter country code for release dates and streaming info"},
        {"key": "token", "label": "Gateway PIN", "type": "secret",
         "help": "Logs in to the setup page; paired TVs keep working if you change it"},
    ]},
    "radarr": {"title": "Radarr", "multi": True, "test": True, "fields": [
        {"key": "name", "label": "Name", "type": "text", "placeholder": "Radarr"},
        {"key": "url", "label": "Address", "type": "url", "required": True, "placeholder": "http://192.168.4.68:7878"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True,
         "help": "Radarr → Settings → General → API Key"},
        {"key": "icon", "label": "Icon", "type": "choice", "options": ["", "4k"], "labels": ["Standard", "4K"]},
    ]},
    "sonarr": {"title": "Sonarr", "multi": True, "test": True, "fields": [
        {"key": "name", "label": "Name", "type": "text", "placeholder": "Sonarr"},
        {"key": "url", "label": "Address", "type": "url", "required": True, "placeholder": "http://192.168.4.68:8989"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True,
         "help": "Sonarr → Settings → General → API Key"},
    ]},
    "seerr": {"title": "Jellyseerr", "multi": True, "max": 1, "test": True, "fields": [
        {"key": "name", "label": "Name", "type": "text", "placeholder": "Jellyseerr"},
        {"key": "url", "label": "Address", "type": "url", "required": True, "placeholder": "http://192.168.4.68:5055"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True,
         "help": "Jellyseerr → Settings → General → API Key"},
    ]},
    "prowlarr": {"title": "Prowlarr", "single": True, "test": True, "optional": True, "fields": [
        {"key": "url", "label": "Address", "type": "url", "required": True, "placeholder": "http://192.168.4.68:9696"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True,
         "help": "Prowlarr → Settings → General → API Key"},
    ]},
    "newznab": {"title": "Newznab indexer", "multi": True, "test": True, "fields": [
        {"key": "name", "label": "Name", "type": "text", "placeholder": "NZBgeek"},
        {"key": "url", "label": "Address", "type": "url", "required": True, "placeholder": "https://api.nzbgeek.info"},
        {"key": "api_key", "label": "API key", "type": "secret"},
    ]},
    "nzbget": {"title": "NZBGet", "multi": True, "test": True, "fields": [
        {"key": "name", "label": "Name", "type": "text", "placeholder": "NZBGet"},
        {"key": "url", "label": "Address", "type": "url", "required": True, "placeholder": "http://192.168.4.68:6789"},
        {"key": "username", "label": "Username", "type": "text"},
        {"key": "password", "label": "Password", "type": "secret"},
    ]},
    "qbittorrent": {"title": "qBittorrent", "multi": True, "test": True, "fields": [
        {"key": "name", "label": "Name", "type": "text", "placeholder": "qBittorrent"},
        {"key": "url", "label": "Address", "type": "url", "required": True, "placeholder": "http://192.168.4.68:8080"},
        {"key": "api_key", "label": "API key", "type": "secret", "help": "qBittorrent 5.2+; or use username/password"},
        {"key": "username", "label": "Username", "type": "text"},
        {"key": "password", "label": "Password", "type": "secret"},
    ]},
    "filestation": {"title": "File Station", "single": True, "test": True, "optional": True, "fields": [
        {"key": "url", "label": "DSM address", "type": "url", "required": True, "placeholder": "http://192.168.4.68:5000"},
        {"key": "username", "label": "DSM username", "type": "text", "required": True, "placeholder": "dockerlimited"},
        {"key": "password", "label": "DSM password", "type": "secret", "required": True},
        {"key": "allowed_shares", "label": "Shared folders", "type": "list", "placeholder": "/data",
         "help": "Comma-separated; Harbour can only see and change these"},
        {"key": "verify_ssl", "label": "Check HTTPS certificate", "type": "bool", "default": True},
    ]},
    "plex": {"title": "Plex", "single": True, "test": True, "optional": True, "fields": [
        {"key": "url", "label": "Address", "type": "url", "required": True, "placeholder": "http://192.168.4.68:32400"},
        {"key": "token", "label": "Plex token", "type": "secret", "required": True},
    ]},
    "tmdb": {"title": "TMDB", "single": True, "optional": True, "fields": [
        {"key": "api_key", "label": "Read access token", "type": "secret", "help": "Optional — extra artwork and info"},
    ]},
}
ORDER = ["general", "radarr", "sonarr", "seerr", "nzbget", "qbittorrent", "newznab", "filestation", "plex", "prowlarr", "tmdb"]
SECRET_KEYS = {s: {f["key"] for f in v["fields"] if f["type"] == "secret"} for s, v in SCHEMA.items()}


def install(app, *, get_cfg, config_path, valid_token, save_config, test_service, ApiError, FileStation):
    """Wire the web-app routes and the access check into the gateway's Flask app."""
    fails = {}          # ip -> [failure times]
    fails_lock = threading.Lock()
    save_lock = threading.Lock()

    # ------------------------------------------------------------ helpers

    def web():
        return get_cfg().get("web") or {}

    def client_ip():
        """The caller's address as the reverse proxy saw it. X-Real-IP and the *last* X-Forwarded-For
        entry are set by the proxy itself; earlier entries can be faked by the caller."""
        real = (request.headers.get("X-Real-IP") or "").strip()
        fwd = [x.strip() for x in (request.headers.get("X-Forwarded-For") or "").split(",") if x.strip()]
        return real or (fwd[-1] if fwd else "") or request.remote_addr or "?"

    def is_remote():
        """True when the request came in through the reverse proxy rather than straight over the LAN."""
        if request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP"):
            return True
        host = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
        if host in ("localhost", "") or host.endswith((".local", ".lan", ".home", ".internal")):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            return True  # a domain name, e.g. harbour.plexstation.synology.me

    def is_https():
        return (request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
                or is_remote())

    def signer():
        sec = web().get("secret")
        return URLSafeTimedSerializer(sec, salt="harbour-web") if sec else None

    def session_ok():
        s, raw = signer(), request.cookies.get(COOKIE)
        if not s or not raw:
            return False
        try:
            data = s.loads(raw, max_age=COOKIE_DAYS * 86400)
        except BadSignature:
            return False
        return isinstance(data, dict) and data.get("g") == web().get("gen", 0)

    def set_session(resp):
        resp.set_cookie(COOKIE, signer().dumps({"g": web().get("gen", 0)}), max_age=COOKIE_DAYS * 86400,
                        httponly=True, samesite="Strict", secure=is_https(), path="/")
        return resp

    def locked_out():
        """Per address: 5 misses in 15 min. Overall: 30 misses in 15 min from anywhere locks sign-in for
        everyone until things calm down (stops guessing spread across many addresses)."""
        now, ip = time.time(), client_ip()
        with fails_lock:
            for k in list(fails):
                fails[k] = [t for t in fails[k] if now - t < FAIL_WINDOW]
                if not fails[k]:
                    del fails[k]
            total = sum(len(v) for v in fails.values())
            return len(fails.get(ip, [])) >= FAIL_LIMIT or total >= FAIL_LIMIT * 6

    def note_fail():
        with fails_lock:
            fails.setdefault(client_ip(), []).append(time.time())
        time.sleep(0.5)  # slows guessing a little more

    def clear_fails():
        with fails_lock:
            fails.pop(client_ip(), None)

    def current_file():
        if not os.path.exists(config_path):
            return {}
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)

    def save(mutate):
        """Load the file, apply `mutate`, save + reload through the gateway; roll back if the reload fails."""
        with save_lock:
            old = current_file()
            new = json.loads(json.dumps(old))
            mutate(new)
            try:
                save_config(new)
            except ApiError:
                raise                           # couldn't write — nothing changed
            except Exception as e:
                save_config(old)
                raise ApiError(f"Those settings didn't load ({type(e).__name__}: {e}) — nothing was changed")
            return new

    # ------------------------------------------------------------ access check

    WEB_PUBLIC = {"/api/login", "/api/login/status", "/api/login/setup",
                  "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/favicon.ico"}
    REMOTE_OK = {"/", "/favicon.ico"}   # of the gateway's other public paths, what's fine from outside

    def is_web_public(p):
        return p in WEB_PUBLIC or p == "/m" or p.startswith("/m/")

    def remote_guard(p):
        """From outside, the setup page, pairing and APK downloads need a web-app sign-in."""
        if not is_remote() or p in REMOTE_OK or session_ok():
            return None
        return jsonify(error="That's only available on your home network", login=True), 403

    def check_access():
        p = request.path.lower()
        if is_web_public(p):
            return None
        if session_ok():
            if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Harbour-Web") != "1":
                return jsonify(error="Missing X-Harbour-Web header"), 403
            return None
        cfg = get_cfg()
        given = request.headers.get("X-Token") or request.args.get("t") or ""
        if is_remote() and not cfg.get("remote_token"):
            return jsonify(error="Sign in to Harbour", login=True), 401
        if not cfg.get("token"):
            return jsonify(error="The gateway isn't set up yet — open http://<your NAS>:8765/setup"), 503
        if not valid_token(given):
            if is_remote():
                if locked_out():
                    return jsonify(error="Too many attempts — try again in 15 minutes"), 429
                note_fail()
            return jsonify(error="Wrong access token — pair this device again from Harbour's settings",
                           login=bool(web().get("password"))), 401
        return None

    # ------------------------------------------------------------ login routes

    @app.get("/api/login/status")
    def login_status():
        return jsonify(signed_in=session_ok(), setup_needed=not web().get("password"),
                       name=get_cfg().get("name", "Harbour"), remote=is_remote())

    @app.post("/api/login/setup")
    def login_setup():
        """First run: prove you're the owner with the TV access token, then pick a password."""
        if web().get("password"):
            raise ApiError("A password is already set — sign in instead", 409)
        if locked_out():
            return jsonify(error="Too many attempts — try again in 15 minutes"), 429
        b = request.get_json(silent=True) or {}
        token = get_cfg().get("token") or ""
        pw = b.get("password") or ""
        if not token:
            raise ApiError("Set up the gateway first: open http://<your NAS>:8765/setup on your home network", 409)
        if not hmac.compare_digest(token, str(b.get("token") or "").strip()):
            note_fail()
            raise ApiError("That isn't the gateway PIN — it's the PIN you chose on the setup page", 401)
        if len(pw) < 8:
            raise ApiError("Use at least 8 characters for the password")

        def mutate(c):
            w = c.setdefault("web", {})
            w["password"] = generate_password_hash(pw)
            w["secret"] = w.get("secret") or secrets.token_urlsafe(32)
            w["gen"] = int(w.get("gen", 0)) + 1
        save(mutate)
        clear_fails()
        return set_session(jsonify(ok=True))

    @app.post("/api/login")
    def login():
        if locked_out():
            return jsonify(error="Too many attempts — try again in 15 minutes"), 429
        h = web().get("password")
        if not h:
            raise ApiError("No password set yet", 409)
        pw = (request.get_json(silent=True) or {}).get("password") or ""
        if not check_password_hash(h, pw):
            note_fail()
            raise ApiError("Wrong password", 401)
        clear_fails()
        return set_session(jsonify(ok=True))

    @app.post("/api/logout")
    def logout():
        r = jsonify(ok=True)
        r.delete_cookie(COOKIE, path="/")
        return r

    @app.post("/api/login/password")
    def change_password():
        if not session_ok():
            raise ApiError("Sign in first", 401)
        b = request.get_json(silent=True) or {}
        if not check_password_hash(web().get("password") or "", b.get("current") or ""):
            note_fail()
            raise ApiError("Current password is wrong", 401)
        if len(b.get("new") or "") < 8:
            raise ApiError("Use at least 8 characters for the password")

        def mutate(c):
            w = c.setdefault("web", {})
            w["password"] = generate_password_hash(b["new"])
            w["gen"] = int(w.get("gen", 0)) + 1  # signs out every other device
        save(mutate)
        return set_session(jsonify(ok=True, message="Password changed — other devices are signed out"))

    # ------------------------------------------------------------ settings

    def as_list(v):
        return v if isinstance(v, list) else [v] if isinstance(v, dict) and v else []

    def masked(section, entry):
        out, have = {}, []
        for f in SCHEMA[section]["fields"]:
            k = f["key"]
            v = entry.get(k)
            if f["type"] == "secret":
                if v:
                    have.append(k)
                out[k] = ""
            elif f["type"] == "list":
                out[k] = ", ".join(v) if isinstance(v, list) else (v or "")
            elif f["type"] == "bool":
                out[k] = f.get("default", False) if v is None else bool(v)
            else:
                out[k] = "" if v is None else v
        if SCHEMA[section].get("multi"):
            out["id"] = entry.get("id") or section   # same default ids the gateway itself uses
        out["_set"] = have
        return out

    @app.get("/api/settings")
    def settings_get():
        c = current_file()
        values = {}
        for s in ORDER:
            if s == "general":
                values[s] = masked(s, c)
            elif SCHEMA[s].get("multi"):
                values[s] = [masked(s, e) for e in as_list(c.get(s))]
            else:
                values[s] = masked(s, c[s]) if isinstance(c.get(s), dict) and c.get(s) else None
        writable = os.access(config_path, os.W_OK)
        return jsonify(schema=SCHEMA, order=ORDER, values=values, writable=writable,
                       remote_token=bool(c.get("remote_token")))

    def slug(name, taken):
        base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "service"
        s, n = base, 2
        while s in taken:
            s, n = f"{base}-{n}", n + 1
        return s

    def apply_entry(section, existing, edit):
        """Merge one edited entry onto what's in the file, keeping unknown keys and unchanged secrets."""
        e = dict(existing or {})
        for f in SCHEMA[section]["fields"]:
            k = f["key"]
            if k not in edit:
                continue
            v = edit[k]
            t = f["type"]
            if t == "secret":
                if v is None:
                    e.pop(k, None)          # explicit clear
                elif str(v).strip():
                    e[k] = str(v).strip()   # replaced; "" means keep
                continue
            if t == "list":
                items = v if isinstance(v, list) else str(v or "").split(",")
                items = ["/" + x.strip().strip("/") for x in items if x.strip().strip("/")]
                if items:
                    e[k] = items
                else:
                    e.pop(k, None)
                continue
            if t == "bool":
                e[k] = bool(v)
                continue
            v = str(v or "").strip()
            if t == "url":
                v = v.rstrip("/")
                if v and not re.match(r"^https?://", v, re.I):
                    v = "http://" + v
            if v:
                e[k] = v
            else:
                e.pop(k, None)
        label = e.get("name") or SCHEMA[section]["title"]
        for f in SCHEMA[section]["fields"]:
            if f.get("required") and not e.get(f["key"]):
                raise ApiError(f"{SCHEMA[section]['title']} – {label}: {f['label']} is needed")
        if e.get("url") and not re.match(r"^https?://[^/\s]+", e["url"], re.I):
            raise ApiError(f"{label}: that address doesn't look right")
        return e

    def merged_section(section, file_cfg, edits):
        """New value for a section, built from the edits with secrets/extra keys carried over by id."""
        if section == "general":
            return apply_entry(section, file_cfg, edits or {})
        if SCHEMA[section].get("multi"):
            old = {e.get("id") or section: e for e in as_list(file_cfg.get(section))}
            out, taken = [], set()
            for ed in edits or []:
                eid = ed.get("id")
                base = old.get(eid) if eid else None
                e = apply_entry(section, base, ed)
                e["id"] = eid if (eid and base is not None) else slug(e.get("name") or section, taken | set(old))
                if e["id"] in taken:
                    raise ApiError(f"Two {SCHEMA[section]['title']} entries have the same id")
                taken.add(e["id"])
                out.append(e)
            mx = SCHEMA[section].get("max")
            if mx and len(out) > mx:
                raise ApiError(f"Only {mx} {SCHEMA[section]['title']} is supported")
            return out
        if edits is None:
            return None
        return apply_entry(section, file_cfg.get(section) if isinstance(file_cfg.get(section), dict) else None, edits)

    @app.put("/api/settings")
    def settings_put():
        b = request.get_json(silent=True) or {}
        vals = b.get("values") or {}

        def mutate(c):
            for s in ORDER:
                if s not in vals:
                    continue
                v = merged_section(s, c, vals[s])
                if s == "general":
                    for k in ("name", "region", "token"):
                        if k in v:
                            c[k] = v[k]
                        else:
                            c.pop(k, None)
                elif SCHEMA[s].get("multi"):
                    if not v:
                        c.pop(s, None)
                    elif s in ("seerr", "nzbget", "qbittorrent") and len(v) == 1 and not isinstance(c.get(s), list):
                        c[s] = v[0]   # keep the original one-object shape
                    else:
                        c[s] = v
                elif v is None:
                    c.pop(s, None)
                else:
                    c[s] = v
            if "remote_token" in b:
                c["remote_token"] = bool(b["remote_token"])
        save(mutate)
        return jsonify(ok=True, message="Saved")

    def test_conf(section, edit):
        """The entry as it would be saved (secrets filled in from the file) — without saving it."""
        c = current_file()
        if SCHEMA[section].get("multi"):
            old = {e.get("id") or section: e for e in as_list(c.get(section))}
            base = old.get(edit.get("id")) if edit.get("id") else None
        else:
            base = c.get(section) if isinstance(c.get(section), dict) else None
        return apply_entry(section, base, edit)

    @app.post("/api/settings/test")
    def settings_test():
        b = request.get_json(silent=True) or {}
        section = b.get("section")
        if section not in SCHEMA or not SCHEMA[section].get("test"):
            raise ApiError("Unknown service")
        try:
            r = test_service(section, test_conf(section, b.get("entry") or {}))
            ok, msg = bool(r.get("ok")), r.get("message") or ""
        except ApiError as e:
            ok, msg = False, e.message
        return jsonify(ok=ok, message=msg)

    # ------------------------------------------------------------ the web app itself

    @app.get("/m")
    def web_root_redirect():
        return redirect("/m/", 302)

    @app.get("/m/")
    def web_index():
        r = send_from_directory(WEB_DIR, "index.html")
        r.headers["Cache-Control"] = "no-cache"
        return r

    @app.get("/m/<path:name>")
    def web_file(name):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            return jsonify(error="Not found"), 404
        r = send_from_directory(WEB_DIR, name)
        if name.endswith(".webmanifest"):
            r.mimetype = "application/manifest+json"
        r.headers["Cache-Control"] = "no-cache" if name.endswith((".html", ".webmanifest")) else "public, max-age=86400"
        return r

    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    def touch_icon():
        return send_from_directory(WEB_DIR, "apple-touch-icon.png")

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(WEB_DIR, "favicon.ico")

    return {"check_access": check_access, "remote_guard": remote_guard, "is_remote": is_remote}


__all__ = ["install", "SCHEMA"]
