"""Wielojęzyczne wzorce scoringowe dla wykrywania treści commentary/reakcji/edutainment."""

# Każda kategoria to dict: język -> lista wzorców (case-insensitive, dopasowanie substring).
# Trafienie w daną kategorię daje +10 punktów do commentary_score (tylko raz per kategoria).

QUESTION_HOOKS = {
    "en": ["why", "how to", "what happens", "did you know", "what if", "watch what", "when you", "can you explain"],
    "pl": ["dlaczego", "jak to", "co się stanie", "czy wiesz", "co jeśli", "spójrz co", "jak możliwe", "jak działa"],
    "ru": ["почему", "как это", "что будет", "а вы знали", "что если", "посмотри что", "знаешь ли", "как работает"],
    "es": ["por qué", "cómo", "qué pasa", "sabías que", "qué pasaría", "mira lo que", "sabes que", "cómo funciona"],
    "pt": ["por que", "como fazer", "o que acontece", "você sabia", "e se", "veja o que", "sabia que", "como funciona"],
    "de": ["warum", "wie geht", "was passiert", "wusstest du", "was wäre wenn", "schau was", "hast du gewusst", "wie funktioniert"],
    "fr": ["pourquoi", "comment faire", "que se passe", "saviez-vous", "et si", "regardez ce", "savais-tu", "comment ça marche"],
    "it": ["perché", "come fare", "cosa succede", "lo sapevi", "e se", "guarda cosa", "sapevi che", "come funziona"],
    "uk": ["чому", "як це", "що станеться", "ви знали", "що якщо", "дивись що", "чи знаєш", "як працює"],
    "tr": ["neden", "nasıl yapılır", "ne olur", "biliyor muydun", "ya eğer", "bak ne", "şunu biliyor musun", "nasıl çalışır"],
    "id": ["kenapa", "bagaimana cara", "apa yang terjadi", "tahukah kamu", "bagaimana jika", "lihat apa", "kamu tahu", "bagaimana bisa"],
    "hi": ["क्यों", "कैसे करें", "क्या होता", "क्या आप जानते", "अगर", "देखो क्या", "कैसे काम करता"],
    "ar": ["لماذا", "كيف تفعل", "ماذا يحدث", "هل تعلم", "ماذا لو", "انظر ماذا", "كيف يعمل"],
}

REACTION_COMMENTARY = {
    "en": ["reaction", "reacting", "i tried", "watch this", "you won't believe", "wait for it", "pov", "responding to", "rate this", "challenge", "my response"],
    "pl": ["reakcja", "reaguję", "próbowałem", "spójrz na to", "nie uwierzysz", "poczekaj", "pov", "reaguję na", "oceń to", "challenge", "moja odpowiedź"],
    "ru": ["реакция", "реагирую", "я попробовал", "смотри это", "не поверишь", "подожди", "пов", "реагирую на", "челлендж", "мой ответ"],
    "es": ["reacción", "reaccionando", "lo intenté", "mira esto", "no lo creerás", "espera", "pov", "respondiendo a", "reto", "mi respuesta"],
    "pt": ["reação", "reagindo", "tentei", "olha isso", "não vai acreditar", "espera", "pov", "reagindo a", "desafio", "minha resposta"],
    "de": ["reaktion", "reagiere", "ich habs versucht", "schau dir das an", "du wirst es nicht glauben", "warte", "pov", "reagiere auf", "challenge", "meine antwort"],
    "fr": ["réaction", "je réagis", "j'ai essayé", "regarde ça", "tu ne croiras pas", "attends", "pov", "réagissant à", "défi", "ma réponse"],
    "it": ["reazione", "reagisco", "ho provato", "guarda questo", "non ci crederai", "aspetta", "pov", "reagendo a", "sfida", "la mia risposta"],
    "uk": ["реакція", "реагую", "я спробував", "дивись це", "не повіриш", "зачекай", "пов", "реагую на", "челендж", "моя відповідь"],
    "tr": ["tepki", "tepki veriyorum", "denedim", "buna bak", "inanmayacaksın", "bekle", "pov", "tepki", "meydan okuma", "cevabım"],
    "id": ["reaksi", "bereaksi", "aku coba", "lihat ini", "kamu tidak akan percaya", "tunggu", "pov", "merespons", "tantangan", "responseku"],
    "hi": ["रिएक्शन", "प्रतिक्रिया", "मैंने कोशिश की", "देखो यह", "यकीन नहीं होगा", "रुको", "पीओवी", "चैलेंज"],
    "ar": ["رد فعل", "أتفاعل", "جربت", "انظر هذا", "لن تصدق", "انتظر", "بوف", "ردًا على", "تحدي"],
}

