print("✅ Ładuję mój właściwy main.py")
from flask import Flask, jsonify, send_from_directory, request, Response
import os, json, requests, time
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

_cache = {}

# Prawidłowe kategorie (zamiast plików JSON)
CATEGORIES = ["main", "nauka", "zwierzeta", "fun",
              "k1", "k2", "k3", "k4", "k5", "k6"]

app = Flask(__name__)

# Inicjalizacja bazy przy starcie (nie wywala apki gdy brak DATABASE_URL lokalnie)
try:
    db.init_db()
    print("✅ Baza danych gotowa")
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
    key_index = 0

    def current_key():
        return API_KEYS[key_index % len(API_KEYS)]

    video_ids = []
    for channel_id in channel_ids:
        uploads_playlist_id = "UU" + channel_id[2:]
        attempts = 0
        while attempts < len(API_KEYS):
            try:
                url = ("https://www.googleapis.com/youtube/v3/playlistItems"
                       f"?part=contentDetails&playlistId={uploads_playlist_id}"
                       f"&maxResults={UPLOADS_PER_CHANNEL}&key={current_key()}")
                res = requests.get(url, timeout=10).json()
                quota_used += 1
                if 'error' in res:
                    print(f"Błąd API (playlistItems): {res['error'].get('message')}")
                    key_index += 1
                    attempts += 1
                    continue
                for item in res.get("items", []):
                    vid = item["contentDetails"].get("videoId")
                    if vid:
                        video_ids.append(vid)
                break
            except Exception as e:
                print(f"Błąd playlistItems dla {channel_id}: {e}")
                key_index += 1
                attempts += 1
                time.sleep(0.3)

    videos = []
    for batch in _chunk(video_ids, 50):
        ids = ",".join(batch)
        attempts = 0
        while attempts < len(API_KEYS):
            try:
                url = ("https://www.googleapis.com/youtube/v3/videos"
                       f"?part=statistics,snippet,contentDetails&id={ids}"
                       f"&key={current_key()}")
                res = requests.get(url, timeout=10).json()
                quota_used += 1
                if 'error' in res:
                    print(f"Błąd API (videos): {res['error'].get('message')}")
                    key_index += 1
                    attempts += 1
                    continue
                for details in res.get("items", []):
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
                        "published": details["snippet"].get("publishedAt", ""),
                        "url": f"https://www.youtube.com/shorts/{details['id']}",
                        "thumbnail": details["snippet"]["thumbnails"]["medium"]["url"],
                        "views": int(details["statistics"].get("viewCount", 0)),
                        "likes": int(details["statistics"].get("likeCount", 0)),
                        "duration": f"{mm}:{str(ss).zfill(2)}",
                        "tags": details["snippet"].get("tags", []),
                    })
                break
            except Exception as e:
                print(f"Błąd videos dla batcha: {e}")
                key_index += 1
                attempts += 1
                time.sleep(0.3)

    return videos, quota_used


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
