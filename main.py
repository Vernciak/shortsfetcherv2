print("✅ Ładuję mój właściwy main.py")
from flask import Flask, jsonify, send_from_directory, request, Response
import os, json, requests, time, statistics, re
from dotenv import load_dotenv
import isodate
import db
from commentary_patterns import score_commentary, LANG_FLAGS
from discovery_keywords import DISCOVERY_KEYWORDS, KEYWORD_CATEGORIES, KEYWORD_LANG_NAMES

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
# Kraje do przeszukania trendów (1 jednostka API per kraj, ×5 z kategoriami)
COMMENTARY_COUNTRIES = ["US", "GB", "RU", "BR", "ES", "MX", "DE", "FR", "IT", "PL",
                        "IN", "PT", "AR", "CO", "TR", "ID", "JP", "KR", "CA", "AU", "DK", "VN"]
# Kategorie YT z których pobieramy dodatkowe trendy (1 jednostka per kraj×kategoria)
# 24=Entertainment, 22=People&Blogs, 28=Science&Tech, 26=Howto&Style
COMMENTARY_EXTRA_CATEGORIES = ["24", "22", "28", "26"]
# Minimalny wynik żeby short trafił do wyników (łatwy do podkręcenia)
COMMENTARY_MIN_SCORE = 10
# TTL cache commentary — droższe przez wiele krajów, więc dłuższe (2h)
COMMENTARY_CACHE_TTL = 7200

# Parametry endpointu AI (Gemini)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Alias zawsze wskazujący aktualny model Lite (tani, lekki) — potwierdzony przez ListModels
GEMINI_MODEL = "gemini-flash-lite-latest"
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
@app.route('/saved')
@app.route('/hashtagi')
@app.route('/algrow')
@app.route('/odkrywaj')
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


