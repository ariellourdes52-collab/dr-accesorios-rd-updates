from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import requests
import jwt
from jwt import PyJWKClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Flask, jsonify, request
from pywebpush import WebPushException, webpush

app = Flask(__name__)

PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = Path(os.environ.get("DB_PATH", "/data/webpush.db"))
RADAR_URL = os.environ.get("RADAR_URL", "https://dr-accesorios-rd.web.app/radar.json")
SEISMIC_URL = os.environ.get("SEISMIC_URL", "https://dr-accesorios-rd.web.app/radar-seismic-v231.json")
BLOG_FEED_URL = os.environ.get(
    "BLOG_FEED_URL",
    "https://draccesoriosrd.blogspot.com/feeds/posts/default?alt=json&max-results=10",
)
POLL_SECONDS = max(15, int(os.environ.get("POLL_SECONDS", "30")))
VAPID_PUBLIC_KEY = os.environ["VAPID_PUBLIC_KEY"].strip()
VAPID_PRIVATE_KEY_B64 = os.environ["VAPID_PRIVATE_KEY"].strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "https://draccesoriosrd.blogspot.com/").strip()
FIREBASE_PROJECT_ID = "dr-accesorios-rd"
FIREBASE_JWKS_URL = "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
GITHUB_OIDC_JWKS_URL = "https://token.actions.githubusercontent.com/.well-known/jwks"
GITHUB_OIDC_AUDIENCE = "dr-radar-webpush-social"
GITHUB_REPOSITORY = "ariellourdes52-collab/dr-accesorios-rd-updates"
ALLOWED_ORIGINS = {
    "https://dr-accesorios-rd.web.app",
    "https://dr-accesorios-rd.firebaseapp.com",
    "https://draccesoriosrd.blogspot.com",
}

