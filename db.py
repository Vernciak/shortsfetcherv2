"""Warstwa bazy danych (Neon Postgres) dla kanałów."""
import os
import re
import requests
import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("Brak zmiennej DATABASE_URL")
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Tworzy tabelę kanałów jeśli nie istnieje i dodaje brakujące kolumny."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id          SERIAL PRIMARY KEY,
                category    TEXT NOT NULL,
                channel_id  TEXT NOT NULL,
                title       TEXT,
                added_at    TIMESTAMPTZ DEFAULT now(),
                UNIQUE (category, channel_id)
            );
        """)
        # Idempotentna migracja — nie psuje istniejących danych
        cur.execute("""
            ALTER TABLE channels
            ADD COLUMN IF NOT EXISTS is_commentary BOOLEAN DEFAULT false;
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_ratings (
                video_id      TEXT PRIMARY KEY,
                is_commentary BOOLEAN NOT NULL,
                confidence    INT NOT NULL,
                reason        TEXT,
                rated_at      TIMESTAMPTZ DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS saved_shorts (
                id          SERIAL PRIMARY KEY,
                video_id    TEXT UNIQUE NOT NULL,
                title       TEXT,
                channel     TEXT,
                thumbnail   TEXT,
                url         TEXT,
                views       BIGINT DEFAULT 0,
                likes       BIGINT DEFAULT 0,
                duration    TEXT,
                published   TEXT,
                status      TEXT DEFAULT 'todo',
                note        TEXT,
                saved_at    TIMESTAMPTZ DEFAULT now()
            );
        """)
        conn.commit()


def get_ai_ratings(video_ids):
    """Zwraca dict video_id -> {is_commentary, confidence, reason} dla już ocenionych."""
    if not video_ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT video_id, is_commentary, confidence, reason "
            "FROM ai_ratings WHERE video_id = ANY(%s)",
            (list(video_ids),)
        )
        return {row[0]: {"is_commentary": bool(row[1]), "confidence": row[2], "reason": row[3]}
                for row in cur.fetchall()}


def save_ai_ratings(ratings):
    """Upsertuje listę {video_id, is_commentary, confidence, reason}."""
    if not ratings:
        return
    with get_conn() as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO ai_ratings (video_id, is_commentary, confidence, reason)
               VALUES %s
               ON CONFLICT (video_id) DO UPDATE SET
                 is_commentary = EXCLUDED.is_commentary,
                 confidence    = EXCLUDED.confidence,
                 reason        = EXCLUDED.reason,
                 rated_at      = now()""",
            [(r["video_id"], r["is_commentary"], r["confidence"], r.get("reason", ""))
             for r in ratings]
        )
        conn.commit()


def is_empty():
    """True jeśli tabela channels nie ma żadnych wierszy."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM channels LIMIT 1")
        return cur.fetchone() is None


def get_channels(category):
    """Zwraca listę channel_id dla danej kategorii."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT channel_id FROM channels WHERE category = %s ORDER BY added_at",
            (category,),
        )
        return [row[0] for row in cur.fetchall()]


def get_all_channels():
    """Zwraca listę unikalnych channel_id ze wszystkich kategorii (dla viral)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT channel_id FROM channels")
        return [row[0] for row in cur.fetchall()]


def get_all_channels_with_flags():
    """Zwraca listę (channel_id, is_commentary) — unikalnych kanałów (dla commentary)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (channel_id) channel_id, is_commentary "
            "FROM channels ORDER BY channel_id, is_commentary DESC"
        )
        return [(row[0], bool(row[1])) for row in cur.fetchall()]


def list_channels(category):
    """Zwraca pełne rekordy (do wyświetlenia w panelu)."""
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            "SELECT id, channel_id, title, added_at, is_commentary FROM channels "
            "WHERE category = %s ORDER BY added_at DESC",
            (category,),
        )
        return [dict(r) for r in cur.fetchall()]


def set_channel_commentary(channel_id, value):
    """Ustawia flagę is_commentary dla wszystkich wierszy danego kanału. Zwraca liczbę zmienionych wierszy."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE channels SET is_commentary = %s WHERE channel_id = %s",
            (bool(value), channel_id),
        )
        updated = cur.rowcount
        conn.commit()
        return updated


def add_channel(category, channel_id, title=None):
    """Dodaje kanał. Zwraca True jeśli dodano, False jeśli już istniał."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO channels (category, channel_id, title) "
            "VALUES (%s, %s, %s) ON CONFLICT (category, channel_id) DO NOTHING",
            (category, channel_id, title),
        )
        added = cur.rowcount > 0
        conn.commit()
        return added


