print("✅ Ładuję mój właściwy main.py")
from flask import Flask, jsonify, send_from_directory, request, Response
import os, json, requests, time, statistics
from dotenv import load_dotenv
import isodate
import db

load_dotenv()

API_KEYS = [k for k in (
    os.getenv("YOUTUBE_API_KEY_1"),
    os.getenv("YOUTUBE_API_KEY_2"),
    os.getenv("YOUTUBE_API_KEY_3"),
    os.getenv("YOUTUBE_API_KEY_4"),
    os.getenv("YOUTUBE_API_KEY_5"),
    os.getenv("YOUTUBE_API_KEY_6"),
    os.getenv("YOUTUBE_API_KEY_7"),
    os.getenv("YOUTUBE_API_KEY_8"),
    os.getenv("YOUTUBE_API_KEY_9"),
) if k]

SHORT_MAX_SECONDS = 180
UPLOADS_PER_CHANNEL = 5
CACHE_TTL = 900
MAX_LINK_LEN = 300

# Parametry algorytmu Hity
HITS_UPLOADS = 20
HITS_DAYS = 7
HITS_MIN_VIEWS = 50000
HITS_MULTIPLIER = 3.0
HITS_CACHE_TTL = 21600

# Cache kategorii YT (24h)
CATEGORIES_CACHE_TTL = 86400

_cache = {}

# Prawidłowe kategorie (zamiast plików JSON)
CATEGORIES = ["main", "nauka", "zwierzeta", "fun",
              "k1", "k2", "k3", "k4", "k5", "k6"]

app = Flask(__name__)

# Inicjalizacja bazy przy starcie (nie wywala apki gdy brak DATABASE_URL lokalnie)
try:
    db.init_db()
    print("✅ Baza danych gotowa")
    # Automatyczna migracja z JSON-ów — wykona się TYLKO gdy baza jest pusta.
    # Po pierwszym razie sama się pomija (baza ma już kanały).
    try:
        if db.is_empty():
            import migrate_json_to_db
            migrate_json_to_db.main()
            print("✅ Automatyczna migracja JSON -> baza zakończona")
        else:
            print("ℹ️ Baza ma już kanały — migracja pominięta")
    except Exception as e:
        print(f"⚠️ Migracja pominięta z powodu błędu: {e}")
except Exception as e:
    print(f"⚠️ Nie udało się zainicjalizować bazy: {e}")


@app.route('/')
@app.route('/nauka')
@app.route('/zwierzeta')
@app.route('/fun')
@app.route('/k1')
@app.route('/k2')
@app.route('/k3')
@app.route('/k4')
@app.route('/k5')
@app.route('/k6')
@app.route('/viral')
@app.route('/hity')
@app.route('/trendy')
@app.route('/manage')
def index():
    return send_from_directory('frontend', 'index.html')


@app.route('/style.css')
def style():
    return send_from_directory('frontend', 'style.css')


@app.route('/favicon.ico')
def favicon():
    return Response(status=204)


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _norm_category(category):
    category = (category or "").lower()
    if not category:
        return "main"
    return category


def fetch_videos_for_channels(channel_ids):
    if not API_KEYS:
        return [], 0

    quota_used = 0
    key_idx = [0]

    video_ids = []
    for channel_id in channel_ids:
        uploads_playlist_id = "UU" + channel_id[2:]
        attempts = 0
        while attempts < len(API_KEYS):
            try:
                url = ("https://www.googleapis.com/youtube/v3/playlistItems"
                       f"?part=contentDetails&playlistId={uploads_playlist_id}"
                       f"&maxResults={UPLOADS_PER_CHANNEL}"
                       f"&key={API_KEYS[key_idx[0] % len(API_KEYS)]}")
                res = requests.get(url, timeout=10).json()
                quota_used += 1
                if 'error' in res:
                    print(f"Błąd API (playlistItems): {res['error'].get('message')}")
                    key_idx[0] += 1
                    attempts += 1
                    continue
                for item in res.get("items", []):
                    vid = item["contentDetails"].get("videoId")
                    if vid:
                        video_ids.append(vid)
                break
            except Exception as e:
                print(f"Błąd playlistItems dla {channel_id}: {e}")
                key_idx[0] += 1
                attempts += 1
                time.sleep(0.3)

    videos, q = _fetch_video_details_batch(video_ids, key_idx)
    quota_used += q
    return videos, quota_used


def _parse_video_details(items):
    """Parsuje listę items z YouTube videos API do słowników shortów."""
    videos = []
    for details in items:
        duration_iso = details["contentDetails"].get("duration", "PT0S")
        try:
            total_seconds = int(isodate.parse_duration(duration_iso).total_seconds())
        except Exception:
            total_seconds = 0
        if total_seconds == 0 or total_seconds > SHORT_MAX_SECONDS:
            continue
        mm, ss = total_seconds // 60, total_seconds % 60
        videos.append({
            "title": details["snippet"]["title"],
            "channel": details["snippet"].get("channelTitle", "Nieznany kanał"),
            "channel_id": details["snippet"].get("channelId", ""),
            "published": details["snippet"].get("publishedAt", ""),
            "url": f"https://www.youtube.com/shorts/{details['id']}",
            "thumbnail": details["snippet"]["thumbnails"]["medium"]["url"],
            "views": int(details["statistics"].get("viewCount", 0)),
            "likes": int(details["statistics"].get("likeCount", 0)),
            "duration": f"{mm}:{str(ss).zfill(2)}",
            "tags": details["snippet"].get("tags", []),
        })
    return videos


