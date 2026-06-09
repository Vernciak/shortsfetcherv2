print("✅ Ładuję mój właściwy main.py")
from flask import Flask, jsonify, send_from_directory, request, Response
import os, json, requests, time, statistics
from dotenv import load_dotenv
import isodate
import db
from commentary_patterns import score_commentary, LANG_FLAGS

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

# Parametry endpointu Commentary
# Kraje do przeszukania trendów (1 jednostka API per kraj)
COMMENTARY_COUNTRIES = ["US", "GB", "RU", "BR", "ES", "MX", "DE", "FR", "IT", "PL"]
# Kategorie YT z których pobieramy dodatkowe trendy (1 jednostka per kraj×kategoria)
# 24=Entertainment, 22=People&Blogs, 28=Science&Tech, 26=Howto&Style
COMMENTARY_EXTRA_CATEGORIES = ["24", "22", "28", "26"]
# Minimalny wynik żeby short trafił do wyników (łatwy do podkręcenia)
COMMENTARY_MIN_SCORE = 10
# TTL cache commentary — droższe przez wiele krajów, więc dłuższe (2h)
COMMENTARY_CACHE_TTL = 7200

# Parametry endpointu AI (Gemini)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Model — 2.5-flash-lite jest GA; 503 to chwilowe przeciążenie (obsłużone przez retry)
GEMINI_MODEL = "gemini-2.5-flash-lite"
# Minimalny confidence Gemini żeby short trafił do wyników (0-100)
AI_MIN_CONFIDENCE = 60
# TTL cache wyników AI (2h)
AI_CACHE_TTL = 7200
# Max kandydatów do oceny per request — reszta zostanie w DB cache przy kolejnym odświeżeniu
AI_MAX_NEW_PER_REQUEST = 150

# Lekkie anty-wzorce dla preselekcji przed Gemini (tylko oczywiste przypadki)
_AI_HARD_REJECT = [
    "official trailer", "official clip", "clip from", "official video",
    "trailer oficial", "clipe oficial", "trailer dublado", "dublado", "legendado",
    "oficjalny zwiastun", "oficjalny trailer",
    "официальный трейлер", "официальный клип",
    "subtitulado", "doblado", "dubbed", "subbed",
]

