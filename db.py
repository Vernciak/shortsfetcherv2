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
    """Tworzy tabelę kanałów jeśli nie istnieje."""
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


def list_channels(category):
    """Zwraca pełne rekordy (do wyświetlenia w panelu)."""
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            "SELECT id, channel_id, title, added_at FROM channels "
            "WHERE category = %s ORDER BY added_at DESC",
            (category,),
        )
        return [dict(r) for r in cur.fetchall()]


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
