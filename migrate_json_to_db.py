"""
Jednorazowy skrypt: przenosi kanały z plików JSON do bazy Neon.
Uruchom RAZ lokalnie lub w Render Shell:  python migrate_json_to_db.py
Wymaga ustawionej zmiennej DATABASE_URL.
"""
import json
import os
import db

# Mapowanie plik JSON -> kategoria w bazie
FILE_TO_CATEGORY = {
    "channels.json": "main",
    "channels_nauka.json": "nauka",
    "channels_zwierzeta.json": "zwierzeta",
    "channels_fun.json": "fun",
    "channels_k1.json": "k1",
    "channels_k2.json": "k2",
    "channels_k3.json": "k3",
    "channels_k4.json": "k4",
    "channels_k5.json": "k5",
    "channels_k6.json": "k6",
}


def main():
    db.init_db()
    total_added = 0
    total_seen = 0
    for filename, category in FILE_TO_CATEGORY.items():
        if not os.path.exists(filename):
            print(f"⏭️  Pomijam {filename} (brak pliku)")
            continue
        try:
            with open(filename) as f:
                channel_ids = json.load(f)
        except Exception as e:
            print(f"⚠️  Błąd czytania {filename}: {e}")
            continue
        for ch_id in channel_ids:
            ch_id = (ch_id or "").strip()
            if not ch_id:
                continue
            total_seen += 1
            try:
                if db.add_channel(category, ch_id, None):
                    total_added += 1
            except Exception as e:
                print(f"⚠️  Błąd dodawania {ch_id} ({category}): {e}")
        print(f"✅ {filename} -> kategoria '{category}' ({len(channel_ids)} ID)")

    print(f"\n🎉 Gotowe. Przejrzano {total_seen}, dodano {total_added} nowych "
          f"(reszta to duplikaty już w bazie).")


if __name__ == "__main__":
    main()