EDUTAINMENT_CURIOSITY = {
    "en": ["secret", "trick", "hack", "the best", "most dangerous", "genius", "nobody knows", "hidden", "truth about", "life hack", "surprising", "shocking", "incredible", "you should know", "exposed", "actually works", "mind blown"],
    "pl": ["sekret", "trik", "hack", "najlepszy", "najbardziej niebezpieczny", "genialne", "nikt nie wie", "ukryte", "prawda o", "ciekawostka", "zaskakujące", "niesamowite", "powinieneś wiedzieć", "okazuje się", "szokujące"],
    "ru": ["секрет", "трюк", "лайфхак", "лучший", "самый опасный", "гениально", "никто не знает", "скрытое", "правда о", "лайфхак", "удивительно", "шокирующее", "невероятно", "ты должен знать", "оказывается"],
    "es": ["secreto", "truco", "hack", "el mejor", "más peligroso", "genial", "nadie sabe", "oculto", "la verdad", "sorprendente", "increíble", "deberías saber", "resulta que", "impactante"],
    "pt": ["segredo", "truque", "hack", "o melhor", "mais perigoso", "genial", "ninguém sabe", "oculto", "a verdade", "surpreendente", "incrível", "você deveria saber", "acontece que", "chocante"],
    "de": ["geheimnis", "trick", "hack", "das beste", "gefährlichste", "genial", "niemand weiß", "versteckt", "die wahrheit", "überraschend", "unglaublich", "solltest du wissen", "stellt sich heraus", "schockierend"],
    "fr": ["secret", "astuce", "hack", "le meilleur", "le plus dangereux", "génial", "personne ne sait", "caché", "la vérité", "surprenant", "incroyable", "tu devrais savoir", "il s'avère", "choquant"],
    "it": ["segreto", "trucco", "hack", "il migliore", "più pericoloso", "geniale", "nessuno sa", "nascosto", "la verità", "sorprendente", "incredibile", "dovresti sapere", "in realtà", "sconvolgente"],
    "uk": ["секрет", "трюк", "лайфхак", "найкращий", "найнебезпечніший", "геніально", "ніхто не знає", "приховане", "правда про", "дивовижне", "неймовірно", "ти повинен знати", "виявляється"],
    "tr": ["sır", "numara", "hack", "en iyi", "en tehlikeli", "dahice", "kimse bilmez", "gizli", "gerçek", "şaşırtıcı", "inanılmaz", "bilmen gerek", "meğer", "şok edici"],
    "id": ["rahasia", "trik", "hack", "terbaik", "paling berbahaya", "jenius", "tidak ada yang tahu", "tersembunyi", "kebenaran", "mengejutkan", "luar biasa", "kamu harus tahu", "ternyata"],
    "hi": ["राज", "ट्रिक", "हैक", "सबसे अच्छा", "सबसे खतरनाक", "जीनियस", "कोई नहीं जानता", "छुपा हुआ", "सच्चाई", "चौंकाने वाला", "अविश्वसनीय", "जानना चाहिए"],
    "ar": ["سر", "حيلة", "هاك", "الأفضل", "الأكثر خطورة", "عبقري", "لا أحد يعرف", "مخفي", "الحقيقة", "مثير للدهشة", "لا يصدق", "يجب أن تعرف"],
}

ALL_CATEGORIES = [QUESTION_HOOKS, REACTION_COMMENTARY, EDUTAINMENT_CURIOSITY]
LANGUAGES = list(QUESTION_HOOKS.keys())

LANG_FLAGS = {
    "en": "🇬🇧", "pl": "🇵🇱", "ru": "🇷🇺", "es": "🇪🇸", "pt": "🇧🇷",
    "de": "🇩🇪", "fr": "🇫🇷", "it": "🇮🇹", "uk": "🇺🇦", "tr": "🇹🇷",
    "id": "🇮🇩", "hi": "🇮🇳", "ar": "🇸🇦", "?": "🌐",
}


def score_commentary(title, description, duration_seconds, is_commentary_channel):
    """Oblicza commentary_score i wykrywa język.

    Returns:
        (score: int, detected_lang: str)

    Progi (łatwe do zmiany poprzez stałe w main.py):
        - +10 za każdą kategorię z trafieniem (max 3 kategorie = 30 pkt)
        - +10 za czas trwania 15–50 s (typowy profil narracyjny)
        - +5 za czas 8–14 s
        - +3 za czas 51–120 s
        - +30 za kanał oznaczony jako commentary (silny priorytet)
    """
    text = (title + " " + (description or ""))[:3000].lower()
    lang_hits = {lang: 0 for lang in LANGUAGES}
    total_score = 0

    for cat_dict in ALL_CATEGORIES:
        matched = False
        for lang, patterns in cat_dict.items():
            for pat in patterns:
                if pat in text:
                    lang_hits[lang] += 1
                    matched = True
                    break
        if matched:
            total_score += 10

    if 15 <= duration_seconds <= 50:
        total_score += 10
    elif 8 <= duration_seconds < 15:
        total_score += 5
    elif 51 <= duration_seconds <= 120:
        total_score += 3

    if is_commentary_channel:
        total_score += 30

    best_lang = max(lang_hits, key=lambda l: lang_hits[l])
    detected = best_lang if lang_hits[best_lang] > 0 else "?"

    return total_score, detected