def delete_channel(row_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM channels WHERE id = %s", (row_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------- Parser linków YouTube ----------

def resolve_channel_id(link, api_key):
    """
    Z linku/ID wyciąga channel_id (UC...).
    Zwraca (channel_id, title) lub (None, błąd).
    Zużywa 0 jednostek quota dla linków /channel/UC..., 1 dla pozostałych.
    """
    link = (link or "").strip()
    if not link:
        return None, "Pusty link"

    # 1. Samo ID kanału wklejone wprost
    if re.fullmatch(r"UC[\w-]{22}", link):
        return link, None

    # 2. /channel/UC... — ID jest w URL, zero zapytań
    m = re.search(r"/channel/(UC[\w-]{22})", link)
    if m:
        return m.group(1), None

    if not api_key:
        return None, "Brak klucza API do rozpoznania linku"

    # 3. Handle: /@nazwa
    m = re.search(r"/@([\w.\-]+)", link)
    if m:
        handle = m.group(1)
        try:
            url = ("https://www.googleapis.com/youtube/v3/channels"
                   f"?part=snippet&forHandle=@{handle}&key={api_key}")
            res = requests.get(url, timeout=10).json()
            items = res.get("items", [])
            if items:
                return items[0]["id"], items[0]["snippet"]["title"]
            return None, "Nie znaleziono kanału dla tego handle"
        except Exception as e:
            return None, f"Błąd API: {e}"

    # 4. Link do filmu: watch?v=, /shorts/, youtu.be/
    video_id = None
    m = (re.search(r"[?&]v=([\w-]{11})", link)
         or re.search(r"/shorts/([\w-]{11})", link)
         or re.search(r"youtu\.be/([\w-]{11})", link))
    if m:
        video_id = m.group(1)
    if video_id:
        try:
            url = ("https://www.googleapis.com/youtube/v3/videos"
                   f"?part=snippet&id={video_id}&key={api_key}")
            res = requests.get(url, timeout=10).json()
            items = res.get("items", [])
            if items:
                ch_id = items[0]["snippet"]["channelId"]
                ch_title = items[0]["snippet"].get("channelTitle")
                return ch_id, ch_title
            return None, "Nie znaleziono filmu"
        except Exception as e:
            return None, f"Błąd API: {e}"

    return None, "Nie rozpoznano linku (wklej link do kanału lub filmu)"


# ---------- Zapisane shorty ----------

def save_short(data):
    """Zapisuje short. Zwraca (added: bool, id: int)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO saved_shorts
               (video_id, title, channel, thumbnail, url, views, likes, duration, published)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (video_id) DO NOTHING
               RETURNING id""",
            (data["video_id"], data.get("title"), data.get("channel"),
             data.get("thumbnail"), data.get("url"),
             int(data.get("views") or 0), int(data.get("likes") or 0),
             data.get("duration"), data.get("published"))
        )
        row = cur.fetchone()
        conn.commit()
        return (True, row[0]) if row else (False, None)


def delete_short(video_id):
    """Usuwa z zapisanych. Zwraca True jeśli usunięto."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM saved_shorts WHERE video_id = %s", (video_id,))
        conn.commit()
        return cur.rowcount > 0


def get_saved_shorts(status=None):
    """Zwraca listę zapisanych shortów, opcjonalnie filtrując po statusie."""
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        if status:
            cur.execute(
                "SELECT * FROM saved_shorts WHERE status = %s ORDER BY saved_at DESC",
                (status,)
            )
        else:
            cur.execute("SELECT * FROM saved_shorts ORDER BY saved_at DESC")
        return [dict(r) for r in cur.fetchall()]


def get_saved_ids():
    """Zwraca zbiór video_id wszystkich zapisanych shortów."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT video_id FROM saved_shorts")
        return [row[0] for row in cur.fetchall()]


def patch_short(video_id, status=None, note=None):
    """Zmienia status i/lub notatkę. Zwraca True jeśli rekord istnieje."""
    if status is None and note is None:
        return False
    with get_conn() as conn, conn.cursor() as cur:
        if status is not None and note is not None:
            cur.execute(
                "UPDATE saved_shorts SET status=%s, note=%s WHERE video_id=%s",
                (status, note, video_id)
            )
        elif status is not None:
            cur.execute(
                "UPDATE saved_shorts SET status=%s WHERE video_id=%s",
                (status, video_id)
            )
        else:
            cur.execute(
                "UPDATE saved_shorts SET note=%s WHERE video_id=%s",
                (note, video_id)
            )
        conn.commit()
        return cur.rowcount > 0