def _country_flag(code):
    """Kod ISO (np. 'PL') -> emoji flagi. Pusty/None -> ''."""
    code = (code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return chr(0x1F1E6 + ord(code[0]) - 65) + chr(0x1F1E6 + ord(code[1]) - 65)


# Fallback: wykryty język -> najbardziej prawdopodobny kraj (gdy kanał nie podaje kraju)
_LANG_TO_COUNTRY = {
    "en": "US", "pl": "PL", "ru": "RU", "es": "ES", "pt": "BR", "de": "DE",
    "fr": "FR", "it": "IT", "uk": "UA", "tr": "TR", "id": "ID", "hi": "IN",
    "ar": "SA",
}


def _enrich_with_country(videos):
    """Dodaje 'country' i 'country_flag' do filmów na podstawie kraju kanału.

    Kraj kanału trzymamy na stałe w DB (channel_countries) — brakujące pobieramy
    jednorazowo przez channels.list (1 jednostka quota per 50 kanałów).
    Zwraca zużytą quota.
    """
    channel_ids = list({v.get("channel_id") for v in videos if v.get("channel_id")})
    if not channel_ids:
        return 0

    try:
        known = db.get_channel_countries(channel_ids)
    except Exception as e:
        print(f"⚠️ channel_countries read: {e}")
        known = {}

    missing = [ch for ch in channel_ids if ch not in known]
    quota = 0
    if missing and API_KEYS:
        fetched = {}
        key_idx = [0]
        for batch in _chunk(missing, 50):
            ids = ",".join(batch)
            attempts = 0
            while attempts < len(API_KEYS):
                try:
                    url = ("https://www.googleapis.com/youtube/v3/channels"
                           f"?part=snippet&id={ids}&maxResults=50"
                           f"&key={API_KEYS[key_idx[0] % len(API_KEYS)]}")
                    res = requests.get(url, timeout=10).json()
                    quota += 1
                    if 'error' in res:
                        key_idx[0] += 1
                        attempts += 1
                        continue
                    for item in res.get("items", []):
                        fetched[item["id"]] = item["snippet"].get("country")
                    break
                except Exception as e:
                    print(f"Błąd channels country batch: {e}")
                    key_idx[0] += 1
                    attempts += 1
                    time.sleep(0.3)
        # Kanały bez odpowiedzi też zapisz jako None, żeby nie pytać w kółko
        for ch in batchless_missing(missing, fetched):
            fetched.setdefault(ch, None)
        try:
            db.save_channel_countries(fetched)
        except Exception as e:
            print(f"⚠️ channel_countries write: {e}")
        known.update(fetched)

    for v in videos:
        country = known.get(v.get("channel_id"))
        v["country_guessed"] = False
        if not country:
            # Fallback: szacuj kraj po języku tytułu/opisu
            _, lang = score_commentary(v.get("title", ""), v.get("description", ""),
                                       v.get("duration_seconds", 0), False)
            country = _LANG_TO_COUNTRY.get(lang)
            if country:
                v["country_guessed"] = True
        v["country"] = country or ""
        v["country_flag"] = _country_flag(country)
    return quota


def batchless_missing(missing, fetched):
    """Kanały o które pytaliśmy, ale API nic nie zwróciło (np. usunięte)."""
    return [ch for ch in missing if ch not in fetched]


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
    quota_used += _enrich_with_country(hits)
    payload = {"videos": hits, "quota_used": quota_used, "cached": False}
    _cache["hity"] = (now, payload)
    try:
        db.upsert_video_metadata(all_videos, "hity")
    except Exception as e:
        print(f"⚠️ upsert_video_metadata (hity): {e}")
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
    quota_used += _enrich_with_country(videos)
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
    quota_used += _enrich_with_country(result)
    payload = {"videos": result, "quota_used": quota_used, "cached": False}
    _cache["commentary"] = (now, payload)
    try:
        db.upsert_video_metadata(pool, "commentary")
    except Exception as e:
        print(f"⚠️ upsert_video_metadata (commentary): {e}")
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
    quota_used += _enrich_with_country(videos)

    payload = {"quota_used": quota_used, "videos": videos, "cached": False}
    _cache[cache_key] = (now, payload)
    try:
        db.upsert_video_metadata(videos, cache_key)
    except Exception as e:
        print(f"⚠️ upsert_video_metadata (feed): {e}")
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


@app.route('/api/ai/refresh', methods=['POST'])
def ai_refresh():
    """Czyści cache endpointu AI, żeby następne GET /api/ai pobrało świeże dane."""
    _cache.pop("ai", None)
    return jsonify({"ok": True})


@app.route('/api/ai/models')
def list_gemini_models():
    """Diagnostyka — zwraca modele dostępne dla klucza, które wspierają generateContent."""
    if not GEMINI_API_KEY:
        return jsonify({"error": "Brak GEMINI_API_KEY"})
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
        res = requests.get(url, timeout=15).json()
        models = [
            {"name": m.get("name"), "displayName": m.get("displayName")}
            for m in res.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        return jsonify({"current_model": GEMINI_MODEL, "available": models})
    except Exception as e:
        return jsonify({"error": str(e)})


def _rate_with_gemini(candidates):
    """Wysyła kandydatów do Gemini w paczkach po 50. Zwraca dict video_id -> rating.

    Retry do 3 razy per paczka przy 503 (model przeciążony), z rosnącym opóźnieniem.
    """
    if not GEMINI_API_KEY or not candidates:
        return {}

    results = {}
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")

    for batch_idx, batch in enumerate(_chunk(candidates, 25)):
        if batch_idx > 0:
            time.sleep(1)
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
    quota_used += _enrich_with_country(result)

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
    try:
        db.upsert_video_metadata(pool, "ai")
    except Exception as e:
        print(f"⚠️ upsert_video_metadata (ai): {e}")
    return jsonify(payload)


# ---------- Zapisane shorty ----------

# ---------- Parametry Algrow (API do odkrywania; search darmowy w planie, limit/h) ----------
ALGROW_API_KEY = os.getenv("ALGROW_API_KEY")
# Bazowy URL REST (docs: https://algrow.online/api/docs)
ALGROW_API_BASE = os.getenv("ALGROW_API_BASE", "https://api.algrow.online")
# Ścieżki endpointów (potwierdzone w docs)
ALGROW_EP_VIRAL_VIDEOS = "/api/viral-videos/search"
ALGROW_EP_SHORTS_CHANNELS = "/api/channels/search"   # wymaga q
ALGROW_EP_CHANNEL_TRENDS = "/api/channel-trends"     # browse bez q
# Mocny cache — API kosztuje kredyty (6h)
ALGROW_CACHE_TTL = 21600
# Domyślne progi wyszukiwania (edytowalne)
ALGROW_MIN_OUTLIER = 3.0        # film zrobił >= 3x normy swojego kanału
ALGROW_UPLOADED_DAYS = 7        # z ostatnich 7 dni
ALGROW_MIN_VIEWS = 10000        # min. wyświetleń filmu
ALGROW_CH_MAX_SUBS = 10000      # sekcja B: kanały do 10k subów
ALGROW_CH_MAX_AGE = 90          # sekcja B: kanały młodsze niż 90 dni
ALGROW_TIMEOUT = 60      # pierwsze zapytanie bywa wolne
ALGROW_RETRIES = 2       # ponowienia przy timeout

# ---------- Parametry podstrony Odkrywaj ----------
ALGROW_EP_SEARCH = "/api/search"          # cały YouTube (sync, bez outlier_score)
ODKRYWAJ_CACHE_TTL = 21600                # 6h — cache per (tryb, fraza, filtry)
ODKRYWAJ_MAX_PHRASES = 5                  # max fraz na jedno wyszukanie multi
ODKRYWAJ_DAYS = 7                         # domyślny okres (dni)
ODKRYWAJ_MIN_OUTLIER = 3.0                # tryb B: min outlier_score
ODKRYWAJ_MAX_SUBS = 50000                 # tryb B: łapanie małych kanałów
ODKRYWAJ_MAX_DUR = 180                    # tryb A: tylko shorty (sekundy)

# ---------- Parametry endpointu Hashtagi ----------
# Tagi generyczne — dominują, zaśmiecają; edytowalna lista
HASHTAGI_GENERYCZNE = frozenset({
    "shorts", "short", "viral", "fyp", "foryou", "foryoupage", "trending",
    "youtubeshorts", "video", "reels", "tiktok", "youtube", "shortvideo",
    "shortsvideo", "viralvideo", "virals", "explore", "trend",
    "subscribe", "like", "share", "follow", "comment", "yt", "ytshorts",
    "новое", "хайп", "repost",
})
# TTL cache wyników hashtagów (1h — analiza jest szybka, nie zużywa quota)
HASHTAGI_CACHE_TTL = 3600
# Regex do wyciągania hashtagów z tekstu (obsługuje unicode: ą,ę,ü,ñ, cyrylicę)
_HASHTAG_RE = re.compile(r'#(\w+)', re.UNICODE)

VALID_STATUSES = {"todo", "doing", "done"}


@app.route('/api/saved', methods=['GET'])
def api_saved_list():
    status = request.args.get("status") or None
    if status and status not in VALID_STATUSES:
        return jsonify({"ok": False, "error": "Nieprawidłowy status"}), 400
    try:
        rows = db.get_saved_shorts(status)
        for r in rows:
            if r.get("saved_at"):
                r["saved_at"] = r["saved_at"].isoformat()
        return jsonify({"shorts": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/saved/ids', methods=['GET'])
def api_saved_ids():
    try:
        return jsonify({"ids": db.get_saved_ids()})
    except Exception as e:
        return jsonify({"ids": [], "error": str(e)}), 500


@app.route('/api/saved', methods=['POST'])
def api_save_short():
    data = request.get_json(silent=True) or {}
    video_id = (data.get("video_id") or "").strip()
    if not video_id:
        return jsonify({"ok": False, "error": "Brak video_id"}), 400
    try:
        added, row_id = db.save_short(data)
        return jsonify({"ok": True, "added": added, "id": row_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/saved/<video_id>', methods=['DELETE'])
def api_delete_short(video_id):
    try:
        ok = db.delete_short(video_id)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/saved/<video_id>', methods=['PATCH'])
def api_patch_short(video_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    note = data.get("note")
    if status is not None and status not in VALID_STATUSES:
        return jsonify({"ok": False, "error": "Nieprawidłowy status"}), 400
    try:
        ok = db.patch_short(video_id, status=status, note=note)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------- Endpointy Algrow (płatne odkrywanie) ----------

def _algrow_call(endpoint, params):
    """Wywołuje REST API Algrow (GET + query params, Bearer). Zwraca (data, error)."""
    if not ALGROW_API_KEY:
        return None, "Skonfiguruj ALGROW_API_KEY w zmiennych środowiskowych"
    url = ALGROW_API_BASE.rstrip("/") + endpoint
    last_err = None
    for attempt in range(ALGROW_RETRIES):
        try:
            res = requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {ALGROW_API_KEY}"},
                timeout=ALGROW_TIMEOUT,
            )
            if res.status_code == 401:
                return None, "Nieprawidłowy klucz ALGROW_API_KEY"
            if res.status_code == 403:
                return None, "Ten endpoint wymaga wyższego planu Algrow"
            if res.status_code == 429:
                return None, "Limit zapytań Algrow (na godzinę) — spróbuj później"
            if res.status_code >= 400:
                return None, f"Algrow HTTP {res.status_code}: {res.text[:200]}"
            print(f"💳 Algrow: GET {endpoint} OK ({len(res.text)} B)")
            return res.json(), None
        except requests.exceptions.Timeout:
            last_err = f"Algrow nie odpowiedział w {ALGROW_TIMEOUT}s"
            print(f"⚠️ Algrow timeout (próba {attempt+1}/{ALGROW_RETRIES}): {endpoint}")
        except Exception as e:
            return None, f"Błąd połączenia z Algrow: {e}"
    return None, f"{last_err} — spróbuj ponownie (kolejne zapytania są zwykle szybsze)"


def _algrow_video_to_card(item):
    """Mapuje wynik /api/viral-videos/search na format karty (jak _parse_video_details)."""
    vid = item.get("video_id") or ""
    dur_s = int(item.get("duration") or 0)
    mm, ss = dur_s // 60, dur_s % 60
    return {
        "id": vid,
        "title": item.get("title") or "",
        "channel": item.get("channel_name") or "",
        "channel_id": item.get("channel_id") or "",
        "published": item.get("upload_date") or "",
        "url": item.get("url") or f"https://www.youtube.com/shorts/{vid}",
        "thumbnail": item.get("thumbnail_url") or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
        "views": int(item.get("view_count") or 0),
        "likes": 0,
        "comment_count": 0,
        "duration": f"{mm}:{str(ss).zfill(2)}" if dur_s else "",
        "duration_seconds": dur_s,
        "description": "",
        "tags": [],
        "category_id": "",
        "outlier_score": round(float(item.get("outlier_score") or 0), 1),
        "channel_subs": int(item.get("subscriber_count") or 0),
        "views_24h": int(item.get("view_increase_24h") or 0),
    }


@app.route('/api/algrow/videos', methods=['GET', 'POST'])
def algrow_videos():
    """GET = tylko cache (bez kredytów). POST = nowe wyszukiwanie (płatne)."""
    now = time.time()

    if request.method == 'GET':
        cached = _cache.get("algrow_videos")
        if cached and now - cached[0] < ALGROW_CACHE_TTL:
            payload = dict(cached[1])
            payload["cached"] = True
            return jsonify(payload)
        return jsonify({"videos": [], "cached": False, "empty": True,
                        "configured": bool(ALGROW_API_KEY)})

    body = request.get_json(silent=True) or {}
    params = {
        "content_type": "shorts",
        "sort_by": "outlier_score",
        "min_outlier_score": float(body.get("min_outlier_score") or ALGROW_MIN_OUTLIER),
        "max_upload_date": int(body.get("uploaded_within_days") or ALGROW_UPLOADED_DAYS),
        "min_video_views": int(body.get("min_video_views") or ALGROW_MIN_VIEWS),
        "per_page": 50,
    }
    if body.get("max_subs"):
        params["max_subs"] = int(body["max_subs"])
    if body.get("search"):
        params["q"] = str(body["search"])[:200]

    data, err = _algrow_call(ALGROW_EP_VIRAL_VIDEOS, params)
    if err:
        return jsonify({"videos": [], "error": err, "configured": bool(ALGROW_API_KEY)}), 200

    raw_items = data.get("videos") or []
    videos = [_algrow_video_to_card(i) for i in raw_items]
    videos = [v for v in videos if v["id"]]

    # Ocena commentary przez istniejący mechanizm Gemini (cache w ai_ratings)
    ids = [v["id"] for v in videos]
    try:
        cached_ratings = db.get_ai_ratings(ids)
    except Exception as e:
        print(f"⚠️ DB ai_ratings read (algrow): {e}")
        cached_ratings = {}
    uncached = [v for v in videos if v["id"] not in cached_ratings]
    new_ratings = {}
    if uncached and GEMINI_API_KEY:
        new_ratings = _rate_with_gemini(uncached)
        try:
            db.save_ai_ratings([{"video_id": vid, **r} for vid, r in new_ratings.items()])
        except Exception as e:
            print(f"⚠️ DB ai_ratings write (algrow): {e}")
    all_ratings = {**cached_ratings, **new_ratings}

    for v in videos:
        r = all_ratings.get(v["id"])
        v["ai_is_commentary"] = bool(r["is_commentary"]) if r else None
        v["ai_confidence"] = r["confidence"] if r else None
        v["ai_reason"] = r["reason"] if r else None

    # Język + flaga kraju (fallback po języku — zero quota)
    for v in videos:
        _, lang = score_commentary(v["title"], v.get("description", ""),
                                   v.get("duration_seconds", 0), False)
        v["lang"] = lang
        v["lang_flag"] = LANG_FLAGS.get(lang, "🌐")
    _enrich_with_country(videos)

    payload = {
        "videos": videos,
        "cached": False,
        "gemini_new": len(new_ratings),
        "gemini_cached": len(ids) - len(uncached),
        "params": params,
    }
    _cache["algrow_videos"] = (now, payload)
    print(f"🔎 Algrow videos: {len(videos)} wyników, Gemini nowych: {len(new_ratings)}")
    return jsonify(payload)


@app.route('/api/algrow/channels', methods=['GET', 'POST'])
def algrow_channels():
    """GET = tylko cache. POST = nowe wyszukiwanie kanałów (płatne)."""
    now = time.time()

    if request.method == 'GET':
        cached = _cache.get("algrow_channels")
        if cached and now - cached[0] < ALGROW_CACHE_TTL:
            payload = dict(cached[1])
            payload["cached"] = True
            return jsonify(payload)
        return jsonify({"channels": [], "cached": False, "empty": True,
                        "configured": bool(ALGROW_API_KEY)})

    body = request.get_json(silent=True) or {}
    q = str(body.get("q") or "").strip()[:200]
    max_subs = int(body.get("max_subs") or ALGROW_CH_MAX_SUBS)
    max_age = int(body.get("max_age") or ALGROW_CH_MAX_AGE)

    if q:
        # Similarity/keyword search — q wymagane przez /api/channels/search
        params = {
            "q": q,
            "max_subs": max_subs,
            "max_age": max_age,
            "sort": body.get("sort") or "views_24h_desc",
            "per_page": 50,
        }
        if body.get("min_views_24h"):
            params["min_views_24h"] = int(body["min_views_24h"])
        if body.get("languages"):
            params["languages"] = body["languages"]
        data, err = _algrow_call(ALGROW_EP_SHORTS_CHANNELS, params)
    else:
        # Browse bez frazy — leaderboard wzrostów /api/channel-trends
        params = {
            "content_type": "shorts",
            "metric": "views",
            "max_subs": max_subs,
            "max_age": max_age,
            "per_page": 50,
        }
        if body.get("languages"):
            params["languages"] = body["languages"]
        data, err = _algrow_call(ALGROW_EP_CHANNEL_TRENDS, params)

    if err:
        return jsonify({"channels": [], "error": err, "configured": bool(ALGROW_API_KEY)}), 200

    raw = data.get("channels") or []
    channels = []
    for c in raw:
        ch_id = c.get("channel_id") or ""
        channels.append({
            "channel_id": ch_id,
            "title": c.get("channel_title") or c.get("title") or "",
            "url": f"https://www.youtube.com/channel/{ch_id}" if ch_id else "",
            "thumbnail": c.get("thumbnail_url") or "",
            "subs": int(c.get("subscriber_count") or 0),
            "views_24h": int(c.get("view_increase_24h") or 0),
            "age_days": int(c.get("channel_age_days") or 0),
            "language": c.get("primary_language") or "",
            "avg_views": int(c.get("avg_views_per_video") or 0),
            "total_videos": int(c.get("total_videos") or 0),
            "description": "",
        })
    channels = [c for c in channels if c["channel_id"]]

    payload = {"channels": channels, "cached": False, "params": params}
    _cache["algrow_channels"] = (now, payload)
    print(f"🔎 Algrow channels: {len(channels)} wyników")
    return jsonify(payload)


@app.route('/api/algrow/known-channels')
def algrow_known_channels():
    """Zwraca channel_id już śledzone w bazie — do badge 'już śledzę'."""
    try:
        return jsonify({"ids": db.get_all_channels()})
    except Exception as e:
        return jsonify({"ids": [], "error": str(e)}), 500


def _gemini_annotate(videos):
    """Ocenia filmy Gemini z cache w ai_ratings (reużycie mechanizmu z /ai).

    Dodaje ai_is_commentary / ai_confidence / ai_reason do każdego filmu.
    Zwraca (n_new, n_cached).
    """
    ids = [v["id"] for v in videos]
    try:
        cached_ratings = db.get_ai_ratings(ids)
    except Exception as e:
        print(f"⚠️ DB ai_ratings read: {e}")
        cached_ratings = {}
    uncached = [v for v in videos if v["id"] not in cached_ratings]
    new_ratings = {}
    if uncached and GEMINI_API_KEY:
        new_ratings = _rate_with_gemini(uncached)
        try:
            db.save_ai_ratings([{"video_id": vid, **r} for vid, r in new_ratings.items()])
        except Exception as e:
            print(f"⚠️ DB ai_ratings write: {e}")
    all_ratings = {**cached_ratings, **new_ratings}
    for v in videos:
        r = all_ratings.get(v["id"])
        v["ai_is_commentary"] = bool(r["is_commentary"]) if r else None
        v["ai_confidence"] = r["confidence"] if r else None
        v["ai_reason"] = r["reason"] if r else None
    return len(new_ratings), len(ids) - len(uncached)


# ---------- Endpointy Odkrywaj (Algrow: cały YouTube + virale) ----------

@app.route('/api/odkrywaj/keywords')
def odkrywaj_keywords():
    """Słownik fraz-hooków per język/kategoria dla UI."""
    return jsonify({
        "keywords": DISCOVERY_KEYWORDS,
        "categories": KEYWORD_CATEGORIES,
        "lang_names": KEYWORD_LANG_NAMES,
    })


# Cache weryfikacji "czy to naprawdę short" (per proces; film nie zmienia typu)
_shorts_verify_cache = {}


def _is_real_short(video_id):
    """True jeśli film jest prawdziwym shortem.

    YouTube przekierowuje /shorts/ID na /watch dla zwykłych filmów —
    status 200 = short, 30x = longform. Wynik cache'owany na stałe.
    """
    if video_id in _shorts_verify_cache:
        return _shorts_verify_cache[video_id]
    try:
        res = requests.head(
            f"https://www.youtube.com/shorts/{video_id}",
            allow_redirects=False, timeout=5,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        is_short = res.status_code == 200
    except Exception:
        is_short = True  # przy błędzie nie wycinaj — lepiej pokazać za dużo niż zgubić
    _shorts_verify_cache[video_id] = is_short
    return is_short


def _odkrywaj_search_yt(phrase, days):
    """Tryb A: GET /api/search — cały YouTube, filtrowanie do shortów po stronie serwera.

    Uwaga: przy published_within_days Algrow ignoruje duration=short (zweryfikowane),
    więc dodatkowo odsiewamy po duration_seconds ORAZ weryfikujemy przekierowanie
    /shorts/ID (łapie 1-3 min zwykłe filmy, których sam czas trwania nie odsieje).
    """
    params = {
        "q": phrase,
        "type": "video",
        "sort_by": "view_count",
        "published_within_days": days,
        "duration": "short",
        "limit": 50,
    }
    data, err = _algrow_call(ALGROW_EP_SEARCH, params)
    if err:
        return None, err
    videos = []
    for item in data.get("results") or []:
        if item.get("type") != "video":
            continue
        dur_s = int(item.get("duration_seconds") or 0)
        if dur_s == 0 or dur_s > ODKRYWAJ_MAX_DUR:
            continue
        vid = item.get("video_id") or ""
        if not _is_real_short(vid):
            continue
        mm, ss = dur_s // 60, dur_s % 60
        videos.append({
            "id": vid,
            "title": item.get("title") or "",
            "channel": item.get("channel_name") or "",
            "channel_id": item.get("channel_id") or "",
            "published": "",
            "published_text": item.get("published_text") or "",
            "url": f"https://www.youtube.com/shorts/{vid}",
            "thumbnail": item.get("thumbnail_url") or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            "views": int(item.get("view_count") or 0),
            "likes": 0,
            "comment_count": 0,
            "duration": f"{mm}:{str(ss).zfill(2)}",
            "duration_seconds": dur_s,
            "description": (item.get("description_snippet") or "")[:500],
            "tags": [],
            "category_id": "",
            "outlier_score": None,
            "channel_subs": 0,
            "phrase": phrase,
        })
    return videos, None


def _odkrywaj_search_viral(phrase, days, min_outlier, max_subs):
    """Tryb B: GET /api/viral-videos/search — virale z outlier_score."""
    params = {
        "q": phrase,
        "content_type": "shorts",
        "sort_by": "outlier_score",
        "min_outlier_score": min_outlier,
        "max_upload_date": days,
        "max_subs": max_subs,
        "per_page": 50,
        "include_youtube": "true",  # rozszerza poza curated bazę Algrow
    }
    data, err = _algrow_call(ALGROW_EP_VIRAL_VIDEOS, params)
    if err:
        return None, err
    videos = [_algrow_video_to_card(i) for i in (data.get("videos") or [])]
    for v in videos:
        v["phrase"] = phrase
    return [v for v in videos if v["id"]], None


@app.route('/api/odkrywaj', methods=['GET', 'POST'])
def odkrywaj_search():
    """GET = ostatni wynik z cache (zero zapytań Algrow). POST = nowe wyszukiwanie."""
    now = time.time()

    if request.method == 'GET':
        cached = _cache.get("odkrywaj_last")
        if cached and now - cached[0] < ODKRYWAJ_CACHE_TTL:
            payload = dict(cached[1])
            payload["cached"] = True
            return jsonify(payload)
        return jsonify({"videos": [], "cached": False, "empty": True,
                        "configured": bool(ALGROW_API_KEY)})

    body = request.get_json(silent=True) or {}
    mode = body.get("mode") if body.get("mode") in ("yt", "viral") else "viral"
    phrases = [str(p).strip()[:200] for p in (body.get("phrases") or []) if str(p).strip()]
    phrases = phrases[:ODKRYWAJ_MAX_PHRASES]
    if not phrases:
        return jsonify({"videos": [], "error": "Podaj przynajmniej jedną frazę"}), 200

    days = int(body.get("days") or ODKRYWAJ_DAYS)
    min_outlier = float(body.get("min_outlier") or ODKRYWAJ_MIN_OUTLIER)
    max_subs = int(body.get("max_subs") or ODKRYWAJ_MAX_SUBS)

    # Sekwencyjnie (limit zapytań/h i brak równoległych jobów), z cache per fraza+filtry
    merged, seen, errors, api_calls = [], set(), [], 0
    for phrase in phrases:
        cache_key = f"odkrywaj_{mode}_{phrase}_{days}_{min_outlier}_{max_subs}"
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < ODKRYWAJ_CACHE_TTL:
            videos = cached[1]
        else:
            if mode == "yt":
                videos, err = _odkrywaj_search_yt(phrase, days)
            else:
                videos, err = _odkrywaj_search_viral(phrase, days, min_outlier, max_subs)
            api_calls += 1
            if err:
                errors.append(f"„{phrase}”: {err}")
                continue
            _cache[cache_key] = (now, videos)
        for v in videos:
            if v["id"] not in seen:
                seen.add(v["id"])
                merged.append(v)

    if not merged and errors:
        return jsonify({"videos": [], "error": " | ".join(errors),
                        "configured": bool(ALGROW_API_KEY)}), 200

    # Ocena commentary przez istniejący mechanizm Gemini (cache w ai_ratings)
    n_new, n_cached = _gemini_annotate(merged)

    # Język + flaga kraju (fallback po języku)
    for v in merged:
        _, lang = score_commentary(v["title"], v.get("description", ""),
                                   v.get("duration_seconds", 0), False)
        v["lang"] = lang
        v["lang_flag"] = LANG_FLAGS.get(lang, "🌐")
    _enrich_with_country(merged)

    merged.sort(key=lambda v: (v.get("outlier_score") or 0, v["views"]), reverse=True)

    payload = {
        "videos": merged,
        "cached": False,
        "mode": mode,
        "phrases": phrases,
        "api_calls": api_calls,
        "gemini_new": n_new,
        "gemini_cached": n_cached,
        "errors": errors,
    }
    _cache["odkrywaj_last"] = (now, payload)
    print(f"🌐 Odkrywaj [{mode}]: {len(merged)} wyników z {len(phrases)} fraz "
          f"({api_calls} zapytań Algrow, Gemini nowych: {n_new})")
    return jsonify(payload)


# ---------- Endpoint Hashtagi ----------

@app.route('/api/hashtagi')
def get_hashtagi():
    sort_by = request.args.get("sort", "viral_score")
    if sort_by not in ("count", "reach", "viral_score", "channels"):
        sort_by = "viral_score"
    min_count = max(1, int(request.args.get("min_count", 2) or 2))
    source_filter = request.args.get("source", "")   # "hashtag" / "tag" / ""
    pula_filter = request.args.get("pula", "")        # "commentary" / "ai" / "hity" / ""

    cache_key = f"hashtagi_{sort_by}_{min_count}_{source_filter}_{pula_filter}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < HASHTAGI_CACHE_TTL:
        payload = dict(cached[1])
        payload["cached"] = True
        return jsonify(payload)

    try:
        rows = db.get_all_video_metadata()
    except Exception as e:
        return jsonify({"tags": [], "error": str(e), "cached": False}), 500

    if pula_filter:
        rows = [r for r in rows if r.get("source") == pula_filter]

    from collections import defaultdict

    # term_lower -> {display_variants, hashtag_vids, tag_vids, channels}
    term_data = {}

    def _get_td(lower):
        if lower not in term_data:
            term_data[lower] = {
                "dv": defaultdict(int),  # display variant -> count
                "h": {},                  # video_id -> views (hashtag source)
                "t": {},                  # video_id -> views (tag source)
                "ch": set(),
            }
        return term_data[lower]

    for row in rows:
        vid_id = row["video_id"]
        views = int(row.get("views") or 0)
        channel_id = row.get("channel_id") or ""
        text = (row.get("title") or "") + " " + (row.get("description") or "")

        # Źródło 1: hashtagi z title+description
        for m in _HASHTAG_RE.finditer(text):
            raw = m.group(1)
            lower = raw.lower()
            td = _get_td(lower)
            td["dv"][raw] += 1
            td["h"][vid_id] = views
            td["ch"].add(channel_id)

        # Źródło 2: tagi-metadane
        tags_raw = row.get("tags")
        if tags_raw:
            try:
                tags_list = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
            except Exception:
                tags_list = []
            for tag in tags_list:
                raw = (tag or "").strip()
                if not raw:
                    continue
                lower = raw.lower()
                td = _get_td(lower)
                td["dv"][raw] += 1
                td["t"][vid_id] = views
                td["ch"].add(channel_id)

    # Złóż wyniki
    results = []
    for lower, td in term_data.items():
        h_count = len(td["h"])
        t_count = len(td["t"])

        if h_count > 0 and t_count > 0:
            src = "oba"
        elif h_count > 0:
            src = "hashtag"
        else:
            src = "tag"

        if source_filter and src != source_filter:
            continue

        total_count = len({**td["h"], **td["t"]})
        if total_count < min_count:
            continue

        display = max(td["dv"], key=td["dv"].__getitem__)
        all_views = list({**td["h"], **td["t"]}.values())
        reach = sum(all_views)
        med_views = int(statistics.median(all_views)) if all_views else 0

        results.append({
            "term": lower,
            "display": display,
            "source": src,
            "count": total_count,
            "hashtag_count": h_count,
            "tag_count": t_count,
            "reach": reach,
            "median_views": med_views,
            "viral_score": med_views,
            "channels": len(td["ch"]),
            "is_generic": lower in HASHTAGI_GENERYCZNE,
        })

    results.sort(key=lambda r: r[sort_by], reverse=True)

    payload = {
        "tags": results,
        "total_videos": len(rows),
        "cached": False,
        "generyczne": sorted(HASHTAGI_GENERYCZNE),
    }
    _cache[cache_key] = (now, payload)
    return jsonify(payload)


@app.route('/api/hashtagi/videos')
def get_hashtagi_videos():
    term = (request.args.get("term") or "").lower()
    source_filter = request.args.get("source", "")  # "hashtag" / "tag" / ""
    if not term:
        return jsonify({"videos": [], "total": 0})

    try:
        rows = db.get_all_video_metadata()
    except Exception as e:
        return jsonify({"videos": [], "error": str(e)}), 500

    matching = []
    for row in rows:
        text = (row.get("title") or "") + " " + (row.get("description") or "")
        found_hashtag = any(m.group(1).lower() == term for m in _HASHTAG_RE.finditer(text))

        found_tag = False
        tags_raw = row.get("tags")
        if tags_raw:
            try:
                tags_list = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
                found_tag = any((t or "").lower().strip() == term for t in tags_list)
            except Exception:
                pass

        if source_filter == "hashtag" and not found_hashtag:
            continue
        if source_filter == "tag" and not found_tag:
            continue
        if not source_filter and not (found_hashtag or found_tag):
            continue

        matching.append({
            "id": row["video_id"],
            "title": row.get("title") or "",
            "channel": row.get("channel") or "",
            "channel_id": row.get("channel_id") or "",
            "thumbnail": row.get("thumbnail") or f"https://i.ytimg.com/vi/{row['video_id']}/mqdefault.jpg",
            "url": f"https://www.youtube.com/shorts/{row['video_id']}",
            "views": int(row.get("views") or 0),
            "likes": int(row.get("likes") or 0),
            "published": row.get("published") or "",
            "duration": row.get("duration") or "",
            "source": row.get("source") or "",
        })

    matching.sort(key=lambda v: v["views"], reverse=True)
    return jsonify({"videos": matching[:60], "total": len(matching)})


if __name__ == '__main__':
    app.run()