_GEMINI_SYSTEM_PROMPT = (
    "Oceniasz YouTube Shorts pod kątem czy to COMMENTARY / reakcja / ciekawostka / "
    "edutainment z narracją lektora (NIE czysty klip z filmu/serialu, trailer, teledysk, "
    "surowy materiał bez komentarza). Rozumiesz wszystkie języki. "
    "Zwróć czysty JSON (bez markdown), tablicę obiektów: "
    "{video_id, is_commentary (bool), confidence (0-100), reason (krótko po polsku)}. "
    "Nic poza JSON."
)

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
@app.route('/commentary')
@app.route('/ai')
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
        snip = details["snippet"]
        stats = details["statistics"]
        videos.append({
            "id": details["id"],
            "title": snip["title"],
            "channel": snip.get("channelTitle", "Nieznany kanał"),
            "channel_id": snip.get("channelId", ""),
            "published": snip.get("publishedAt", ""),
            "url": f"https://www.youtube.com/shorts/{details['id']}",
            "thumbnail": snip["thumbnails"]["medium"]["url"],
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
            "duration": f"{mm}:{str(ss).zfill(2)}",
            "duration_seconds": total_seconds,
            "tags": snip.get("tags", []),
            "description": snip.get("description", "")[:500],
            "audio_lang": snip.get("defaultAudioLanguage") or snip.get("defaultLanguage") or "",
            "category_id": snip.get("categoryId", ""),
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


@app.route('/api/commentary')
def get_commentary():
    now = time.time()
    cached = _cache.get("commentary")
    if cached and now - cached[0] < COMMENTARY_CACHE_TTL:
        payload = dict(cached[1])
        payload["cached"] = True
        return jsonify(payload)

    if not API_KEYS:
        return jsonify({"videos": [], "quota_used": 0, "cached": False})

    try:
        channels_with_flags = db.get_all_channels_with_flags()
    except Exception as e:
        return jsonify({"videos": [], "quota_used": 0, "cached": False, "error": str(e)})

    key_idx = [0]
    quota_used = 0
    seen_ids = set()
    pool = []

    def add_to_pool(videos_list):
        for v in videos_list:
            vid = v.get("id", v.get("url", ""))
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                pool.append(v)

    # Źródło 1: moje kanały (ostatnie 20 uploadów jak w hitach)
    commentary_channel_ids = {ch for ch, flag in channels_with_flags if flag}
    all_channel_ids = [ch for ch, _ in channels_with_flags]

    channel_video_ids = {}
    for channel_id in all_channel_ids:
        uploads_playlist_id = "UU" + channel_id[2:]
        attempts = 0
        while attempts < len(API_KEYS):
            try:
                url = ("https://www.googleapis.com/youtube/v3/playlistItems"
                       f"?part=contentDetails&playlistId={uploads_playlist_id}"
                       f"&maxResults=20"
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
                print(f"Błąd playlistItems commentary {channel_id}: {e}")
                key_idx[0] += 1
                attempts += 1
                time.sleep(0.3)

    all_ids = [vid for ids in channel_video_ids.values() for vid in ids]
    channel_videos, q = _fetch_video_details_batch(all_ids, key_idx)
    quota_used += q
    add_to_pool(channel_videos)

    # Źródła 2+3: trendy per kraj + trendy per kraj × kategoria
    def fetch_trending_page(region, category_id=""):
        nonlocal quota_used
        attempts = 0
        while attempts < len(API_KEYS):
            try:
                url = ("https://www.googleapis.com/youtube/v3/videos"
                       f"?part=statistics,snippet,contentDetails"
                       f"&chart=mostPopular&regionCode={region}&maxResults=50"
                       + (f"&videoCategoryId={category_id}" if category_id else "")
                       + f"&key={API_KEYS[key_idx[0] % len(API_KEYS)]}")
                res = requests.get(url, timeout=10).json()
                quota_used += 1
                if 'error' in res:
                    key_idx[0] += 1
                    attempts += 1
                    continue
                return _parse_video_details(res.get("items", []))
            except Exception as e:
                print(f"Błąd trending commentary {region}/{category_id}: {e}")
                key_idx[0] += 1
                attempts += 1
                time.sleep(0.3)
        return []

    for country in COMMENTARY_COUNTRIES:
        add_to_pool(fetch_trending_page(country))
        for cat_id in COMMENTARY_EXTRA_CATEGORIES:
            add_to_pool(fetch_trending_page(country, cat_id))

    # Scoring
    cutoff_ts = now - 86400 * 90  # odrzucamy filmy starsze niż 90 dni
    from datetime import datetime, timezone

    result = []
    for v in pool:
        pub_ts = 0
        try:
            pub_ts = datetime.fromisoformat(
                v["published"].replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            pass
        if pub_ts and pub_ts < cutoff_ts:
            continue

        is_commentary_ch = v.get("channel_id", "") in commentary_channel_ids
        score, lang = score_commentary(
            v["title"],
            v.get("description", ""),
            v.get("duration_seconds", 0),
            is_commentary_ch,
        )
        if score < COMMENTARY_MIN_SCORE:
            continue

        hours_since = max((now - pub_ts) / 3600, 0.5) if pub_ts else None
        vph = round(v["views"] / hours_since, 1) if hours_since else None
        eng = round((v["likes"] + v["comment_count"]) / v["views"] * 100, 2) if v["views"] > 0 else 0

        v["commentary_score"] = score
        v["lang"] = lang
        v["lang_flag"] = LANG_FLAGS.get(lang, "🌐")
        v["vph"] = vph
        v["engagement_rate"] = eng
        result.append(v)

    result.sort(key=lambda v: v["commentary_score"], reverse=True)
    payload = {"videos": result, "quota_used": quota_used, "cached": False}
    _cache["commentary"] = (now, payload)
    print(f"🎙️ Commentary: {len(result)} wyników (pula {len(pool)}), ~{quota_used} quota")
    return jsonify(payload)


@app.route('/api/channels/<channel_id>/commentary', methods=['PUT'])
def api_set_commentary(channel_id):
    data = request.get_json(silent=True) or {}
    value = bool(data.get("value", False))
    try:
        updated = db.set_channel_commentary(channel_id, value)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    _cache.pop("commentary", None)
    return jsonify({"ok": True, "updated": updated, "is_commentary": value})


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


def _ai_hard_reject(title):
    t = title.lower()
    return any(p in t for p in _AI_HARD_REJECT)


def _rate_with_gemini(candidates):
    """Wysyła kandydatów do Gemini w paczkach po 50. Zwraca dict video_id -> rating.

    Retry do 3 razy per paczka przy 503 (model przeciążony), z rosnącym opóźnieniem.
    """
    if not GEMINI_API_KEY or not candidates:
        return {}

    results = {}
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")

    for batch in _chunk(candidates, 50):
        items = [
            {
                "video_id": v["id"],
                "title": v["title"],
                "channel": v["channel"],
                "description": (v.get("description") or "")[:300],
            }
            for v in batch
        ]
        payload = {
            "system_instruction": {"parts": [{"text": _GEMINI_SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text": json.dumps(items, ensure_ascii=False)}]}],
            "generationConfig": {"temperature": 0.1},
        }
        for attempt in range(3):
            try:
                res = requests.post(url, json=payload, timeout=25).json()
                if "candidates" not in res:
                    code = res.get("error", {}).get("code", 0)
                    print(f"⚠️ Gemini brak 'candidates' (próba {attempt+1}): {json.dumps(res)[:200]}")
                    if code in (503, 429) and attempt < 2:
                        time.sleep(3 * (attempt + 1))
                        continue
                    break
                text = res["candidates"][0]["content"]["parts"][0]["text"].strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1]
                    text = text.rsplit("```", 1)[0].strip()
                for r in json.loads(text):
                    vid = r.get("video_id")
                    if vid:
                        results[vid] = {
                            "is_commentary": bool(r.get("is_commentary", False)),
                            "confidence": int(r.get("confidence", 0)),
                            "reason": r.get("reason", ""),
                        }
                break
            except Exception as e:
                print(f"⚠️ Gemini batch error ({len(batch)} filmów, próba {attempt+1}): {e}")
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))

    return results


@app.route('/api/ai')
def get_ai():
    now = time.time()
    cached = _cache.get("ai")
    if cached and now - cached[0] < AI_CACHE_TTL:
        payload = dict(cached[1])
        payload["cached"] = True
        return jsonify(payload)

    if not API_KEYS:
        return jsonify({"videos": [], "quota_used": 0, "cached": False})

    try:
        channels_with_flags = db.get_all_channels_with_flags()
    except Exception as e:
        return jsonify({"videos": [], "quota_used": 0, "cached": False, "error": str(e)})

    key_idx = [0]
    quota_used = 0
    seen_ids = set()
    pool = []
    commentary_channel_ids = {ch for ch, flag in channels_with_flags if flag}

    def add_to_pool(videos_list):
        for v in videos_list:
            vid = v.get("id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                pool.append(v)

    # Źródło 1: kanały (te same co commentary)
    all_channel_ids = [ch for ch, _ in channels_with_flags]
    channel_video_ids = {}
    for channel_id in all_channel_ids:
        uploads_playlist_id = "UU" + channel_id[2:]
        attempts = 0
        while attempts < len(API_KEYS):
            try:
                url = ("https://www.googleapis.com/youtube/v3/playlistItems"
                       f"?part=contentDetails&playlistId={uploads_playlist_id}"
                       f"&maxResults=20"
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
                print(f"Błąd playlistItems ai {channel_id}: {e}")
                key_idx[0] += 1
                attempts += 1
                time.sleep(0.3)

    all_ids = [vid for ids in channel_video_ids.values() for vid in ids]
    channel_videos, q = _fetch_video_details_batch(all_ids, key_idx)
    quota_used += q
    add_to_pool(channel_videos)

    # Źródła 2+3: trendy per kraj + kategoria (identycznie jak commentary)
    def fetch_trending_ai(region, category_id=""):
        nonlocal quota_used
        attempts = 0
        while attempts < len(API_KEYS):
            try:
                url = ("https://www.googleapis.com/youtube/v3/videos"
                       f"?part=statistics,snippet,contentDetails"
                       f"&chart=mostPopular&regionCode={region}&maxResults=50"
                       + (f"&videoCategoryId={category_id}" if category_id else "")
                       + f"&key={API_KEYS[key_idx[0] % len(API_KEYS)]}")
                res = requests.get(url, timeout=10).json()
                quota_used += 1
                if 'error' in res:
                    key_idx[0] += 1
                    attempts += 1
                    continue
                return _parse_video_details(res.get("items", []))
            except Exception as e:
                print(f"Błąd trending ai {region}/{category_id}: {e}")
                key_idx[0] += 1
                attempts += 1
                time.sleep(0.3)
        return []

    for country in COMMENTARY_COUNTRIES:
        add_to_pool(fetch_trending_ai(country))
        for cat_id in COMMENTARY_EXTRA_CATEGORIES:
            add_to_pool(fetch_trending_ai(country, cat_id))

    # Preselekcja: tylko wiek i twardy odrzut tytułu
    from datetime import datetime, timezone
    cutoff_ts = now - 86400 * 90

    auto_pass = []
    to_rate = []

    for v in pool:
        pub_ts = 0
        try:
            pub_ts = datetime.fromisoformat(v["published"].replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
        if pub_ts and pub_ts < cutoff_ts:
            continue
        if _ai_hard_reject(v["title"]):
            continue

        if v.get("channel_id", "") in commentary_channel_ids:
            v["ai_confidence"] = 100
            v["ai_reason"] = "Ręcznie oznaczony kanał commentary"
            auto_pass.append(v)
        else:
            to_rate.append(v)

    # Sprawdź cache ocen w DB
    candidate_ids = [v["id"] for v in to_rate]
    try:
        cached_ratings = db.get_ai_ratings(candidate_ids)
    except Exception as e:
        print(f"⚠️ DB ai_ratings read: {e}")
        cached_ratings = {}

    uncached = [v for v in to_rate if v["id"] not in cached_ratings]

    # Oceń nowe przez Gemini — max AI_MAX_NEW_PER_REQUEST na raz (reszta trafi przy kolejnym odświeżeniu)
    new_ratings = {}
    to_gemini = uncached[:AI_MAX_NEW_PER_REQUEST]
    if len(uncached) > AI_MAX_NEW_PER_REQUEST:
        print(f"ℹ️ AI: {len(uncached)} nowych kandydatów, oceniam pierwsze {AI_MAX_NEW_PER_REQUEST}")
    if to_gemini and GEMINI_API_KEY:
        new_ratings = _rate_with_gemini(to_gemini)
        try:
            db.save_ai_ratings([{"video_id": vid, **r} for vid, r in new_ratings.items()])
        except Exception as e:
            print(f"⚠️ DB ai_ratings write: {e}")
    elif to_gemini:
        print("⚠️ Brak GEMINI_API_KEY — ocena AI pominięta")

    all_ratings = {**cached_ratings, **new_ratings}

    # Złóż wyniki
    result = list(auto_pass)
    for v in to_rate:
        rating = all_ratings.get(v["id"])
        if not rating or not rating["is_commentary"] or rating["confidence"] < AI_MIN_CONFIDENCE:
            continue
        v["ai_confidence"] = rating["confidence"]
        v["ai_reason"] = rating["reason"]
        result.append(v)

    # Detekcja języka i metryki
    for v in result:
        _, lang = score_commentary(v["title"], v.get("description", ""),
                                   v.get("duration_seconds", 0), False)
        v["lang"] = lang
        v["lang_flag"] = LANG_FLAGS.get(lang, "🌐")
        pub_ts = 0
        try:
            pub_ts = datetime.fromisoformat(v["published"].replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
        hours_since = max((now - pub_ts) / 3600, 0.5) if pub_ts else None
        v["vph"] = round(v["views"] / hours_since, 1) if hours_since else None
        v["engagement_rate"] = (round((v["likes"] + v["comment_count"]) / v["views"] * 100, 2)
                                if v["views"] > 0 else 0)

    result.sort(key=lambda v: v.get("ai_confidence", 0), reverse=True)

    n_new = len(new_ratings)
    n_cached = len(candidate_ids) - len(uncached)
    print(f"🤖 AI: {len(result)} wyników (pula {len(pool)}, "
          f"Gemini nowych: {n_new}, z cache: {n_cached}), ~{quota_used} quota")

    payload = {
        "videos": result,
        "quota_used": quota_used,
        "cached": False,
        "gemini_new": n_new,
        "gemini_cached": n_cached,
    }
    _cache["ai"] = (now, payload)
    return jsonify(payload)


if __name__ == '__main__':
    app.run()