def _fetch_video_details_batch(video_ids, key_index_ref):
    """Pobiera szczegóły filmów w batchach po 50. Modyfikuje key_index_ref[0]."""
    videos = []
    quota = 0
    for batch in _chunk(video_ids, 50):
        ids = ",".join(batch)
        attempts = 0
        while attempts < len(API_KEYS):
            try:
                url = ("https://www.googleapis.com/youtube/v3/videos"
                       f"?part=statistics,snippet,contentDetails&id={ids}"
                       f"&key={API_KEYS[key_index_ref[0] % len(API_KEYS)]}")
                res = requests.get(url, timeout=10).json()
                quota += 1
                if 'error' in res:
                    key_index_ref[0] += 1
                    attempts += 1
                    continue
                videos.extend(_parse_video_details(res.get("items", [])))
                break
            except Exception as e:
                print(f"Błąd videos batch: {e}")
                key_index_ref[0] += 1
                attempts += 1
                time.sleep(0.3)
    return videos, quota


@app.route('/api/hits')
def get_hits():
    now = time.time()
    cached = _cache.get("hity")
    if cached and now - cached[0] < HITS_CACHE_TTL:
        payload = dict(cached[1])
        payload["cached"] = True
        return jsonify(payload)

    if not API_KEYS:
        return jsonify({"videos": [], "quota_used": 0, "cached": False})

    try:
        channels = db.get_all_channels()
    except Exception as e:
        return jsonify({"videos": [], "quota_used": 0, "cached": False, "error": str(e)})

    key_idx = [0]
    quota_used = 0
    cutoff_ts = now - HITS_DAYS * 86400

    # Zbierz video_id per kanał
    channel_video_ids = {}
    for channel_id in channels:
        uploads_playlist_id = "UU" + channel_id[2:]
        attempts = 0
        while attempts < len(API_KEYS):
            try:
                url = ("https://www.googleapis.com/youtube/v3/playlistItems"
                       f"?part=contentDetails&playlistId={uploads_playlist_id}"
                       f"&maxResults={HITS_UPLOADS}"
                       f"&key={API_KEYS[key_idx[0] % len(API_KEYS)]}")
                res = requests.get(url, timeout=10).json()
                quota_used += 1
                if 'error' in res:
                    key_idx[0] += 1
                    attempts += 1
                    continue
                ids = [i["contentDetails"]["videoId"]
                       for i in res.get("items", [])
                       if i["contentDetails"].get("videoId")]
                channel_video_ids[channel_id] = ids
                break
            except Exception as e:
                print(f"Błąd playlistItems hits {channel_id}: {e}")
                key_idx[0] += 1
                attempts += 1
                time.sleep(0.3)

    all_ids = [vid for ids in channel_video_ids.values() for vid in ids]
    all_videos, q = _fetch_video_details_batch(all_ids, key_idx)
    quota_used += q

    # Zbuduj mapę video -> kanał i zbierz views per kanał
    channel_views = {ch: [] for ch in channels}
    for v in all_videos:
        ch = v.get("channel_id", "")
        if ch in channel_views:
            channel_views[ch].append(v["views"])

    hits = []
    for v in all_videos:
        pub_ts = 0
        try:
            from datetime import datetime, timezone
            pub_ts = datetime.fromisoformat(
                v["published"].replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            pass

        if pub_ts < cutoff_ts:
            continue
        if v["views"] < HITS_MIN_VIEWS:
            continue

        ch = v.get("channel_id", "")
        views_list = channel_views.get(ch, [])
        if len(views_list) < 2:
            continue
        median_views = statistics.median(views_list)
        if median_views == 0:
            continue
        if v["views"] >= median_views * HITS_MULTIPLIER:
            v["multiplier"] = round(v["views"] / median_views, 1)
            hits.append(v)

    hits.sort(key=lambda v: v["views"], reverse=True)
    payload = {"videos": hits, "quota_used": quota_used, "cached": False}
    _cache["hity"] = (now, payload)
    print(f"🔥 Hity: {len(hits)} wyników, ~{quota_used} quota")
    return jsonify(payload)


@app.route('/api/categories')
def get_categories():
    region = (request.args.get("region") or "PL").upper()
    cache_key = f"categories_{region}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < CATEGORIES_CACHE_TTL:
        return jsonify(cached[1])

    if not API_KEYS:
        return jsonify({"categories": []})

    try:
        url = ("https://www.googleapis.com/youtube/v3/videoCategories"
               f"?part=snippet&regionCode={region}&hl=pl"
               f"&key={API_KEYS[0]}")
        res = requests.get(url, timeout=10).json()
        cats = [
            {"id": c["id"], "title": c["snippet"]["title"]}
            for c in res.get("items", [])
            if c["snippet"].get("assignable", False)
        ]
        payload = {"categories": cats}
        _cache[cache_key] = (now, payload)
        return jsonify(payload)
    except Exception as e:
        return jsonify({"categories": [], "error": str(e)})


@app.route('/api/trending')
def get_trending():
    region = (request.args.get("region") or "PL").upper()
    category_id = request.args.get("category_id") or ""
    cache_key = f"trending_{region}_{category_id}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < HITS_CACHE_TTL:
        payload = dict(cached[1])
        payload["cached"] = True
        return jsonify(payload)

    if not API_KEYS:
        return jsonify({"videos": [], "quota_used": 0, "cached": False})

    key_idx = [0]
    quota_used = 0
    videos = []

    # mostPopular zwraca do 50 wyników, bierzemy 2 strony (100 filmów) żeby mieć szansę na shorty
    page_token = ""
    for _ in range(2):
        attempts = 0
        while attempts < len(API_KEYS):
            try:
                url = ("https://www.googleapis.com/youtube/v3/videos"
                       f"?part=statistics,snippet,contentDetails"
                       f"&chart=mostPopular&regionCode={region}"
                       f"&maxResults=50"
                       + (f"&videoCategoryId={category_id}" if category_id else "")
                       + (f"&pageToken={page_token}" if page_token else "")
                       + f"&key={API_KEYS[key_idx[0] % len(API_KEYS)]}")
                res = requests.get(url, timeout=10).json()
                quota_used += 1
                if 'error' in res:
                    key_idx[0] += 1
                    attempts += 1
                    continue
                videos.extend(_parse_video_details(res.get("items", [])))
                page_token = res.get("nextPageToken", "")
                break
            except Exception as e:
                print(f"Błąd trending: {e}")
                key_idx[0] += 1
                attempts += 1
                time.sleep(0.3)
        if not page_token:
            break

    videos.sort(key=lambda v: v["views"], reverse=True)
    payload = {"videos": videos, "quota_used": quota_used, "cached": False, "region": region}
    _cache[cache_key] = (now, payload)
    print(f"🌍 Trendy {region}: {len(videos)} shortów, ~{quota_used} quota")
    return jsonify(payload)


@app.route('/api/videos')
def get_all_videos():
    category = _norm_category(request.args.get("category", ""))
    is_viral = category == "viral"
    cache_key = category

    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL:
        payload = dict(cached[1])
        payload["cached"] = True
        return jsonify(payload)

    try:
        if is_viral:
            channels = db.get_all_channels()
        else:
            channels = db.get_channels(category)
    except Exception as e:
        print(f"Błąd odczytu kanałów z bazy: {e}")
        return jsonify({"quota_used": 0, "videos": [], "cached": False, "error": "db"})

    videos, quota_used = fetch_videos_for_channels(channels)
    videos.sort(key=lambda v: v["published"], reverse=True)

    payload = {"quota_used": quota_used, "videos": videos, "cached": False}
    _cache[cache_key] = (now, payload)
    print(f"🔢 Zużyto ~{quota_used} jednostek quota (kategoria: {cache_key})")
    return jsonify(payload)


@app.route('/api/channels')
def api_list_channels():
    category = _norm_category(request.args.get("category", ""))
    try:
        return jsonify({"channels": db.list_channels(category)})
    except Exception as e:
        return jsonify({"channels": [], "error": str(e)}), 500


@app.route('/api/channels', methods=['POST'])
def api_add_channel():
    data = request.get_json(silent=True) or {}
    category = _norm_category(data.get("category", ""))
    link = (data.get("link") or "").strip()

    if category not in CATEGORIES:
        return jsonify({"ok": False, "error": "Nieprawidłowa kategoria"}), 400
    if not link:
        return jsonify({"ok": False, "error": "Wklej link"}), 400
    if len(link) > MAX_LINK_LEN:
        return jsonify({"ok": False, "error": "Link za długi"}), 400

    api_key = API_KEYS[0] if API_KEYS else None
    channel_id, info = db.resolve_channel_id(link, api_key)
    if not channel_id:
        return jsonify({"ok": False, "error": info or "Nie rozpoznano linku"}), 400

    title = info if isinstance(info, str) else None
    try:
        added = db.add_channel(category, channel_id, title)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Błąd bazy: {e}"}), 500

    _cache.pop(category, None)
    _cache.pop("viral", None)
    return jsonify({
        "ok": True,
        "added": added,
        "channel_id": channel_id,
        "title": title,
        "message": "Dodano" if added else "Ten kanał już jest w tej kategorii",
    })


@app.route('/api/channels/<int:row_id>', methods=['DELETE'])
def api_delete_channel(row_id):
    try:
        ok = db.delete_channel(row_id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    _cache.clear()
    return jsonify({"ok": ok})


if __name__ == '__main__':
    app.run()
