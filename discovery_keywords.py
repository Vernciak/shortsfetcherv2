"""Wielojęzyczne frazy-hooki do wyszukiwania viralowych shortów (podstrona /odkrywaj).

To są QUERY do wyszukiwarki (Algrow /api/search i /api/viral-videos/search),
nie wzorce do matchowania — dobrane jako naturalne, popularne frazy per język.
"""

# Kategorie: ciekawostki (edutainment), gadzety (hacki/gadżety), reakcje (commentary)
DISCOVERY_KEYWORDS = {
    "en": {
        "ciekawostki": ["did you know", "why does", "what happens if", "how does it work", "facts you didn't know"],
        "gadzety": ["life hack", "gadget", "genius trick", "amazing invention", "smart gadget"],
        "reakcje": ["you won't believe", "watch this", "wait for it", "caught on camera", "unbelievable moment"],
    },
    "es": {
        "ciekawostki": ["sabías que", "por qué", "qué pasa si", "cómo funciona", "datos curiosos"],
        "gadzety": ["truco de vida", "gadget", "truco genial", "invento increíble", "artilugio inteligente"],
        "reakcje": ["no lo creerás", "mira esto", "espera el final", "captado en cámara", "momento increíble"],
    },
    "pt": {
        "ciekawostki": ["você sabia", "por que", "o que acontece se", "como funciona", "curiosidades"],
        "gadzety": ["truque de vida", "gadget", "truque genial", "invenção incrível", "aparelho inteligente"],
        "reakcje": ["você não vai acreditar", "olha isso", "espera o final", "flagrado na câmera", "momento inacreditável"],
    },
    "ru": {
        "ciekawostki": ["а вы знали", "почему", "что будет если", "как это работает", "интересные факты"],
        "gadzety": ["лайфхак", "гаджет", "гениальный трюк", "удивительное изобретение", "умный гаджет"],
        "reakcje": ["не поверишь", "смотри что", "дождись конца", "снято на камеру", "невероятный момент"],
    },
    "uk": {
        "ciekawostki": ["а ви знали", "чому", "що буде якщо", "як це працює", "цікаві факти"],
        "gadzety": ["лайфхак", "гаджет", "геніальний трюк", "дивовижний винахід", "розумний гаджет"],
        "reakcje": ["не повіриш", "дивись це", "дочекайся кінця", "знято на камеру", "неймовірний момент"],
    },
    "pl": {
        "ciekawostki": ["czy wiesz że", "dlaczego", "co się stanie jeśli", "jak to działa", "ciekawostki"],
        "gadzety": ["trik życiowy", "gadżet", "genialny trik", "niesamowity wynalazek", "sprytny gadżet"],
        "reakcje": ["nie uwierzysz", "zobacz to", "poczekaj do końca", "nagrane kamerą", "niesamowity moment"],
    },
    "de": {
        "ciekawostki": ["wusstest du", "warum", "was passiert wenn", "wie funktioniert das", "fakten die du nicht kennst"],
        "gadzety": ["life hack", "gadget", "genialer trick", "erstaunliche erfindung", "cleveres gadget"],
        "reakcje": ["du wirst es nicht glauben", "schau dir das an", "warte bis zum ende", "mit kamera aufgenommen", "unglaublicher moment"],
    },
    "fr": {
        "ciekawostki": ["saviez-vous", "pourquoi", "que se passe-t-il si", "comment ça marche", "faits incroyables"],
        "gadzety": ["astuce de vie", "gadget", "astuce géniale", "invention incroyable", "gadget intelligent"],
        "reakcje": ["tu ne croiras pas", "regarde ça", "attends la fin", "filmé en caméra", "moment incroyable"],
    },
    "it": {
        "ciekawostki": ["lo sapevi", "perché", "cosa succede se", "come funziona", "curiosità"],
        "gadzety": ["trucco di vita", "gadget", "trucco geniale", "invenzione incredibile", "gadget intelligente"],
        "reakcje": ["non ci crederai", "guarda questo", "aspetta la fine", "ripreso dalla telecamera", "momento incredibile"],
    },
    "tr": {
        "ciekawostki": ["biliyor muydun", "neden", "ne olur eğer", "nasıl çalışır", "ilginç bilgiler"],
        "gadzety": ["pratik bilgi", "alet", "dahiyane numara", "inanılmaz icat", "akıllı cihaz"],
        "reakcje": ["inanmayacaksın", "şuna bak", "sonunu bekle", "kameraya yakalandı", "inanılmaz an"],
    },
    "id": {
        "ciekawostki": ["tahukah kamu", "kenapa", "apa yang terjadi jika", "bagaimana cara kerjanya", "fakta menarik"],
        "gadzety": ["trik hidup", "gadget", "trik jenius", "penemuan luar biasa", "gadget pintar"],
        "reakcje": ["kamu tidak akan percaya", "lihat ini", "tunggu sampai akhir", "terekam kamera", "momen luar biasa"],
    },
    "hi": {
        "ciekawostki": ["क्या आप जानते हैं", "क्यों होता है", "क्या होगा अगर", "कैसे काम करता है", "रोचक तथ्य"],
        "gadzety": ["लाइफ हैक", "गैजेट", "जीनियस ट्रिक", "अद्भुत आविष्कार", "स्मार्ट गैजेट"],
        "reakcje": ["यकीन नहीं होगा", "यह देखो", "अंत तक देखो", "कैमरे में कैद", "अविश्वसनीय पल"],
    },
    "ar": {
        "ciekawostki": ["هل تعلم", "لماذا", "ماذا يحدث لو", "كيف يعمل", "حقائق مذهلة"],
        "gadzety": ["حيلة ذكية", "أداة", "خدعة عبقرية", "اختراع مذهل", "جهاز ذكي"],
        "reakcje": ["لن تصدق", "شاهد هذا", "انتظر النهاية", "لقطة كاميرا", "لحظة لا تصدق"],
    },
}

KEYWORD_CATEGORIES = ["ciekawostki", "gadzety", "reakcje"]

KEYWORD_LANG_NAMES = {
    "en": "🇬🇧 Angielski", "es": "🇪🇸 Hiszpański", "pt": "🇧🇷 Portugalski",
    "ru": "🇷🇺 Rosyjski", "uk": "🇺🇦 Ukraiński", "pl": "🇵🇱 Polski",
    "de": "🇩🇪 Niemiecki", "fr": "🇫🇷 Francuski", "it": "🇮🇹 Włoski",
    "tr": "🇹🇷 Turecki", "id": "🇮🇩 Indonezyjski", "hi": "🇮🇳 Hindi",
    "ar": "🇸🇦 Arabski",
}