_session = requests.Session()
_stop = threading.Event()
_firebase_jwks = PyJWKClient(FIREBASE_JWKS_URL)
_github_jwks = PyJWKClient(GITHUB_OIDC_JWKS_URL)


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _write_vapid_pem() -> str:
    raw = _b64url_decode(VAPID_PRIVATE_KEY_B64)
    if len(raw) != 32:
        raise RuntimeError("VAPID_PRIVATE_KEY inválida.")
    value = int.from_bytes(raw, "big")
    key = ec.derive_private_key(value, ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path = "/tmp/dr_radar_vapid_private.pem"
    Path(path).write_bytes(pem)
    return path


VAPID_PRIVATE_PEM = _write_vapid_pem()


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                endpoint TEXT PRIMARY KEY,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(subscriptions)").fetchall()}
        if "firebase_uid" not in columns:
            conn.execute("ALTER TABLE subscriptions ADD COLUMN firebase_uid TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_uid ON subscriptions(firebase_uid)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_events (
                event_key TEXT PRIMARY KEY,
                received_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def get_meta(key: str) -> str | None:
    with closing(db()) as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    with closing(db()) as conn:
        conn.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def parse_date_ms(value: Any) -> int:
    if not value:
        return 0
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        from datetime import datetime
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except Exception:
        return 0


def first_text(obj: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = obj.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def is_current(alert: dict[str, Any]) -> bool:
    if alert.get("active") is False:
        return False
    exp = parse_date_ms(alert.get("expiresAt"))
    return not exp or exp > int(time.time() * 1000)


def alert_key(alert: dict[str, Any]) -> str:
    return first_text(alert, ["id"]) or "|".join(
        [
            first_text(alert, ["title", "name", "headline"]),
            first_text(alert, ["type", "category"]),
            first_text(alert, ["createdAt", "publishedAt", "timestamp"]),
        ]
    )


def fetch_json(url: str, allow_404: bool = False) -> dict[str, Any] | None:
    response = _session.get(
        url,
        params={"webpush": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=20,
    )
    if allow_404 and response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def active_alerts() -> list[dict[str, Any]]:
    main = None
    seismic = None
    try:
        main = fetch_json(RADAR_URL, False)
    except Exception as exc:
        print(f"[webpush] radar.json error: {exc}", flush=True)
    try:
        seismic = fetch_json(SEISMIC_URL, True)
    except Exception as exc:
        print(f"[webpush] seismic feed error: {exc}", flush=True)

    if main is None and seismic is None:
        raise RuntimeError("No se pudo consultar ningún feed.")

    rows: list[dict[str, Any]] = []
    for origin, feed in (("main", main), ("seismic", seismic)):
        if not feed:
            continue
        for item in feed.get("alerts", []):
            if isinstance(item, dict) and is_current(item):
                alert = dict(item)
                alert["_origin"] = origin
                rows.append(alert)

    deduped: dict[str, dict[str, Any]] = {}
    for alert in rows:
        key = alert_key(alert)
        if key and key not in deduped:
            deduped[key] = alert
    return list(deduped.values())


def blogger_posts() -> list[dict[str, str]]:
    response = _session.get(
        BLOG_FEED_URL,
        params={"webpush": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    feed = payload.get("feed") or {}
    entries = feed.get("entry") or []
    posts: list[dict[str, str]] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        post_id = str((entry.get("id") or {}).get("$t") or "").strip()
        title = str((entry.get("title") or {}).get("$t") or "Nueva publicación").strip()
        published = str((entry.get("published") or {}).get("$t") or "").strip()
        url = ""
        for link in entry.get("link") or []:
            if isinstance(link, dict) and link.get("rel") == "alternate":
                url = str(link.get("href") or "").strip()
                if url:
                    break
        if post_id and url:
            posts.append(
                {
                    "id": post_id,
                    "title": title,
                    "published": published,
                    "url": url,
                }
            )
    return posts


def verify_firebase_id_token(token: str) -> str:
    signing_key = _firebase_jwks.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=FIREBASE_PROJECT_ID,
        issuer=f"https://securetoken.google.com/{FIREBASE_PROJECT_ID}",
        options={"require": ["exp", "iat", "sub", "aud", "iss"]},
    )
    uid = str(claims.get("sub") or "").strip()
    if not uid or len(uid) > 128:
        raise ValueError("Firebase UID inválido.")
    return uid


def verify_github_oidc(token: str) -> dict[str, Any]:
    signing_key = _github_jwks.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=GITHUB_OIDC_AUDIENCE,
        issuer="https://token.actions.githubusercontent.com",
        options={"require": ["exp", "iat", "sub", "aud", "iss"]},
    )
    if claims.get("repository") != GITHUB_REPOSITORY:
        raise ValueError("Repositorio OIDC no autorizado.")
    if claims.get("ref") != "refs/heads/main":
        raise ValueError("Ref OIDC no autorizado.")
    return claims


def list_subscriptions(firebase_uid: str | None = None) -> list[dict[str, str]]:
    with closing(db()) as conn:
        if firebase_uid:
            rows = conn.execute(
                "SELECT endpoint,p256dh,auth FROM subscriptions WHERE firebase_uid=?",
                (firebase_uid,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT endpoint,p256dh,auth FROM subscriptions").fetchall()
        return [dict(row) for row in rows]


def delete_subscription(endpoint: str) -> None:
    with closing(db()) as conn:
        conn.execute("DELETE FROM subscriptions WHERE endpoint=?", (endpoint,))
        conn.commit()


def push_payload(alert: dict[str, Any]) -> dict[str, Any]:
    title = first_text(alert, ["title", "name", "headline"]) or "Nueva alerta en DR Radar"
    description = first_text(alert, ["description", "message", "body", "summary"]) or "Hay una nueva alerta activa en DR Radar."
    link = first_text(alert, ["url", "link"]) or "https://draccesoriosrd.blogspot.com/"
    category = first_text(alert, ["type", "category"]) or ("sismo" if alert.get("_origin") == "seismic" else "radar")
    return {
        "title": f"DR Radar · {title}",
        "body": description[:220],
        "url": link,
        "tag": ("dr-radar-" + alert_key(alert))[:180],
        "category": category,
    }


def send_payload(payload_data: dict[str, Any], firebase_uid: str | None = None) -> tuple[int, int]:
    sent = 0
    removed = 0
    payload = json.dumps(payload_data, ensure_ascii=False)
    for sub in list_subscriptions(firebase_uid):
        info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_PEM,
                vapid_claims={"sub": VAPID_SUBJECT},
                ttl=3600,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                delete_subscription(sub["endpoint"])
                removed += 1
            else:
                print(f"[webpush] push error status={status}: {exc}", flush=True)
        except Exception as exc:
            print(f"[webpush] push error: {exc}", flush=True)
    return sent, removed


def send_alert(alert: dict[str, Any]) -> tuple[int, int]:
    return send_payload(push_payload(alert))


def post_payload(post: dict[str, str]) -> dict[str, str]:
    post_id = post["id"]
    return {
        "title": "DR Accesorios RD · Nuevo artículo",
        "body": post["title"][:220],
        "url": post["url"],
        "tag": ("dr-post-" + post_id)[-180:],
        "category": "post",
    }


def social_payload(event: dict[str, Any]) -> dict[str, str]:
    event_type = str(event.get("type") or "").upper()
    actor = str(event.get("actorName") or event.get("actorUserName") or "Alguien").strip()
    article_title = str(event.get("articleTitle") or "").strip()
    article_url = str(event.get("articleUrl") or "https://draccesoriosrd.blogspot.com/").strip()

    if "REPLY" in event_type:
        title = "💬 Te respondieron"
        body = f"{actor} respondió a tu comentario"
    elif "LIKE" in event_type:
        title = "❤️ Nueva reacción"
        body = f"{actor} indicó que le gusta tu comentario"
    elif "COMMENT" in event_type:
        title = "💬 Nueva conversación"
        body = f"{actor} comentó en una conversación que sigues"
    else:
        title = "🔔 Nueva actividad social"
        body = str(event.get("body") or "Tienes nueva actividad en DR Accesorios RD").strip()

    if article_title:
        body = f"{body} · {article_title}"

    event_key = str(event.get("eventKey") or event.get("eventId") or "")
    return {
        "title": title,
        "body": body[:220],
        "url": article_url,
        "tag": ("dr-social-" + event_key)[-180:],
        "category": "social",
    }


def social_event_seen(event_key: str) -> bool:
    with closing(db()) as conn:
        row = conn.execute("SELECT 1 FROM social_events WHERE event_key=?", (event_key,)).fetchone()
        return row is not None


def mark_social_event(event_key: str) -> None:
    with closing(db()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO social_events(event_key,received_at) VALUES(?,?)",
            (event_key, int(time.time())),
        )
        conn.commit()


def monitor_loop() -> None:
    print(f"[webpush] monitor started every {POLL_SECONDS}s", flush=True)
    while not _stop.is_set():
        radar_ok = False
        posts_ok = False

        try:
            alerts = active_alerts()
            current_keys = [alert_key(a) for a in alerts if alert_key(a)]
            raw_seen = get_meta("seen_alert_keys")
            if raw_seen is None:
                set_meta("seen_alert_keys", json.dumps(current_keys))
                print(f"[webpush] radar baseline initialized with {len(current_keys)} active alerts", flush=True)
            else:
                try:
                    seen = set(json.loads(raw_seen))
                except Exception:
                    seen = set()
                fresh = [a for a in alerts if alert_key(a) not in seen]
                for alert in fresh:
                    sent, removed = send_alert(alert)
                    print(
                        f"[webpush] new alert key={alert_key(alert)!r} sent={sent} removed={removed}",
                        flush=True,
                    )
                merged = list(dict.fromkeys(current_keys + list(seen)))[:500]
                set_meta("seen_alert_keys", json.dumps(merged))
            radar_ok = True
            set_meta("last_radar_poll_ok", str(int(time.time())))
        except Exception as exc:
            print(f"[webpush] radar monitor error: {exc}", flush=True)

        try:
            posts = blogger_posts()
            current_post_ids = [post["id"] for post in posts]
            raw_seen_posts = get_meta("seen_post_ids")
            if raw_seen_posts is None:
                set_meta("seen_post_ids", json.dumps(current_post_ids))
                print(f"[webpush] blog baseline initialized with {len(current_post_ids)} posts", flush=True)
            else:
                try:
                    seen_posts = set(json.loads(raw_seen_posts))
                except Exception:
                    seen_posts = set()

                fresh_posts = [post for post in reversed(posts) if post["id"] not in seen_posts]
                for post in fresh_posts:
                    sent, removed = send_payload(post_payload(post))
                    print(
                        f"[webpush] new post id={post['id']!r} sent={sent} removed={removed} title={post['title']!r}",
                        flush=True,
                    )

                merged_posts = list(dict.fromkeys(current_post_ids + list(seen_posts)))[:500]
                set_meta("seen_post_ids", json.dumps(merged_posts))
            posts_ok = True
            set_meta("last_blog_poll_ok", str(int(time.time())))
        except Exception as exc:
            print(f"[webpush] blog monitor error: {exc}", flush=True)

        if radar_ok or posts_ok:
            set_meta("last_poll_ok", str(int(time.time())))

        _stop.wait(POLL_SECONDS)


@app.after_request
def cors_headers(response):
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/health", methods=["GET"])
def health():
    with closing(db()) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM subscriptions").fetchone()["n"]
        linked = conn.execute(
            "SELECT COUNT(*) AS n FROM subscriptions WHERE firebase_uid IS NOT NULL AND firebase_uid != ''"
        ).fetchone()["n"]
    return jsonify(
        ok=True,
        subscriptions=count,
        socialLinkedSubscriptions=linked,
        lastPollOk=get_meta("last_poll_ok"),
        lastRadarPollOk=get_meta("last_radar_poll_ok"),
        lastBlogPollOk=get_meta("last_blog_poll_ok"),
        lastSocialDispatchAt=get_meta("last_social_dispatch_at"),
        pollSeconds=POLL_SECONDS,
        blogFeed=BLOG_FEED_URL,
    )


@app.route("/vapid-public-key", methods=["GET"])
def vapid_public_key():
    return jsonify(publicKey=VAPID_PUBLIC_KEY)


@app.route("/subscribe", methods=["OPTIONS"])
@app.route("/unsubscribe", methods=["OPTIONS"])
def preflight():
    return ("", 204)


@app.route("/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = str(data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    id_token = str(data.get("idToken") or "").strip()
    firebase_uid = None
    if id_token:
        try:
            firebase_uid = verify_firebase_id_token(id_token)
        except Exception as exc:
            print(f"[webpush] invalid Firebase token: {exc}", flush=True)
            return jsonify(ok=False, error="Token de Firebase inválido."), 401

    if not endpoint.startswith("https://") or not p256dh or not auth:
        return jsonify(ok=False, error="Suscripción inválida."), 400
    if len(endpoint) > 4096 or len(p256dh) > 1024 or len(auth) > 1024:
        return jsonify(ok=False, error="Suscripción fuera de límites."), 400

    now = int(time.time())
    with closing(db()) as conn:
        conn.execute(
            """
            INSERT INTO subscriptions(endpoint,p256dh,auth,created_at,last_seen_at,firebase_uid)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET
              p256dh=excluded.p256dh,
              auth=excluded.auth,
              last_seen_at=excluded.last_seen_at,
              firebase_uid=COALESCE(excluded.firebase_uid, subscriptions.firebase_uid)
            """,
            (endpoint, p256dh, auth, now, now, firebase_uid),
        )
        conn.commit()

    return jsonify(ok=True)


@app.route("/dispatch-social", methods=["POST"])
def dispatch_social():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify(ok=False, error="No autorizado."), 401
    token = auth_header.split(" ", 1)[1].strip()
    try:
        verify_github_oidc(token)
    except Exception as exc:
        print(f"[webpush] invalid GitHub OIDC: {exc}", flush=True)
        return jsonify(ok=False, error="OIDC inválido."), 401

    payload = request.get_json(silent=True) or {}
    events = payload.get("events") or []
    if not isinstance(events, list) or len(events) > 300:
        return jsonify(ok=False, error="Lote social inválido."), 400

    baseline = get_meta("social_baseline_initialized") != "1"
    sent_total = 0
    removed_total = 0
    processed = 0

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").upper()
        if "UNLIKE" in event_type:
            continue
        recipient_uid = str(event.get("recipientUid") or "").strip()
        event_key = str(event.get("eventKey") or event.get("eventId") or "").strip()
        if not recipient_uid or not event_key or social_event_seen(event_key):
            continue

        if not baseline:
            sent, removed = send_payload(social_payload(event), recipient_uid)
            sent_total += sent
            removed_total += removed
        mark_social_event(event_key)
        processed += 1

    if baseline:
        set_meta("social_baseline_initialized", "1")
    set_meta("last_social_dispatch_at", str(int(time.time())))
    return jsonify(
        ok=True,
        baseline=baseline,
        processed=processed,
        sent=sent_total,
        removed=removed_total,
    )


@app.route("/unsubscribe", methods=["POST", "DELETE"])
def unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = str(data.get("endpoint") or "").strip()
    if endpoint:
        delete_subscription(endpoint)
    return jsonify(ok=True)


init_db()

if os.environ.get("DISABLE_MONITOR", "0") != "1":
    threading.Thread(target=monitor_loop, name="dr-radar-webpush-monitor", daemon=True).start()
