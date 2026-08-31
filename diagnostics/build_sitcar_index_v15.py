from __future__ import annotations

import hashlib
import html
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote


# ============================================================
# SITCAR MODEL BENCHMARK INDEX BUILDER v15
# ============================================================
# Поклади цей файл у директорію з результатами моделей і запусти:
#
#     python build_sitcar_index_v15.py
#
# Скрипт:
#   1) знаходить HTML/PDF виду MODEL_MODULE_YYYY-MM-DD_HH-MM-SS.*
#   2) для кожної model+module бере найновіший результат;
#   3) НЕ множить ORG на 3/10 — суми складаються з реальних запусків модулів;
#   4) окремо рахує фактичний та MAX-output сценарій;
#   5) сумує latency всіх реально протестованих модулів моделі;
#   6) створює компактний index.html;
#   7) технічні дані ховає в окрему панель і не розтягує таблицю;
#   8) виправляє back-link у всіх HTML на index.html в цій самій директорії.
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
PDF_RESULTS_DIR = BASE_DIR / "PDF_RESULTS"
FILES_DIR = BASE_DIR / "Files"

# Один і той самий ПДВ/податок для відображення у result HTML.
# TOTAL COST залишається чистою API-вартістю; TOTAL + 20% показується окремо.
RESULT_HTML_TAX_RATE = 0.20

# v15: result HTML вже підготовлені попереднім запуском.
# False = builder лише ЧИТАЄ result HTML і генерує index.html, нічого в них не змінює.
# Якщо колись додаси нові сирі HTML і захочеш знову автоматично виправити
# back-link / Files/ / TOTAL + 20%, тимчасово постав True.
AUTO_PATCH_RESULT_HTML = False


# ============================================================
# МОДУЛІ SITCAR
# ============================================================

MODULE_ORDER = [
    "ORG",
    "SYS",
    "STR",
    "INN",
    "TAL",
    "CUL",
    "ADA",
    "REP",
    "ARC",
]

MODULE_NAMES = {
    "ORG": "Профіль організації",
    "SYS": "Системна перевірка",
    "STR": "Стратегія",
    "INN": "Інновації",
    "TAL": "Таланти",
    "CUL": "Культура",
    "ADA": "Адаптивність",
    "REP": "Репутація",
    "ARC": "Поведінковий профіль організації",
    "FULL": "Повний результат",
    "FINAL": "Загальний висновок",
    "PRES": "Презентація",
    "PRESENTATION": "Презентація",
}

# Короткі назви тільки для компактної колонки «Результати».
# Повні назви в технічних даних залишаються без змін.
RESULT_SHORT_NAMES = {
    "ORG": "Профіль організації",
    "SYS": "Системна перевірка",
    "STR": "Стратегія",
    "INN": "Інновації",
    "TAL": "Таланти",
    "CUL": "Культура",
    "ADA": "Адаптивність",
    "REP": "Репутація",
    "ARC": "Поведінковий профіль",
    "FULL": "Повний результат",
    "FINAL": "Загальний висновок",
    "PRES": "Презентація",
    "PRESENTATION": "Презентація",
}

# У попередньому калькуляторі Безплатна версія = 3 запити.
# Тут замість ORG × 3 використовуються РЕАЛЬНІ ціни цих трьох модулів.
# Якщо склад Free-версії інший — просто зміни список нижче.
FREE_MODULES = ["ORG", "SYS", "STR"]

# Повна SITCAR-діагностика = 9 напрямків + окремий фінальний FULL API-запит.
# Тобто за повного комплекту це 10 реальних API-запусків.
FULL_MODULES = MODULE_ORDER + ["FULL"]

# Якщо пізніше з'явиться окремий файл презентації/фінального висновку,
# скрипт його знайде та додасть до Повної версії.
OPTIONAL_FINAL_CODES = ["FINAL", "PRES", "PRESENTATION"]

# FULL лишається окремо виділеним результатом у UI.
# ВАЖЛИВО: якщо існує FULL.html — це окремий реальний API-запит і він входить
# у FULL_MODULES / вартість / latency. FULL.pdf — лише альтернативний формат
# того самого результату і сам по собі вартості не додає.
DISPLAY_ONLY_CODES = ["FULL"]

ALL_CODES = MODULE_ORDER + DISPLAY_ONLY_CODES + OPTIONAL_FINAL_CODES
DISPLAY_CODES = MODULE_ORDER + DISPLAY_ONLY_CODES + OPTIONAL_FINAL_CODES


# ============================================================
# ПОДАТКИ / НАЦІНКИ
# ============================================================
# Збережено ту саму логіку, що була в попередньому index.

TAX_RATES = {
    "openai": 0.20,
    "deepseek": 0.20,
    "gemini": 0.20,
    "claude": 0.20,
    "other": 0.20,
}


# ============================================================
# ПОРЯДОК МОДЕЛЕЙ
# ============================================================

MODEL_ORDER = [
    "GPT-5.6 Sol",
    "GPT-5.6 Terra",
    "GPT-5.6 Luna",
    "GPT-5.5",
    "GPT-5.5 Pro",
    "GPT-5.4",
    "GPT-5.4 Pro",
    "GPT-5.4 Mini",
    "GPT-5.4 Nano",
    "Gemini 3.6 Flash",
    "Gemini 3.5 Flash",
    "Gemini 3.5 Flash-Lite",
    "Gemini 3.1 Pro Preview",
    "Gemini 3.1 Flash-Lite",
    "Claude Opus 5",
    "Claude Sonnet 5",
    "Claude Fable 5",
    "DeepSeek V4 Flash 0731",
    "DeepSeek V4 Pro 0813",
]


# ============================================================
# ДОВІДНИК ІНШИХ / ВІДКИНУТИХ МОДЕЛЕЙ
# Актуальність: 28.08.2026
# Intelligence = Artificial Analysis Intelligence Index.
# Для reasoning-моделей беремо найсильніший/Max effort результат, якщо він є.
# ============================================================

# Ці моделі, навіть якщо мають реальний тест і показуються у верхній таблиці,
# додатково лишаємо в нижньому блоці як «відкинуті / економічно невигідні».
# Тобто результат Fable НЕ ховається — дубль унизу потрібен лише як пояснення рішення.
FORCED_REJECTED_MODELS = {
    "claude fable 5",
}

# Artificial Analysis Intelligence Index (актуальний зріз для наших основних моделей).
# Для моделей, що вже є в OTHER_MODEL_CATALOG, значення нижче не обов'язкове:
# helper автоматично забере intelligence з каталогу. Тут — моделі, яких у каталозі немає.
MAIN_INTELLIGENCE_INDEX = {
    "gpt-5.6 sol": 61,
    "gpt-5.6 luna": 52,
    "gemini 3.6 flash": 52,
}

# price_input / price_output можуть бути рядками, бо для OpenRouter ціна
# іноді залежить від конкретного провайдера/маршруту.
OTHER_MODEL_CATALOG = [
    {
        "name": "GPT-5.6 Terra",
        "provider": "OpenAI",
        "intelligence": 57,
        "intelligence_display": "57",
        "input": "$2.00",
        "output": "$12.00",
        "context": "1 050 000",
        "max_output": "128 000",
        "status": "Не протестовано повністю",
        "comment": "Сильний баланс інтелекту та ціни; логічний кандидат для наступного повного SITCAR-прогону.",
    },
    {
        "name": "GPT-5.5",
        "provider": "OpenAI",
        "intelligence": 56,
        "intelligence_display": "56",
        "input": "$5.00",
        "output": "$30.00",
        "context": "1 050 000",
        "max_output": "128 000",
        "status": "Не пріоритетна",
        "comment": "Сильна, але старіша й дорожча за GPT-5.6 Terra; для нового тесту економічний сенс слабший.",
    },
    {
        "name": "GPT-5.5 Pro",
        "provider": "OpenAI",
        "intelligence": None,
        "intelligence_display": "—",
        "input": "$30.00",
        "output": "$180.00",
        "context": "1 050 000",
        "max_output": "128 000",
        "status": "Відкинуто",
        "comment": "Надто дорога для 10-запитної SITCAR-діагностики; API-запити також можуть виконуватися кілька хвилин.",
    },
    {
        "name": "GPT-5.4",
        "provider": "OpenAI",
        "intelligence": 53,
        "intelligence_display": "53",
        "input": "$2.50",
        "output": "$15.00",
        "context": "1 050 000",
        "max_output": "128 000",
        "status": "Не пріоритетна",
        "comment": "Нормальна якість, але GPT-5.6 Terra новіша, розумніша та дешевша на input/output.",
    },
    {
        "name": "GPT-5.4 Pro",
        "provider": "OpenAI",
        "intelligence": None,
        "intelligence_display": "—",
        "input": "$30.00",
        "output": "$180.00",
        "context": "1 050 000",
        "max_output": "128 000",
        "status": "Відкинуто",
        "comment": "Надмірна ціна для SITCAR; практичного сенсу проти новіших моделей майже немає.",
    },
    {
        "name": "GPT-5.4 Mini",
        "provider": "OpenAI",
        "intelligence": 41,
        "intelligence_display": "41",
        "input": "$0.75",
        "output": "$4.50",
        "context": "400 000",
        "max_output": "128 000",
        "status": "Резерв",
        "comment": "Дешева й швидка, але глобальний рівень інтелекту суттєво нижчий за актуальні frontier/Flash-моделі.",
    },
    {
        "name": "GPT-5.4 Nano",
        "provider": "OpenAI",
        "intelligence": 40,
        "intelligence_display": "40",
        "input": "$0.20",
        "output": "$1.25",
        "context": "400 000",
        "max_output": "128 000",
        "status": "Резерв",
        "comment": "Дуже дешево, але радше для простих масових задач; GPT-5.6 Luna є актуальнішим кандидатом цього класу.",
    },
    {
        "name": "Gemini 3.7 Flash",
        "provider": "Google Gemini",
        "intelligence": 56,
        "intelligence_display": "56",
        "input": "$0.75*",
        "output": "$3.75*",
        "context": "1 048 576",
        "max_output": "65 536",
        "status": "Повторити тест",
        "comment": "У нашому поточному тестовому скрипті запит не пройшов. Модель офіційно доступна; *промо-ціна діє до 31.12.2026.",
    },
    {
        "name": "Gemini 3.5 Flash",
        "provider": "Google Gemini",
        "intelligence": 52,
        "intelligence_display": "52",
        "input": "$1.50",
        "output": "$9.00",
        "context": "1 048 576",
        "max_output": "65 536",
        "status": "Не пріоритетна",
        "comment": "Слабша й дорожча за актуальну Gemini 3.7 Flash; має сенс лише для сумісності зі старими інтеграціями.",
    },
    {
        "name": "Gemini 3.5 Flash-Lite",
        "provider": "Google Gemini",
        "intelligence": 37,
        "intelligence_display": "37",
        "input": "$0.30",
        "output": "$2.50",
        "context": "1 048 576",
        "max_output": "65 536",
        "status": "Резерв",
        "comment": "Дуже швидка й дешева, але якість помітно нижча; більше підходить для парсингу та простих масових задач.",
    },
    {
        "name": "Gemini 3.1 Pro Preview",
        "provider": "Google Gemini",
        "intelligence": None,
        "intelligence_display": "—",
        "input": "$2.00**",
        "output": "$12.00**",
        "context": "1 048 576",
        "max_output": "65 536",
        "status": "Старіший кандидат",
        "comment": "**Для input >200k діє дорожчий тариф $4/$18. Нова 3.7 Flash дешевша та актуальніша для нашого сценарію.",
    },
    {
        "name": "Gemini 3.1 Flash-Lite",
        "provider": "Google Gemini",
        "intelligence": 26,
        "intelligence_display": "26",
        "input": "$0.25",
        "output": "$1.50",
        "context": "1 048 576",
        "max_output": "65 536",
        "status": "Резерв",
        "comment": "Найдешевший Google-кандидат, але рівень інтелекту занизький для фінальної організаційної діагностики.",
    },
    {
        "name": "Claude Opus 5",
        "provider": "Anthropic Claude",
        "intelligence": 63,
        "intelligence_display": "63",
        "input": "$5.00",
        "output": "$25.00",
        "context": "1 000 000",
        "max_output": "128 000",
        "status": "Сильний, але дорогий",
        "comment": "Один із найрозумніших варіантів глобально, але повільний і дорогий для 10 великих SITCAR-запитів.",
    },
    {
        "name": "Claude Sonnet 5",
        "provider": "Anthropic Claude",
        "intelligence": 55,
        "intelligence_display": "55",
        "input": "$2.00",
        "output": "$10.00",
        "context": "1 000 000",
        "max_output": "128 000",
        "status": "Кандидат",
        "comment": "Помітно дешевша за Opus/Fable, сильна для професійних задач; можна протестувати окремо.",
    },
    {
        "name": "Claude Fable 5",
        "provider": "Anthropic Claude",
        "intelligence": 62,
        "intelligence_display": "62",
        "input": "$10.00",
        "output": "$50.00",
        "context": "1 000 000",
        "max_output": "128 000",
        "status": "Відкинуто",
        "comment": "Дуже розумна, але економічно невигідна для SITCAR: сам тариф уже $10 input / $50 output за 1M токенів.",
    },
    {
        "name": "Claude Mythos 5",
        "provider": "Anthropic Claude",
        "intelligence": 62,
        "intelligence_display": "≈62*",
        "input": "$10.00",
        "output": "$50.00",
        "context": "1 000 000",
        "max_output": "128 000",
        "status": "Обмежений доступ",
        "comment": "*Той самий базовий рівень можливостей, що Fable 5; окремий публічний AA-бал не використовуємо. Доступ через Project Glasswing.",
    },
    {
        "name": "DeepSeek V4 Flash 0731",
        "provider": "DeepSeek / OpenRouter",
        "intelligence": 52,
        "intelligence_display": "52",
        "input": "від ~$0.03",
        "output": "від ~$0.075",
        "context": "1 310 720",
        "max_output": "—",
        "status": "Не завершили тест",
        "comment": "Дуже дешева. У нашому OpenRouter-прогоні end-to-end обробка була надто повільною, тому повні 10 запитів не тестували; ціна залежить від маршруту/provider.",
    },
    {
        "name": "DeepSeek V4 Pro 0813",
        "provider": "DeepSeek / OpenRouter",
        "intelligence": 53,
        "intelligence_display": "53",
        "input": "~$0.66–1.32",
        "output": "~$1.98–3.96",
        "context": "1 048 576",
        "max_output": "—",
        "status": "Не завершили тест",
        "comment": "Інтелект хороший за свою ціну, але OpenRouter/provider pricing плаває. У нашому сценарії повний прогін не завершували через швидкість.",
    },
    {
        "name": "Grok 4.6",
        "provider": "SpaceXAI / xAI",
        "intelligence": 61,
        "intelligence_display": "61",
        "input": "$2.00",
        "output": "$6.00",
        "context": "500 000",
        "max_output": "не заявлено",
        "status": "Кандидат",
        "comment": "Frontier-рівень за відносно помірною ціною. Варто додати до наступної хвилі SITCAR-тестів.",
    },
]


# ============================================================
# FILE NAME PARSER
# ============================================================

_CODES_RE = "|".join(map(re.escape, ALL_CODES))

FILE_RE = re.compile(
    rf"^(?P<model>.+)_(?P<module>{_CODES_RE})_"
    r"(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<time>\d{2}-\d{2}-\d{2})"
    r"(?:\((?P<dup>\d+)\))?\."
    r"(?P<ext>html?|pdf)$",
    flags=re.IGNORECASE,
)


# ============================================================
# HELPERS
# ============================================================

def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_model_name(value: str) -> str:
    return normalize_space(value).casefold()


MODEL_ORDER_MAP = {
    normalize_model_name(name): i
    for i, name in enumerate(MODEL_ORDER)
}


def strip_tags(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    return normalize_space(value)


def parse_number(value: str | None) -> float | None:
    if not value:
        return None

    value = html.unescape(value)
    value = value.replace("\u00a0", " ").replace("\u202f", " ").strip()

    if value in {"", "—", "-", "N/A", "n/a", "None"}:
        return None

    # Прибираємо пробіли-тисячні роздільники та службові символи.
    cleaned = re.sub(r"[^\d,\.\-]", "", value)
    if not cleaned:
        return None

    # 1,234.56
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    # 0,66 або 1,25
    elif cleaned.count(",") == 1:
        left, right = cleaned.split(",", 1)
        if len(right) <= 2:
            cleaned = left + "." + right
        else:
            cleaned = left + right
    elif cleaned.count(",") > 1:
        cleaned = cleaned.replace(",", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def fmt_money(value: float | None) -> str:
    """Грошовий формат для таблиці.

    Звичайні суми показуємо до 2 знаків після коми.
    Але маленькі ненульові суми не можна перетворювати на $0.00:
      0.43524  -> $0.44
      0.04148  -> $0.04
      0.00834  -> $0.00834
      0.0004   -> $0.0004
    """
    if value is None:
        return "—"

    value = float(value)
    if value == 0:
        return "$0.00"

    absolute = abs(value)

    if absolute >= 0.01:
        return f"${value:.2f}"

    # Для дуже малих значень залишаємо достатньо цифр,
    # щоб ненульова ціна залишалась видимою.
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return f"${text}"


def fmt_integer(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{int(round(value)):,}".replace(",", " ")


def fmt_price_per_million(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:.2f}"


def fmt_percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.0f}%"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"

    seconds = max(0, seconds)

    if seconds < 60:
        return f"{seconds:.1f} с"

    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours} год {minutes} хв {secs} с"

    return f"{minutes} хв {secs} с"


def numeric_sort_value(value: float | int | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.12f}"


def safe_sum(values) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def filename_model_to_name(raw: str) -> str:
    value = raw.replace("_", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_datetime(match: re.Match) -> datetime:
    return datetime.strptime(
        f"{match.group('date')} {match.group('time')}",
        "%Y-%m-%d %H-%M-%S",
    )


def module_rank(code: str) -> int:
    if code in MODULE_ORDER:
        return MODULE_ORDER.index(code)
    if code in DISPLAY_ONLY_CODES:
        return len(MODULE_ORDER) + DISPLAY_ONLY_CODES.index(code)
    return len(MODULE_ORDER) + len(DISPLAY_ONLY_CODES) + OPTIONAL_FINAL_CODES.index(code)


# ============================================================
# HTML PARSER
# ============================================================

def extract_metrics(document: str) -> dict[str, str]:
    matches = re.findall(
        r'<div\s+class=["\']metric(?:\s+[^"\']*)?["\']\s*>\s*'
        r'<span>\s*(.*?)\s*</span>\s*'
        r'<strong>\s*(.*?)\s*</strong>\s*'
        r'</div>',
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )

    result: dict[str, str] = {}
    for key, value in matches:
        result[strip_tags(key).casefold()] = strip_tags(value)
    return result


def metric_value(metrics: dict[str, str], *names: str) -> str | None:
    for name in names:
        key = normalize_space(name).casefold()
        if key in metrics:
            return metrics[key]
    return None


def metric_number(metrics: dict[str, str], *names: str) -> float | None:
    return parse_number(metric_value(metrics, *names))


def extract_title(document: str, fallback: str) -> str:
    for tag in ("h1", "title"):
        m = re.search(
            rf"<{tag}[^>]*>(.*?)</{tag}>",
            document,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            value = strip_tags(m.group(1))
            if value:
                return value
    return fallback


def extract_muted_header(document: str) -> str:
    m = re.search(
        r'<div\s+class=["\']muted["\'][^>]*>(.*?)</div>',
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return strip_tags(m.group(1)) if m else ""


def detect_provider(model_name: str, requested_model: str, muted_header: str, filename: str) -> tuple[str, str]:
    value = f"{model_name} {requested_model} {muted_header} {filename}".casefold()

    if "deepseek" in value or "openrouter" in value:
        return "deepseek", "DeepSeek / OpenRouter"
    if "claude" in value or "anthropic" in value:
        return "claude", "Anthropic Claude"
    if "gemini" in value or "google" in value:
        return "gemini", "Google Gemini"
    if "gpt" in value or "openai" in value:
        return "openai", "OpenAI"
    return "other", "Інший"


def patch_index_link(document: str) -> tuple[str, bool]:
    original = document

    # Будь-який href у кнопці/посиланні class="back" -> index.html
    def replace_back(match: re.Match) -> str:
        tag = match.group(0)
        if re.search(r'href\s*=\s*["\'][^"\']*["\']', tag, flags=re.IGNORECASE):
            return re.sub(
                r'href\s*=\s*["\'][^"\']*["\']',
                'href="index.html"',
                tag,
                count=1,
                flags=re.IGNORECASE,
            )
        return tag[:-1] + ' href="index.html">'

    document = re.sub(
        r'<a\b[^>]*class=["\'][^"\']*\bback\b[^"\']*["\'][^>]*>',
        replace_back,
        document,
        flags=re.IGNORECASE,
    )

    # Додаткова страховка для старих ../index.html / ../../index.html / ./index.html
    document = re.sub(
        r'href\s*=\s*["\'](?:\.\./)+index\.html["\']',
        'href="index.html"',
        document,
        flags=re.IGNORECASE,
    )
    document = re.sub(
        r'href\s*=\s*["\']\./index\.html["\']',
        'href="index.html"',
        document,
        flags=re.IGNORECASE,
    )

    return document, document != original


def build_files_lookup() -> tuple[dict[str, Path], dict[str, Path]]:
    """Повертає (relative_lookup, unique_basename_lookup) для BASE_DIR/Files.

    relative_lookup дозволяє знайти файл за відносним шляхом всередині Files,
    basename_lookup використовується лише коли basename унікальний. Це не дає
    випадково послатися не на той файл, якщо у вкладених папках є однакові назви.
    """
    relative_lookup: dict[str, Path] = {}
    basename_candidates: dict[str, list[Path]] = defaultdict(list)

    if not FILES_DIR.exists() or not FILES_DIR.is_dir():
        return relative_lookup, {}

    for path in FILES_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel_inside = path.relative_to(FILES_DIR).as_posix()
        except ValueError:
            continue
        relative_lookup[rel_inside.casefold()] = path
        basename_candidates[path.name.casefold()].append(path)

    unique_basename_lookup = {
        name: paths[0]
        for name, paths in basename_candidates.items()
        if len(paths) == 1
    }
    return relative_lookup, unique_basename_lookup


def file_href(path: Path) -> str:
    """URL файла відносно result HTML/index.html, наприклад Files/report.pdf."""
    try:
        rel = path.resolve().relative_to(BASE_DIR.resolve())
    except (OSError, ValueError):
        rel = Path("Files") / path.name
    return quote(rel.as_posix(), safe="/")


def resolve_files_reference(
    raw_value: str,
    relative_lookup: dict[str, Path],
    basename_lookup: dict[str, Path],
) -> Path | None:
    """Намагається зіставити старий href/видимий шлях із реальним файлом Files/."""
    if not raw_value:
        return None

    value = html.unescape(unquote(str(raw_value))).strip()
    value = value.split("#", 1)[0].split("?", 1)[0]
    value = value.replace("\\", "/").strip(" ./")
    if not value:
        return None

    # Якщо href уже містить Files/... — беремо частину після Files/.
    m = re.search(r'(?:^|/)Files/(.+)$', value, flags=re.IGNORECASE)
    inside_files = m.group(1).strip("/") if m else value

    direct = relative_lookup.get(inside_files.casefold())
    if direct:
        return direct

    basename = inside_files.rsplit("/", 1)[-1].casefold()
    return basename_lookup.get(basename)


def _link_plain_filenames_in_html_fragment(
    fragment: str,
    relative_lookup: dict[str, Path],
    basename_lookup: dict[str, Path],
) -> tuple[str, int]:
    """Робить видимі назви файлів клікабельними, якщо вони ще не всередині <a>."""
    if not basename_lookup:
        return fragment, 0

    # Довші назви першими, щоб одна коротша назва не перехопила частину довшої.
    names = sorted((p.name for p in basename_lookup.values()), key=len, reverse=True)
    if not names:
        return fragment, 0

    # Працюємо тільки з текстовими сегментами між HTML-тегами.
    tokens = re.split(r'(<[^>]+>)', fragment)
    anchor_depth = 0
    linked = 0

    for i, token in enumerate(tokens):
        if token.startswith("<"):
            if re.match(r'<a\b', token, flags=re.IGNORECASE):
                anchor_depth += 1
            elif re.match(r'</a\s*>', token, flags=re.IGNORECASE):
                anchor_depth = max(0, anchor_depth - 1)
            continue

        if anchor_depth or not token.strip():
            continue

        updated = token
        for name in names:
            path = basename_lookup.get(name.casefold())
            if not path:
                continue

            # У тексті HTML символи можуть бути entity-escaped.
            variants = {name, html.escape(name)}
            for visible in sorted(variants, key=len, reverse=True):
                pattern = re.compile(re.escape(visible), flags=re.IGNORECASE)
                if not pattern.search(updated):
                    continue
                href = file_href(path)
                replacement = (
                    f'<a class="attachment-file-link" href="{html.escape(href)}" '
                    f'target="_blank" rel="noopener">{html.escape(path.name)}</a>'
                )
                updated, count = pattern.subn(replacement, updated)
                linked += count
                if count:
                    break
        tokens[i] = updated

    return "".join(tokens), linked


def patch_attachment_links(
    document: str,
    relative_lookup: dict[str, Path],
    basename_lookup: dict[str, Path],
) -> tuple[str, int]:
    """Виправляє attachment links на BASE_DIR/Files/... у всіх result HTML."""
    if not relative_lookup and not basename_lookup:
        return document, 0

    section_re = re.compile(
        r'(<section\b[^>]*class=["\'][^"\']*\battachments-card\b[^"\']*["\'][^>]*>)(.*?)(</section>)',
        flags=re.IGNORECASE | re.DOTALL,
    )
    total_links = 0

    def patch_section(match: re.Match) -> str:
        nonlocal total_links
        opening, body, closing = match.group(1), match.group(2), match.group(3)

        # 1) Існуючі <a href="..."> — переписуємо href, якщо файл знайдено у Files/.
        anchor_re = re.compile(
            r'<a\b(?P<before>[^>]*?)href\s*=\s*(?P<q>["\'])(?P<href>.*?)(?P=q)(?P<after>[^>]*)>',
            flags=re.IGNORECASE | re.DOTALL,
        )

        def patch_anchor(a: re.Match) -> str:
            nonlocal total_links
            target = resolve_files_reference(
                a.group("href"),
                relative_lookup,
                basename_lookup,
            )
            if not target:
                return a.group(0)

            href = file_href(target)
            tag = f'<a{a.group("before")}href="{html.escape(href)}"{a.group("after")}>'
            if not re.search(r'\btarget\s*=', tag, flags=re.IGNORECASE):
                tag = tag[:-1] + ' target="_blank" rel="noopener">'
            total_links += 1
            return tag

        body = anchor_re.sub(patch_anchor, body)

        # 2) Якщо генератор показав просто назву файла без <a>, робимо її клікабельною.
        body, added = _link_plain_filenames_in_html_fragment(
            body,
            relative_lookup,
            basename_lookup,
        )
        total_links += added
        return opening + body + closing

    document = section_re.sub(patch_section, document)
    return document, total_links


def patch_total_with_tax(document: str, tax_rate: float = RESULT_HTML_TAX_RATE) -> tuple[str, bool]:
    """Додає після TOTAL COST окремий TOTAL + 20% у картці COST.

    Функція ідемпотентна: повторний запуск оновлює/перегенеровує наш рядок,
    а не додає дублікати.
    """
    original = document

    # Прибираємо попередньо доданий нами рядок, якщо скрипт запускають повторно.
    document = re.sub(
        r'<div\s+class=["\'][^"\']*\btotal-with-tax\b[^"\']*["\'][^>]*>.*?</div>\s*',
        '',
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )

    total_re = re.compile(
        r'(?P<row><div\s+class=["\'][^"\']*\btotal\b[^"\']*["\'][^>]*>\s*'
        r'<span>\s*TOTAL\s+COST\s*</span>\s*<strong>\s*(?P<value>.*?)\s*</strong>\s*</div>)',
        flags=re.IGNORECASE | re.DOTALL,
    )

    def add_tax_row(match: re.Match) -> str:
        raw = strip_tags(match.group("value"))
        total = parse_number(raw)
        if total is None:
            return match.group("row")

        total_with_tax = total * (1.0 + tax_rate)
        percent = int(round(tax_rate * 100))
        tax_row = (
            f'<div class="metric total total-with-tax">'
            f'<span>TOTAL + {percent}%</span>'
            f'<strong>${total_with_tax:.6f}</strong>'
            f'</div>'
        )
        return match.group("row") + "\n" + tax_row

    document, count = total_re.subn(add_tax_row, document, count=1)

    # CSS для нового total та посилань на вкладення — додаємо один раз.
    css_lines = []
    if "total-with-tax strong" not in document:
        css_lines.append('.total-with-tax strong { color:#d2a8ff; }')
    if "attachment-file-link" not in document:
        css_lines.append(
            '.attachments-card a, .attachment-file-link { color:var(--accent); font-weight:700; text-decoration:none; }\n'
            '.attachments-card a:hover, .attachment-file-link:hover { text-decoration:underline; }'
        )
    if css_lines and "</style>" in document:
        document = document.replace("</style>", "\n" + "\n".join(css_lines) + "\n</style>", 1)

    return document, (document != original and count > 0)


def patch_result_html(
    document: str,
    relative_lookup: dict[str, Path],
    basename_lookup: dict[str, Path],
) -> tuple[str, dict[str, int]]:
    """Усі автоматичні правки одного result HTML перед побудовою index.html."""
    stats = {"back": 0, "tax": 0, "file_links": 0}

    document, changed = patch_index_link(document)
    stats["back"] = int(changed)

    document, linked = patch_attachment_links(
        document,
        relative_lookup,
        basename_lookup,
    )
    stats["file_links"] = linked

    document, tax_changed = patch_total_with_tax(document)
    stats["tax"] = int(tax_changed)

    return document, stats


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class Artifact:
    path: Path
    model_raw: str
    model_name: str
    module: str
    ext: str
    run_dt: datetime


@dataclass
class RunMetrics:
    artifact: Artifact
    provider_key: str
    provider_label: str
    requested_model: str
    context_window: float | None
    max_output: float | None
    latency_seconds: float | None
    input_tokens: float | None
    cached_tokens: float | None
    output_tokens: float | None
    total_tokens: float | None
    input_price: float | None
    cached_price: float | None
    output_price: float | None
    input_cost: float | None
    cached_cost: float | None
    output_cost: float | None
    total_cost: float | None
    tax_rate: float
    tax_amount: float | None
    total_with_tax: float | None
    max_total_with_tax: float | None


# ============================================================
# DISCOVERY
# ============================================================

def discover_artifacts() -> list[Artifact]:
    """
    Джерела:
      - HTML у BASE_DIR
      - PDF у BASE_DIR (якщо раптом є)
      - PDF у BASE_DIR/PDF_RESULTS

    PDF лише показуються як файли результату. Метрики, токени, latency та
    розрахунок вартості читаються тільки з HTML.
    """
    artifacts: list[Artifact] = []

    # v15 за замовчуванням не змінює result HTML.
    # Lookup потрібен лише якщо вручну увімкнути AUTO_PATCH_RESULT_HTML.
    if AUTO_PATCH_RESULT_HTML:
        relative_files, basename_files = build_files_lookup()
    else:
        relative_files, basename_files = {}, {}
    patch_stats = {"html_changed": 0, "tax": 0, "file_links": 0, "back": 0}

    candidate_paths: list[Path] = []

    # HTML-моделі лежать поруч зі скриптом/index.html.
    candidate_paths.extend(BASE_DIR.glob("*.html"))

    # Підтримка PDF у корені, якщо вони там з'являться.
    candidate_paths.extend(BASE_DIR.glob("*.pdf"))

    # Основна папка PDF результатів.
    if PDF_RESULTS_DIR.exists() and PDF_RESULTS_DIR.is_dir():
        candidate_paths.extend(PDF_RESULTS_DIR.glob("*.pdf"))

    # Прибираємо дублікати шляхів та index.html.
    seen_paths: set[Path] = set()

    for path in sorted(candidate_paths, key=lambda x: str(x).casefold()):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path

        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)

        if not path.is_file():
            continue

        if path.resolve() == INDEX_FILE.resolve():
            continue

        m = FILE_RE.match(path.name)
        if not m:
            continue

        module = m.group("module").upper()
        ext = m.group("ext").lower()
        if ext == "htm":
            ext = "html"

        model_raw = m.group("model")
        model_name = filename_model_to_name(model_raw)

        # Автоматично переписуємо result HTML:
        #   - back -> index.html
        #   - attachment links -> Files/...
        #   - TOTAL + 20% після чистого TOTAL COST
        if ext == "html" and AUTO_PATCH_RESULT_HTML:
            document = path.read_text(encoding="utf-8", errors="replace")
            patched, stats = patch_result_html(
                document,
                relative_files,
                basename_files,
            )
            if patched != document:
                path.write_text(patched, encoding="utf-8")
                patch_stats["html_changed"] += 1
            patch_stats["tax"] += stats["tax"]
            patch_stats["file_links"] += stats["file_links"]
            patch_stats["back"] += stats["back"]

        artifacts.append(
            Artifact(
                path=path,
                model_raw=model_raw,
                model_name=model_name,
                module=module,
                ext=ext,
                run_dt=extract_datetime(m),
            )
        )

    if AUTO_PATCH_RESULT_HTML:
        if patch_stats["html_changed"] or patch_stats["file_links"]:
            print(
                "HTML auto-patch: "
                f"оновлено={patch_stats['html_changed']}, "
                f"TOTAL+20%={patch_stats['tax']}, "
                f"посилань Files/={patch_stats['file_links']}, "
                f"back-link={patch_stats['back']}"
            )
        elif FILES_DIR.exists():
            print("HTML auto-patch: змін не потрібно; Files/ перевірено")
    else:
        print("Result HTML: read-only режим; файли не змінюються")

    return artifacts


def newest_by_model_module_format(artifacts: list[Artifact]) -> tuple[dict, int]:
    newest: dict[tuple[str, str, str], Artifact] = {}
    hidden_duplicates = 0

    for artifact in artifacts:
        key = (
            normalize_model_name(artifact.model_name),
            artifact.module,
            artifact.ext,
        )

        current = newest.get(key)
        if current is None or artifact.run_dt > current.run_dt:
            if current is not None:
                hidden_duplicates += 1
            newest[key] = artifact
        else:
            hidden_duplicates += 1

    return newest, hidden_duplicates


# ============================================================
# PARSE RUN METRICS
# ============================================================

def parse_run_metrics(artifact: Artifact) -> RunMetrics:
    document = artifact.path.read_text(encoding="utf-8", errors="replace")
    metrics = extract_metrics(document)

    requested_model = metric_value(
        metrics,
        "Requested",
        "Requested model",
        "Model",
    ) or ""

    muted_header = extract_muted_header(document)
    provider_key, provider_label = detect_provider(
        artifact.model_name,
        requested_model,
        muted_header,
        artifact.path.name,
    )

    tax_rate = TAX_RATES.get(provider_key, TAX_RATES["other"])

    context_window = metric_number(
        metrics,
        "Context window",
        "Context",
        "Context tokens",
        "Max context",
    )

    max_output = metric_number(
        metrics,
        "Max output",
        "Maximum output",
        "Max output tokens",
        "Maximum output tokens",
    )

    latency_seconds = metric_number(
        metrics,
        "Latency",
        "Duration",
        "Request duration",
        "Elapsed",
        "Elapsed time",
    )

    input_tokens = metric_number(metrics, "Input tokens", "Prompt tokens")
    cached_tokens = metric_number(
        metrics,
        "Cached input",
        "Cached tokens",
        "Cache read tokens",
    )
    output_tokens = metric_number(metrics, "Output tokens", "Completion tokens")
    total_tokens = metric_number(metrics, "Total tokens")

    input_price = metric_number(
        metrics,
        "Input price / 1M",
        "Input price / 1M tokens",
        "Input / 1M",
    )
    cached_price = metric_number(
        metrics,
        "Cached price / 1M",
        "Cache price / 1M",
        "Cached input price / 1M",
    )
    output_price = metric_number(
        metrics,
        "Output price / 1M",
        "Output price / 1M tokens",
        "Output / 1M",
    )

    input_cost = metric_number(metrics, "Input cost")
    cached_cost = metric_number(metrics, "Cached cost", "Cache cost")
    output_cost = metric_number(metrics, "Output cost")
    total_cost = metric_number(metrics, "TOTAL COST", "Total cost")

    # Fallback calculation when a specific cost line is absent.
    if input_cost is None and input_tokens is not None and input_price is not None:
        cached = cached_tokens or 0.0
        normal_input = max(input_tokens - cached, 0.0)
        input_cost = normal_input * input_price / 1_000_000

    if cached_cost is None:
        if cached_tokens is not None and cached_price is not None:
            cached_cost = cached_tokens * cached_price / 1_000_000
        else:
            cached_cost = 0.0

    if output_cost is None and output_tokens is not None and output_price is not None:
        output_cost = output_tokens * output_price / 1_000_000

    if total_cost is None:
        total_cost = safe_sum([input_cost, cached_cost, output_cost])

    tax_amount = total_cost * tax_rate if total_cost is not None else None
    total_with_tax = (
        total_cost + tax_amount
        if total_cost is not None and tax_amount is not None
        else None
    )

    # MAX-output scenario for THIS module:
    # actual input side + full declared output ceiling.
    input_side_cost = safe_sum([input_cost, cached_cost])
    max_total_with_tax = None

    if input_side_cost is not None and max_output is not None and output_price is not None:
        max_output_cost = max_output * output_price / 1_000_000
        max_before_tax = input_side_cost + max_output_cost
        max_total_with_tax = max_before_tax * (1.0 + tax_rate)

    return RunMetrics(
        artifact=artifact,
        provider_key=provider_key,
        provider_label=provider_label,
        requested_model=requested_model,
        context_window=context_window,
        max_output=max_output,
        latency_seconds=latency_seconds,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_price=input_price,
        cached_price=cached_price,
        output_price=output_price,
        input_cost=input_cost,
        cached_cost=cached_cost,
        output_cost=output_cost,
        total_cost=total_cost,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        total_with_tax=total_with_tax,
        max_total_with_tax=max_total_with_tax,
    )


# ============================================================
# GROUPING
# ============================================================

def canonical_model_name(artifacts_for_model: list[Artifact]) -> str:
    # Prefer ORG HTML title, then any HTML title, then filename-derived name.
    ordered = sorted(
        artifacts_for_model,
        key=lambda a: (
            0 if (a.module == "ORG" and a.ext == "html") else
            1 if a.ext == "html" else 2,
            -a.run_dt.timestamp(),
        ),
    )
    return ordered[0].model_name


def group_data(newest: dict) -> dict[str, dict]:
    by_model_key: dict[str, list[Artifact]] = defaultdict(list)

    for artifact in newest.values():
        by_model_key[normalize_model_name(artifact.model_name)].append(artifact)

    result: dict[str, dict] = {}

    for model_key, artifacts in by_model_key.items():
        model_name = canonical_model_name(artifacts)

        html_runs: dict[str, RunMetrics] = {}
        files_by_module: dict[str, dict[str, Artifact]] = defaultdict(dict)

        for artifact in artifacts:
            files_by_module[artifact.module][artifact.ext] = artifact
            if artifact.ext == "html":
                try:
                    html_runs[artifact.module] = parse_run_metrics(artifact)
                except Exception as exc:
                    print(f"  WARN metrics: {artifact.path.name}: {exc}")

        preferred_run = (
            html_runs.get("ORG")
            or next(iter(sorted(html_runs.values(), key=lambda r: module_rank(r.artifact.module))), None)
        )

        if preferred_run:
            provider_key = preferred_run.provider_key
            provider_label = preferred_run.provider_label
            tax_rate = preferred_run.tax_rate
            input_price = preferred_run.input_price
            output_price = preferred_run.output_price
            context_window = preferred_run.context_window
            max_output = preferred_run.max_output
        else:
            provider_key, provider_label = detect_provider(model_name, "", "", model_name)
            tax_rate = TAX_RATES.get(provider_key, TAX_RATES["other"])
            input_price = output_price = context_window = max_output = None

        tested_modules = [m for m in FULL_MODULES if m in html_runs]
        optional_final = next((c for c in OPTIONAL_FINAL_CODES if c in html_runs), None)

        # Час повної діагностики: 9 напрямків + FULL, якщо відповідні HTML-запуски є.
        total_latency = safe_sum([html_runs[m].latency_seconds for m in tested_modules])
        if optional_final:
            total_latency = safe_sum([total_latency, html_runs[optional_final].latency_seconds])

        # FREE = real sum of available Free modules. NO ORG multiplication.
        free_runs = [html_runs[m] for m in FREE_MODULES if m in html_runs]
        free_actual = safe_sum([r.total_with_tax for r in free_runs])
        free_max = safe_sum([r.max_total_with_tax for r in free_runs])

        # FULL = реальна сума 9 діагностичних модулів + окремого FULL API-запиту.
        # Нічого не множимо та не екстраполюємо. PDF вартості не додає.
        # Optional presentation/final додається лише якщо існує реальний HTML API-запуск.
        full_runs = [html_runs[m] for m in FULL_MODULES if m in html_runs]
        if optional_final:
            full_runs.append(html_runs[optional_final])

        full_actual = safe_sum([r.total_with_tax for r in full_runs])
        full_max = safe_sum([r.max_total_with_tax for r in full_runs])

        all_actual = safe_sum([r.total_with_tax for r in html_runs.values()])
        all_max = safe_sum([r.max_total_with_tax for r in html_runs.values()])

        result[model_key] = {
            "model_name": model_name,
            "provider_key": provider_key,
            "provider_label": provider_label,
            "tax_rate": tax_rate,
            "input_price": input_price,
            "output_price": output_price,
            "context_window": context_window,
            "max_output": max_output,
            "html_runs": html_runs,
            "files_by_module": files_by_module,
            "tested_modules": tested_modules,
            "optional_final": optional_final,
            "total_latency": total_latency,
            "free_actual": free_actual,
            "free_max": free_max,
            "free_count": len(free_runs),
            "full_actual": full_actual,
            "full_max": full_max,
            "full_count": len([m for m in FULL_MODULES if m in html_runs]),
            "has_final": optional_final is not None,
            "all_actual": all_actual,
            "all_max": all_max,
        }

    return result


# ============================================================
# SORTING
# ============================================================

def model_sort_key(item: dict):
    key = normalize_model_name(item["model_name"])
    if key in MODEL_ORDER_MAP:
        return (0, MODEL_ORDER_MAP[key], "")

    provider_order = {
        "openai": 0,
        "gemini": 1,
        "claude": 2,
        "deepseek": 3,
        "other": 9,
    }
    return (1, provider_order.get(item["provider_key"], 9), key)


# ============================================================
# HTML PARTS
# ============================================================

def coverage_free(item: dict) -> str:
    n = item["free_count"]
    total = len(FREE_MODULES)
    if n == total:
        return f"{n}/{total} мод. · повністю"
    return f"{n}/{total} мод. · частково"


def coverage_full(item: dict) -> str:
    n = item["full_count"]
    total = len(FULL_MODULES)
    if item["has_final"]:
        return f"{n}/{total} зап. + додатковий фінальний"
    if n == total:
        return f"{n}/{total} зап. · повністю"
    return f"{n}/{total} зап. · частково"


def artifact_href(artifact: Artifact) -> str:
    """URL відносно index.html, включно з PDF_RESULTS/..."""
    try:
        rel = artifact.path.resolve().relative_to(BASE_DIR.resolve())
    except ValueError:
        rel = Path(artifact.path.name)
    return quote(rel.as_posix(), safe="/")


def module_file_links(files: dict[str, Artifact], module: str, compact: bool = False) -> str:
    module_name = MODULE_NAMES.get(module, module)
    parts = []

    html_art = files.get("html")
    pdf_art = files.get("pdf")

    if compact:
        label = html.escape(module_name)
    else:
        label = f"{html.escape(module)} · {html.escape(module_name)}"

    if html_art:
        parts.append(
            f'<a class="result-link" href="{html.escape(artifact_href(html_art))}" '
            f'title="{html.escape(module_name)} · HTML">'
            f'<span class="result-code">HTML</span>'
            f'<span class="result-name">{html.escape(module_name)}</span>'
            f'</a>'
        )

    if pdf_art:
        parts.append(
            f'<a class="result-link pdf" href="{html.escape(artifact_href(pdf_art))}" '
            f'title="{html.escape(module_name)} · PDF">'
            f'<span class="result-code">PDF</span>'
            f'<span class="result-name">{html.escape(module_name)}</span>'
            f'</a>'
        )

    return "".join(parts)


def model_intelligence_score(model_name: str) -> int | float | None:
    """AA Intelligence для моделі у верхній таблиці.

    Спочатку дивимося explicit map для реально протестованих моделей,
    потім — загальний OTHER_MODEL_CATALOG.
    """
    key = normalize_model_name(model_name)

    if key in MAIN_INTELLIGENCE_INDEX:
        return MAIN_INTELLIGENCE_INDEX[key]

    for model in OTHER_MODEL_CATALOG:
        if normalize_model_name(model.get("name", "")) == key:
            return model.get("intelligence")

    return None


def make_main_intelligence_cell(model_name: str) -> str:
    score = model_intelligence_score(model_name)
    if score is None:
        return (
            '<td class="main-intel-cell" data-sort="">'
            '<strong>—</strong><span>немає AA-балу</span></td>'
        )

    display = f"{float(score):g}"
    level = intelligence_level(score)
    return (
        f'<td class="main-intel-cell" data-sort="{float(score):.6f}">'
        f'<strong>{html.escape(display)}</strong>'
        f'<span>{html.escape(level)}</span></td>'
    )


def make_results_cell(item: dict) -> str:
    """Красивий компактний блок результатів у головній таблиці.

    - 2 модулі в один ряд на desktop;
    - кожен модуль має окрему назву та кнопки HTML/PDF;
    - якщо є лише один формат — єдина кнопка центрується;
    - FULL/FINAL/PRES візуально відокремлені.
    """
    files_by_module = item["files_by_module"]
    modules = [code for code in DISPLAY_CODES if code in files_by_module]

    if not modules:
        return '<span class="muted">—</span>'

    cards: list[str] = []

    for module in modules:
        files = files_by_module[module]
        module_name = RESULT_SHORT_NAMES.get(module, MODULE_NAMES.get(module, module))
        html_art = files.get("html")
        pdf_art = files.get("pdf")

        buttons: list[str] = []

        if html_art:
            buttons.append(
                f'<a class="result-main-btn html" '
                f'href="{html.escape(artifact_href(html_art))}" '
                f'title="{html.escape(module_name)} · HTML">HTML</a>'
            )

        if pdf_art:
            buttons.append(
                f'<a class="result-main-btn pdf" '
                f'href="{html.escape(artifact_href(pdf_art))}" '
                f'title="{html.escape(module_name)} · PDF">PDF</a>'
            )

        if not buttons:
            continue

        card_classes = ["result-main-card"]
        if len(buttons) == 1:
            card_classes.append("single-format")
        if module in DISPLAY_ONLY_CODES + OPTIONAL_FINAL_CODES:
            card_classes.append("final-result")

        cards.append(
            f'<div class="{" ".join(card_classes)}">'
            f'  <div class="result-main-label">'
            f'    <span class="result-main-code">{html.escape(module)}</span>'
            f'  </div>'
            f'  <div class="result-main-actions">{"".join(buttons)}</div>'
            f'</div>'
        )

    if not cards:
        return '<span class="muted">—</span>'

    return '<div class="results-main-grid">' + "".join(cards) + '</div>'


def make_money_cell(value: float | None, coverage: str, css: str = "") -> str:
    return (
        f'<td class="money {css}" data-sort="{numeric_sort_value(value)}">'
        f'<strong>{fmt_money(value)}</strong>'
        f'<span class="coverage">{html.escape(coverage)}</span>'
        f'</td>'
    )


def make_main_row(item: dict) -> str:
    tested_count = item["full_count"]
    time_text = fmt_duration(item["total_latency"])

    module_badges = "".join(
        f'<span class="module-badge">{m}</span>'
        for m in item["tested_modules"]
    )

    if item["optional_final"]:
        module_badges += '<span class="module-badge final">FINAL</span>'

    return f"""
<tr class="model-row" data-model="{html.escape((item['model_name'] + ' ' + item['provider_label']).casefold())}">
    <td class="model-cell" data-sort="{html.escape(normalize_model_name(item['model_name']))}">
        <div class="model-name">{html.escape(item['model_name'])}</div>
        <div class="provider">{html.escape(item['provider_label'])}</div>
        <div class="model-meta">{tested_count}/{len(FULL_MODULES)} зап. · {html.escape(time_text)}</div>
        <div class="module-badges">{module_badges}</div>
    </td>

    {make_main_intelligence_cell(item['model_name'])}

    <td data-sort="{numeric_sort_value(item['input_price'])}">{fmt_price_per_million(item['input_price'])}</td>
    <td data-sort="{numeric_sort_value(item['output_price'])}">{fmt_price_per_million(item['output_price'])}</td>
    <td data-sort="{numeric_sort_value(item['context_window'])}">{fmt_integer(item['context_window'])}</td>
    <td data-sort="{numeric_sort_value(item['max_output'])}">{fmt_integer(item['max_output'])}</td>

    {make_money_cell(item['free_actual'], coverage_free(item), 'free-cell')}
    {make_money_cell(item['full_actual'], coverage_full(item), 'paid-cell')}
    {make_money_cell(item['free_max'], coverage_free(item), 'free-cell max-cell')}
    {make_money_cell(item['full_max'], coverage_full(item), 'paid-cell max-cell')}

    <td class="view-cell">{make_results_cell(item)}</td>
</tr>
"""


def tech_metric(label: str, value: str, css: str = "") -> str:
    return (
        f'<div class="tech-metric {css}">'
        f'<span>{html.escape(label)}</span>'
        f'<strong>{value}</strong>'
        f'</div>'
    )


def make_tech_module_card(run: RunMetrics, files: dict[str, Artifact]) -> str:
    module = run.artifact.module
    module_name = MODULE_NAMES.get(module, module)
    dt = run.artifact.run_dt.strftime("%d.%m.%Y %H:%M:%S")

    links = module_file_links(files, module)

    return f"""
<div class="tech-module-card">
    <div class="tech-module-head">
        <div>
            <span class="tech-code">{html.escape(module)}</span>
            <strong>{html.escape(module_name)}</strong>
        </div>
        <span class="tech-date">{html.escape(dt)}</span>
    </div>

    <div class="tech-metrics-grid">
        {tech_metric('Час', html.escape(fmt_duration(run.latency_seconds)))}
        {tech_metric('Input tokens', fmt_integer(run.input_tokens))}
        {tech_metric('Output tokens', fmt_integer(run.output_tokens))}
        {tech_metric('Total tokens', fmt_integer(run.total_tokens))}
        {tech_metric('Input / 1M', fmt_price_per_million(run.input_price))}
        {tech_metric('Output / 1M', fmt_price_per_million(run.output_price))}
        {tech_metric('Max context', fmt_integer(run.context_window))}
        {tech_metric('Max output', fmt_integer(run.max_output))}
        {tech_metric('Фактично + податок', fmt_money(run.total_with_tax), 'actual-cost')}
        {tech_metric('При MAX output', fmt_money(run.max_total_with_tax), 'max-cost')}
    </div>

    <div class="tech-links">{links}</div>
</div>
"""


def make_technical_section(items: list[dict]) -> str:
    blocks = []

    for item in items:
        cards = []
        html_runs = item["html_runs"]

        for module in FULL_MODULES + OPTIONAL_FINAL_CODES:
            run = html_runs.get(module)
            if not run:
                continue
            cards.append(make_tech_module_card(run, item["files_by_module"][module]))

        # Якщо для FULL є тільки PDF без HTML API-запуску, все одно показуємо
        # посилання на PDF, але не вигадуємо для нього токени/ціну/latency.
        extra_file_links = []
        for module in DISPLAY_ONLY_CODES:
            if module in item["files_by_module"] and module not in html_runs:
                extra_file_links.append(
                    module_file_links(item["files_by_module"][module], module)
                )

        if not cards and not extra_file_links:
            continue

        summary_actual = fmt_money(item["all_actual"])
        summary_max = fmt_money(item["all_max"])

        blocks.append(f"""
<details class="tech-model-details">
    <summary>
        <span>
            <strong>{html.escape(item['model_name'])}</strong>
            <small>{html.escape(item['provider_label'])}</small>
        </span>
        <span class="tech-summary-meta">
            {item['full_count']}/{len(FULL_MODULES)} зап. · {html.escape(fmt_duration(item['total_latency']))} · факт {summary_actual} · MAX {summary_max}
        </span>
    </summary>
    <div class="tech-model-body">
        <div class="tech-grid">
            {''.join(cards)}
        </div>
        {('<div class="tech-extra-files"><span>Додаткові результати:</span>' + ''.join(extra_file_links) + '</div>') if extra_file_links else ''}
    </div>
</details>
""")

    return "\n".join(blocks)


# ============================================================
# ІНШІ / ВІДКИНУТІ МОДЕЛІ
# ============================================================

def intelligence_level(score: int | float | None) -> str:
    if score is None:
        return "Немає зіставного AA-балу"
    if score >= 60:
        return "Frontier"
    if score >= 55:
        return "Дуже високий"
    if score >= 50:
        return "Високий"
    if score >= 40:
        return "Середньо-високий"
    if score >= 30:
        return "Середній"
    return "Базовий / вузький"


def make_other_models_section(main_items: list[dict], all_items: list[dict]) -> str:
    """Таблиця моделей, яких немає у верхньому основному benchmark.

    Fable 5 лишається тут навіть якщо її реальний тест одночасно показаний зверху:
    верхня таблиця = фактичний результат, нижня = пояснення, чому модель відкидаємо за ціною.
    Інші моделі, які вже потрапили у верхню таблицю, не дублюємо.
    """
    main_names = {normalize_model_name(item["model_name"]) for item in main_items}
    all_by_name = {normalize_model_name(item["model_name"]): item for item in all_items}

    # Сортуємо нижню таблицю від найвищого AA Intelligence до найнижчого.
    # Моделі без зіставного AA-балу завжди йдуть внизу; при однаковому балі — за назвою.
    other_models = [
        model
        for model in OTHER_MODEL_CATALOG
        if (
            normalize_model_name(model["name"]) not in main_names
            or normalize_model_name(model["name"]) in FORCED_REJECTED_MODELS
        )
    ]
    other_models.sort(
        key=lambda model: (
            model.get("intelligence") is None,
            -(model.get("intelligence") or 0),
            normalize_model_name(model["name"]),
        )
    )

    rows = []
    for model in other_models:
        key = normalize_model_name(model["name"])

        observed = all_by_name.get(key)
        observed_note = ""
        if observed and observed.get("html_runs"):
            run_count = len(observed["html_runs"])
            actual = fmt_money(observed.get("all_actual"))
            max_cost = fmt_money(observed.get("all_max"))
            observed_note = (
                f' <span class="observed-note">Наш тест: {run_count} API-зап. · '
                f'факт {html.escape(actual)} · MAX {html.escape(max_cost)}.</span>'
            )

        score = model.get("intelligence")
        level = intelligence_level(score)
        score_sort = "" if score is None else str(score)

        rows.append(f"""
<tr>
    <td class="other-model-name">
        <strong>{html.escape(model['name'])}</strong>
        <span>{html.escape(model['provider'])}</span>
    </td>
    <td class="intel-cell" data-sort="{html.escape(score_sort)}">
        <strong>{html.escape(model['intelligence_display'])}</strong>
        <span>{html.escape(level)}</span>
    </td>
    <td>{html.escape(model['input'])}</td>
    <td>{html.escape(model['output'])}</td>
    <td>{html.escape(model['context'])}</td>
    <td>{html.escape(model['max_output'])}</td>
    <td><span class="candidate-status">{html.escape(model['status'])}</span></td>
    <td class="other-comment">{html.escape(model['comment'])}{observed_note}</td>
</tr>
""")

    if not rows:
        return ""

    return f"""
<section class="other-models-section">
    <div class="other-models-head">
        <div>
            <div class="eyebrow">Довідник · станом на 28.08.2026</div>
            <h2>Інші та відкинуті моделі</h2>
            <p>Додаткові кандидати та відкинуті моделі. Якщо модель уже має реальний SITCAR-тест (наприклад Claude Fable 5), її результат може одночасно бути у верхній таблиці, а тут лишається пояснення рішення. Ціни наведено за 1M токенів без нашої податкової/комерційної націнки. Intelligence — Artificial Analysis Intelligence Index; вищий бал = сильніша модель.</p>
        </div>
    </div>
    <div class="other-table-wrap">
        <table class="other-models-table">
            <thead>
                <tr>
                    <th rowspan="2">Модель</th>
                    <th rowspan="2">AA Intelligence</th>
                    <th colspan="2">Тариф за 1M токенів</th>
                    <th colspan="2">Верхня межа моделі</th>
                    <th rowspan="2">Статус</th>
                    <th rowspan="2">Коментар для SITCAR</th>
                </tr>
                <tr>
                    <th>Input</th>
                    <th>Output</th>
                    <th>Max context</th>
                    <th>Max output</th>
                </tr>
            </thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
    </div>
    <div class="other-models-note">
        <strong>Примітки:</strong> Gemini 3.7 Flash зараз має промо-ціну до 31.12.2026. DeepSeek/OpenRouter може мати різну ціну залежно від обраного provider/route. Claude Mythos 5 має обмежений доступ і не використовується як звичайний публічний API-кандидат.
    </div>
</section>
"""


# ============================================================
# BUILD INDEX
# ============================================================

def build_index(items: list[dict], all_items: list[dict], artifacts_count: int, hidden_duplicates: int) -> str:
    rows = "\n".join(make_main_row(item) for item in items)
    tech = make_technical_section(items)
    other_models = make_other_models_section(items, all_items)

    used_html_count = sum(len(item["html_runs"]) for item in items)
    used_pdf_count = sum(
        1
        for item in items
        for module_files in item["files_by_module"].values()
        if "pdf" in module_files
    )
    models_with_more_than_org = sum(1 for item in items if item["full_count"] > 1)
    total_latency_all = safe_sum([item["total_latency"] for item in items])

    tax_note = (
        f"OpenAI {fmt_percent(TAX_RATES['openai'])} · "
        f"DeepSeek/OpenRouter {fmt_percent(TAX_RATES['deepseek'])} · "
        f"Gemini {fmt_percent(TAX_RATES['gemini'])} · "
        f"Claude {fmt_percent(TAX_RATES['claude'])}"
    )

    html_template = r'''<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SITCAR Огляд Моделей ціна якість</title>

<style>
:root {
    --bg:#0d1117;
    --card:#161b22;
    --card2:#1c2128;
    --border:#30363d;
    --text:#f0f6fc;
    --muted:#8b949e;
    --blue:#58a6ff;
    --green:#3fb950;
    --yellow:#d29922;
    --orange:#db6d28;
    --red:#f85149;
    --purple:#bc8cff;
}

* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body {
    margin:0;
    background:var(--bg);
    color:var(--text);
    font-family:Segoe UI,Inter,Arial,sans-serif;
    line-height:1.45;
}
.container {
    width:min(1900px,calc(100% - 24px));
    margin:auto;
    padding:14px 0 70px;
}

/* HERO */
.hero {
    background:
        radial-gradient(circle at 85% 10%,rgba(88,166,255,.14),transparent 35%),
        linear-gradient(135deg,#161b22,#111820);
    border:1px solid var(--border);
    border-radius:16px;
    padding:16px 20px;
    margin-bottom:9px;
}
.hero-top {
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    gap:16px;
    flex-wrap:wrap;
}
.eyebrow {
    color:var(--blue);
    font-size:10px;
    font-weight:800;
    letter-spacing:.08em;
    text-transform:uppercase;
    margin-bottom:3px;
}
h1 {
    margin:0;
    font-size:clamp(27px,3vw,39px);
    line-height:1.1;
}
.hero p {
    margin:7px 0 0;
    max-width:1100px;
    color:var(--muted);
    font-size:13px;
}
.status {
    display:inline-flex;
    padding:7px 10px;
    border:1px solid rgba(63,185,80,.34);
    border-radius:999px;
    background:rgba(46,160,67,.10);
    color:#7ee787;
    font-size:11px;
    font-weight:800;
}

/* SUMMARY */
.summary-grid {
    display:grid;
    grid-template-columns:repeat(4,minmax(0,1fr));
    gap:8px;
    margin-bottom:9px;
}
.summary-card {
    background:var(--card);
    border:1px solid var(--border);
    border-radius:12px;
    padding:9px 11px;
}
.summary-label {
    color:var(--muted);
    font-size:9px;
    font-weight:800;
    letter-spacing:.06em;
    text-transform:uppercase;
    margin-bottom:2px;
}
.summary-value {
    font-size:18px;
    font-weight:850;
}
.summary-value.small { font-size:12px; }

/* TOOLBAR */
.toolbar {
    display:flex;
    gap:8px;
    align-items:center;
    background:var(--card);
    border:1px solid var(--border);
    border-radius:12px;
    padding:7px 9px;
    margin-bottom:7px;
}
.search { flex:1; min-width:220px; }
.search input {
    width:100%;
    padding:8px 10px;
    background:#0d1117;
    border:1px solid var(--border);
    border-radius:8px;
    color:var(--text);
    outline:none;
    font-size:13px;
}
.search input:focus { border-color:var(--blue); }
.toolbar-note { color:var(--muted); font-size:10px; }
.tech-toggle {
    flex:0 0 auto;
    border:1px solid var(--border);
    background:rgba(255,255,255,.035);
    color:var(--muted);
    border-radius:8px;
    padding:7px 10px;
    font-size:10px;
    font-weight:800;
    cursor:pointer;
    white-space:nowrap;
}
.tech-toggle:hover,
.tech-toggle.is-active {
    color:#79c0ff;
    border-color:rgba(88,166,255,.45);
    background:rgba(88,166,255,.08);
}

/* LEGEND */
.visual-legend {
    display:flex;
    flex-wrap:wrap;
    gap:6px;
    margin-bottom:7px;
}
.legend-item {
    display:inline-flex;
    gap:5px;
    align-items:center;
    padding:5px 8px;
    border:1px solid var(--border);
    border-radius:999px;
    background:var(--card);
    color:var(--muted);
    font-size:9px;
}
.legend-item b { font-size:9px; }
.legend-limit b { color:#d2a8ff; }
.legend-free b { color:#7ee787; }
.legend-paid b { color:#d2a8ff; }
.legend-partial b { color:#e3b341; }

/* MAIN TABLE */
.table-card {
    background:var(--card);
    border:1px solid var(--border);
    border-radius:14px;
    overflow:hidden;
    margin-bottom:12px;
}
.table-scroll { overflow-x:auto; }
table {
    width:100%;
    min-width:1280px;
    border-collapse:separate;
    border-spacing:0;
    table-layout:fixed;
    font-size:11px;
}
th,td {
    padding:6px 6px;
    border-right:1px solid var(--border);
    border-bottom:1px solid var(--border);
    text-align:right;
    vertical-align:middle;
    white-space:nowrap;
}
th:last-child,td:last-child { border-right:0; }
tbody tr:last-child td { border-bottom:0; }
thead th {
    background:#1c2128;
    color:#c9d1d9;
    font-weight:800;
    line-height:1.15;
}
thead tr:first-child th {
    background:#21262d;
    color:var(--text);
    text-align:center;
}
thead .group-limit {
    color:#d2a8ff !important;
    background:rgba(188,140,255,.10) !important;
}
thead .group-actual {
    color:#7ee787 !important;
    background:rgba(46,160,67,.11) !important;
}
thead .group-max {
    color:#ffa657 !important;
    background:rgba(219,109,40,.11) !important;
}
th.sortable { cursor:pointer; user-select:none; }
th.sortable:hover { color:var(--blue); }
tbody tr:hover td { background:rgba(88,166,255,.035); }

.model-head,.model-cell {
    width:16%;
    text-align:left;
    position:sticky;
    left:0;
    z-index:2;
}
.model-head { z-index:5; background:#21262d !important; }
.model-cell { background:var(--card); }
.model-name { font-size:13px; font-weight:850; }
.provider { color:var(--blue); font-size:9px; font-weight:700; margin-top:1px; }
.model-meta { color:var(--muted); font-size:9px; margin-top:1px; }
.module-badges {
    display:flex;
    flex-wrap:wrap;
    gap:2px;
    margin-top:3px;
}
.module-badge {
    padding:1px 4px;
    border:1px solid rgba(88,166,255,.24);
    border-radius:4px;
    color:#79c0ff;
    font-size:8px;
    font-weight:800;
}
.module-badge.final { color:#d2a8ff; border-color:rgba(188,140,255,.30); }

.money strong { display:block; font-size:12px; }
.coverage {
    display:block;
    color:var(--muted);
    font-size:8px;
    margin-top:1px;
}
.free-cell {
    color:#7ee787;
    background:rgba(46,160,67,.055);
}
.paid-cell {
    color:#d2a8ff;
    background:rgba(188,140,255,.055);
}
.max-cell { box-shadow:inset 0 2px 0 rgba(219,109,40,.18); }

.main-intel-cell {
    width:7%;
    text-align:center;
    white-space:normal;
}
.main-intel-cell strong {
    display:block;
    font-size:14px;
    color:#d2a8ff;
}
.main-intel-cell span {
    display:block;
    margin-top:1px;
    color:var(--muted);
    font-size:8px;
    line-height:1.15;
}

.view-cell {
    width:25%;
    padding:6px !important;
    text-align:left;
    white-space:normal;
}

/* 8 числових колонок: тарифи, ліміти та 4 SITCAR-вартості. */
#modelsTable tbody td:nth-child(n+3):nth-child(-n+10) {
    width:6.5%;
}

#modelsTable thead tr:nth-child(2) th {
    width:6.5%;
}

#modelsTable thead tr:first-child th:last-child {
    width:25%;
}

/* =========================================================
   RESULTS — 2 MODULE BLOCKS PER ROW
   ========================================================= */
.results-main-grid {
    display:grid;
    grid-template-columns:repeat(2,minmax(0,1fr));
    gap:5px;
    width:100%;
}

.result-main-card {
    min-width:0;
    display:grid;
    grid-template-columns:minmax(0,1fr) auto;
    align-items:stretch;
    min-height:42px;
    border:1px solid rgba(139,148,158,.24);
    border-radius:7px;
    background:rgba(255,255,255,.018);
    overflow:hidden;
}

.result-main-card:hover {
    border-color:rgba(88,166,255,.42);
    background:rgba(88,166,255,.035);
}

.result-main-label {
    min-width:0;
    justify-content:center;
    text-align:center;
    display:flex;
    align-items:center;
    gap:5px;
    padding:5px 7px;
}

.result-main-code {
    flex:0 0 auto;
    color:#79c0ff;
    font-size:9px;
    font-weight:900;
}

.result-main-name {
    min-width:0;
    color:#c9d1d9;
    font-size:10px;
    font-weight:650;
    line-height:1.2;
    white-space:normal;
    overflow:visible;
    text-overflow:clip;
}

.result-main-actions {
    display:flex;
    align-items:stretch;
    justify-content:center;
}

.result-main-btn {
    min-width:58px;
    display:flex;
    align-items:center;
    justify-content:center;
    padding:5px 7px;
    border-left:1px solid rgba(139,148,158,.22);
    color:#c9d1d9;
    text-decoration:none;
    font-size:10px;
    font-weight:900;
    letter-spacing:.02em;
    transition:background .12s ease,color .12s ease;
}

.result-main-btn.html {
    color:#79c0ff;
    background:rgba(88,166,255,.075);
}
.result-main-btn.html:hover { background:rgba(88,166,255,.17); }

.result-main-btn.pdf {
    color:#ff7b72;
    background:rgba(248,81,73,.065);
}
.result-main-btn.pdf:hover { background:rgba(248,81,73,.15); }

/* Якщо PDF/HTML відсутній — одна кнопка не прилипає до краю. */
.result-main-card.single-format .result-main-actions {
    min-width:118px;
    align-items:center;
    padding:4px 8px;
}

.result-main-card.single-format .result-main-btn {
    width:82px;
    min-width:82px;
    min-height:28px;
    border:1px solid rgba(139,148,158,.24);
    border-radius:6px;
}

.result-main-card.final-result {
    border-color:rgba(188,140,255,.30);
    background:rgba(188,140,255,.04);
}
.result-main-card.final-result .result-main-code { color:#d2a8ff; }

/* Старі result-link використовуються у технічній панелі. */
.results-list {
    display:flex;
    flex-wrap:wrap;
    gap:3px;
}
.result-link {
    display:inline-flex;
    align-items:center;
    gap:4px;
    max-width:100%;
    padding:4px 5px;
    border:1px solid rgba(88,166,255,.26);
    border-radius:6px;
    background:rgba(88,166,255,.07);
    color:#c9d1d9;
    text-decoration:none;
    font-size:8px;
    line-height:1.1;
}
.result-link:hover { border-color:#58a6ff; background:rgba(88,166,255,.12); }
.result-link.pdf { border-color:rgba(248,81,73,.28); background:rgba(248,81,73,.06); }
.result-code { color:#79c0ff; font-weight:900; }
.result-link.pdf .result-code { color:#ff7b72; }
.result-link:not(.pdf) .result-code { color:#79c0ff; }
.result-name { overflow:hidden; text-overflow:ellipsis; }
.muted { color:var(--muted); }

@media(max-width:1500px) and (min-width:761px) {
    .view-cell { width:25%; }
    #modelsTable thead tr:first-child th:last-child { width:25%; }

    .result-main-btn {
        min-width:54px;
        padding:5px 6px;
        font-size:9px;
    }

    .result-main-name {
        font-size:9px;
        line-height:1.18;
    }

    .result-main-card.single-format .result-main-actions { min-width:108px; }
    .result-main-card.single-format .result-main-btn { width:78px; min-width:78px; }
}


/* OTHER / REJECTED MODELS */
.other-models-section {
    margin:14px 0 12px;
    background:var(--card);
    border:1px solid var(--border);
    border-radius:14px;
    overflow:hidden;
}
.other-models-head {
    padding:15px 17px 11px;
    background:linear-gradient(135deg,rgba(188,140,255,.08),rgba(88,166,255,.035));
    border-bottom:1px solid var(--border);
}
.other-models-head h2 { margin:0; font-size:20px; }
.other-models-head p { margin:6px 0 0; color:var(--muted); font-size:11px; max-width:1250px; }
.other-table-wrap { overflow-x:auto; }
.other-models-table {
    min-width:1180px;
    width:100%;
    table-layout:auto;
    font-size:10px;
}
.other-models-table th { text-align:center; }
.other-models-table td { padding:8px 9px; white-space:normal; vertical-align:top; }
.other-models-table td:nth-child(3),
.other-models-table td:nth-child(4),
.other-models-table td:nth-child(5),
.other-models-table td:nth-child(6) { text-align:right; white-space:nowrap; }
.other-model-name { min-width:160px; text-align:left !important; }
.other-model-name strong { display:block; font-size:11px; color:var(--text); }
.other-model-name span { display:block; margin-top:2px; color:var(--blue); font-size:8px; }
.intel-cell { min-width:110px; text-align:center !important; }
.intel-cell strong { display:block; font-size:14px; color:#d2a8ff; }
.intel-cell span { display:block; margin-top:2px; color:var(--muted); font-size:8px; }
.candidate-status {
    display:inline-flex;
    padding:3px 6px;
    border-radius:999px;
    border:1px solid rgba(227,179,65,.28);
    background:rgba(227,179,65,.08);
    color:#e3b341;
    font-size:8px;
    font-weight:800;
    white-space:nowrap;
}
.other-comment { min-width:300px; text-align:left !important; color:#c9d1d9; line-height:1.35; }
.observed-note { display:block; margin-top:4px; color:#7ee787; font-weight:700; }
.other-models-note {
    padding:9px 13px;
    border-top:1px solid var(--border);
    color:var(--muted);
    font-size:9px;
    line-height:1.45;
}
.other-models-note strong { color:#c9d1d9; }

/* TECHNICAL PANEL */
.technical-panel {
    display:none;
    margin:12px 0 16px;
}
.technical-panel.visible { display:block; }
.technical-panel-title {
    display:flex;
    align-items:flex-end;
    justify-content:space-between;
    gap:10px;
    margin-bottom:7px;
}
.technical-panel-title h2 { margin:0; font-size:18px; }
.technical-panel-title p { margin:0; color:var(--muted); font-size:10px; }
.tech-model-details {
    border:1px solid var(--border);
    border-radius:12px;
    background:var(--card);
    margin:6px 0;
    overflow:hidden;
}
.tech-model-details > summary {
    cursor:pointer;
    list-style:none;
    display:flex;
    justify-content:space-between;
    gap:12px;
    align-items:center;
    padding:9px 11px;
    background:#1c2128;
}
.tech-model-details > summary::-webkit-details-marker { display:none; }
.tech-model-details > summary strong { display:block; font-size:12px; }
.tech-model-details > summary small { color:var(--blue); font-size:9px; }
.tech-summary-meta { color:var(--muted); font-size:9px; text-align:right; }
.tech-model-body { padding:8px; border-top:1px solid var(--border); }
.tech-grid {
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
    gap:6px;
}
.tech-module-card {
    border:1px solid rgba(255,255,255,.07);
    border-radius:9px;
    background:rgba(255,255,255,.018);
    padding:8px;
    min-width:0;
}
.tech-module-head {
    display:flex;
    justify-content:space-between;
    gap:8px;
    align-items:flex-start;
    padding-bottom:5px;
    margin-bottom:5px;
    border-bottom:1px solid rgba(255,255,255,.06);
}
.tech-module-head strong { font-size:10px; }
.tech-code { color:#79c0ff; font-size:9px; font-weight:900; margin-right:4px; }
.tech-date { color:var(--muted); font-size:8px; white-space:nowrap; }
.tech-metrics-grid {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:2px 7px;
}
.tech-metric {
    display:flex;
    justify-content:space-between;
    gap:6px;
    padding:2px 0;
    border-bottom:1px solid rgba(255,255,255,.035);
    font-size:8px;
}
.tech-metric span { color:var(--muted); }
.tech-metric strong { font-size:8px; text-align:right; }
.tech-metric.actual-cost strong { color:#7ee787; }
.tech-metric.max-cost strong { color:#ffa657; }
.tech-links {
    display:flex;
    flex-wrap:wrap;
    gap:3px;
    margin-top:6px;
}
.tech-links .result-link { font-size:8px; }
.tech-extra-files {
    display:flex;
    flex-wrap:wrap;
    align-items:center;
    gap:4px;
    margin-top:8px;
    padding-top:7px;
    border-top:1px solid rgba(255,255,255,.06);
}
.tech-extra-files > span {
    color:var(--muted);
    font-size:8px;
    font-weight:800;
    margin-right:2px;
}

/* INFO */
.info-card {
    background:var(--card);
    border:1px solid var(--border);
    border-radius:14px;
    padding:17px;
    margin-bottom:12px;
}
.info-card h2 { margin:0 0 8px; font-size:19px; }
.info-card h3 { margin:18px 0 7px; font-size:14px; }
.info-card p,.info-card li { color:#c9d1d9; font-size:12px; }
.info-card ul { margin:8px 0; padding-left:20px; }
.info-card li { margin:5px 0; }
.formula {
    display:grid;
    grid-template-columns:repeat(2,minmax(0,1fr));
    gap:7px;
    margin:10px 0;
}
.formula-box {
    border:1px solid var(--border);
    background:rgba(255,255,255,.018);
    border-radius:9px;
    padding:10px;
    font-size:11px;
    color:#c9d1d9;
}
.formula-box strong { display:block; margin-bottom:3px; color:var(--blue); }
.formula-box.free { background:rgba(46,160,67,.055); border-color:rgba(63,185,80,.22); }
.formula-box.free strong { color:#7ee787; }
.formula-box.paid { background:rgba(188,140,255,.055); border-color:rgba(188,140,255,.22); }
.formula-box.paid strong { color:#d2a8ff; }
code {
    background:rgba(110,118,129,.16);
    border:1px solid rgba(110,118,129,.22);
    border-radius:4px;
    padding:1px 4px;
    color:#d2a8ff;
}
.footer { color:var(--muted); text-align:center; font-size:10px; }

/* =========================================================
   TABLET + MOBILE RESPONSIVE LAYOUT
   ========================================================= */

/* Tablet and mobile: convert the wide benchmark into readable cards. */
@media(max-width:1100px) {
    body { overflow-x:hidden; }
    .container { width:calc(100% - 20px); padding-top:10px; }

    .hero { padding:16px; }
    .hero-top { gap:10px; }
    h1 { font-size:clamp(25px,4.3vw,34px); }
    .hero p { font-size:12px; max-width:none; }
    .status { font-size:10px; padding:6px 9px; }

    .summary-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }

    .toolbar {
        display:grid;
        grid-template-columns:minmax(0,1fr) auto;
        align-items:center;
    }
    .search { min-width:0; grid-column:1 / -1; }
    .search input { font-size:16px; }
    .toolbar-note { font-size:9px; }
    .tech-toggle { justify-self:end; }

    .visual-legend {
        display:grid;
        grid-template-columns:repeat(2,minmax(0,1fr));
    }
    .legend-item {
        width:100%;
        border-radius:8px;
        align-items:flex-start;
        line-height:1.3;
    }

    /* ---------- MAIN BENCHMARK ---------- */
    .table-card { background:transparent; border:0; overflow:visible; }
    .table-scroll { overflow:visible; }

    #modelsTable {
        display:block;
        width:100%;
        min-width:0;
        table-layout:auto;
        font-size:11px;
    }
    #modelsTable colgroup,
    #modelsTable thead { display:none; }

    #modelsTable tbody {
        display:grid;
        width:100%;
        grid-template-columns:repeat(2,minmax(0,1fr));
        gap:9px;
    }

    #modelsTable tbody .model-row {
        display:block;
        min-width:0;
        width:100%;
        background:var(--card);
        border:1px solid var(--border);
        border-radius:13px;
        overflow:hidden;
        margin:0;
    }

    #modelsTable tbody .model-row td {
        display:flex;
        width:100% !important;
        min-width:0;
        align-items:center;
        justify-content:space-between;
        gap:10px;
        padding:7px 10px;
        border-right:0;
        border-bottom:1px solid rgba(255,255,255,.06);
        white-space:normal;
        text-align:right;
        position:static;
    }
    #modelsTable tbody .model-row td:last-child { border-bottom:0; }

    #modelsTable tbody .model-row td::before {
        content:"";
        flex:0 1 48%;
        min-width:105px;
        text-align:left;
        color:var(--muted);
        font-size:9px;
        line-height:1.2;
        font-weight:750;
    }

    #modelsTable tbody .model-row .model-cell {
        position:static;
        display:block;
        width:100% !important;
        background:linear-gradient(135deg,rgba(88,166,255,.11),rgba(188,140,255,.04));
        text-align:left;
        padding:12px;
    }
    #modelsTable tbody .model-row .model-cell::before { display:none; }
    #modelsTable .model-name { font-size:16px; overflow-wrap:anywhere; }
    #modelsTable .provider,
    #modelsTable .model-meta { font-size:9px; }
    #modelsTable .module-badge { font-size:8px; }

    #modelsTable tbody .model-row td:nth-child(2)::before { content:"AA Intelligence"; }
    #modelsTable tbody .model-row td:nth-child(3)::before { content:"Input / 1M"; }
    #modelsTable tbody .model-row td:nth-child(4)::before { content:"Output / 1M"; }
    #modelsTable tbody .model-row td:nth-child(5)::before { content:"Max context"; }
    #modelsTable tbody .model-row td:nth-child(6)::before { content:"Max output"; }
    #modelsTable tbody .model-row td:nth-child(7)::before { content:"БЕЗПЛАТНА · фактично"; }
    #modelsTable tbody .model-row td:nth-child(8)::before { content:"ПОВНА"; }
    #modelsTable tbody .model-row td:nth-child(9)::before { content:"БЕЗПЛАТНА · MAX"; }
    #modelsTable tbody .model-row td:nth-child(10)::before { content:"ПОВНА · MAX"; }
    #modelsTable tbody .model-row td:nth-child(11)::before { content:"Результати"; }

    #modelsTable .main-intel-cell {
        width:100% !important;
        text-align:right;
        white-space:normal;
    }
    #modelsTable .main-intel-cell strong { font-size:13px; }
    #modelsTable .main-intel-cell span { font-size:8px; }
    #modelsTable .money { display:flex !important; }
    #modelsTable .money > strong,
    #modelsTable .money > .coverage { display:block; }

    #modelsTable .view-cell {
        display:block !important;
        width:100% !important;
        text-align:left !important;
        padding:9px !important;
    }
    #modelsTable .view-cell::before {
        display:block;
        margin-bottom:6px;
    }

    .results-main-grid {
        grid-template-columns:repeat(2,minmax(0,1fr));
        gap:5px;
    }
    .result-main-card {
        display:block;
        min-height:0;
    }
    .result-main-label {
        min-height:34px;
        padding:5px 6px;
        border-bottom:1px solid rgba(139,148,158,.18);
    }
    .result-main-code { font-size:9px; }
    .result-main-name { font-size:9px; }
    .result-main-actions {
        width:100%;
        min-width:0 !important;
        padding:0 !important;
    }
    .result-main-btn {
        flex:1 1 50%;
        width:auto !important;
        min-width:0 !important;
        min-height:32px !important;
        border:0;
        border-radius:0 !important;
        font-size:9px;
    }
    .result-main-btn + .result-main-btn {
        border-left:1px solid rgba(139,148,158,.20);
    }
    .result-main-card.single-format .result-main-btn {
        flex:0 1 80%;
        margin:4px auto;
        min-height:29px !important;
        border:1px solid rgba(139,148,158,.24);
        border-radius:6px !important;
    }

    /* ---------- OTHER / REJECTED MODELS ---------- */
    .other-models-section { overflow:visible; }
    .other-models-head { padding:14px; }
    .other-models-head h2 { font-size:18px; }
    .other-models-head p { font-size:10px; }
    .other-table-wrap { overflow:visible; padding:8px; }

    .other-models-table {
        display:block;
        min-width:0;
        width:100%;
        table-layout:auto;
    }
    .other-models-table thead { display:none; }
    .other-models-table tbody {
        display:grid;
        grid-template-columns:repeat(2,minmax(0,1fr));
        gap:8px;
        width:100%;
    }
    .other-models-table tr {
        display:block;
        min-width:0;
        border:1px solid rgba(139,148,158,.22);
        border-radius:10px;
        overflow:hidden;
        background:rgba(255,255,255,.015);
    }
    .other-models-table td,
    .other-models-table td:nth-child(3),
    .other-models-table td:nth-child(4),
    .other-models-table td:nth-child(5),
    .other-models-table td:nth-child(6) {
        display:grid;
        grid-template-columns:minmax(105px,38%) minmax(0,1fr);
        gap:8px;
        align-items:start;
        width:100%;
        min-width:0;
        padding:7px 9px;
        border-right:0;
        border-bottom:1px solid rgba(255,255,255,.055);
        text-align:right !important;
        white-space:normal;
        overflow-wrap:anywhere;
    }
    .other-models-table td:last-child { border-bottom:0; }
    .other-models-table td::before {
        display:block;
        text-align:left;
        color:var(--muted);
        font-size:8px;
        font-weight:800;
        line-height:1.25;
    }
    .other-models-table td:nth-child(2)::before { content:"AA Intelligence"; }
    .other-models-table td:nth-child(3)::before { content:"Input / 1M"; }
    .other-models-table td:nth-child(4)::before { content:"Output / 1M"; }
    .other-models-table td:nth-child(5)::before { content:"Max context"; }
    .other-models-table td:nth-child(6)::before { content:"Max output"; }
    .other-models-table td:nth-child(7)::before { content:"Статус"; }
    .other-models-table td:nth-child(8)::before { content:"Коментар"; }

    .other-models-table .other-model-name {
        display:block;
        width:100%;
        min-width:0;
        padding:10px;
        text-align:left !important;
        background:linear-gradient(135deg,rgba(188,140,255,.08),rgba(88,166,255,.035));
    }
    .other-models-table .other-model-name::before { display:none; }
    .other-model-name strong { font-size:12px; overflow-wrap:anywhere; }
    .other-model-name span { font-size:8px; }

    .other-models-table .intel-cell {
        display:grid;
        width:100%;
        min-width:0;
        text-align:right !important;
    }
    .intel-cell strong { font-size:13px; }
    .candidate-status {
        justify-self:end;
        white-space:normal;
        text-align:center;
        line-height:1.2;
    }
    .other-models-table .other-comment {
        display:block;
        width:100%;
        min-width:0;
        text-align:left !important;
        line-height:1.4;
    }
    .other-models-table .other-comment::before {
        content:"Коментар для SITCAR";
        display:block;
        margin-bottom:4px;
    }
    .observed-note { margin-top:5px; }
    .other-models-note { font-size:9px; padding:9px 11px; }

    /* ---------- TECH / INFO ---------- */
    .technical-panel-title { display:block; }
    .technical-panel-title p { margin-top:3px; }
    .tech-model-details > summary { display:block; }
    .tech-summary-meta { display:block; text-align:left; margin-top:4px; }
    .tech-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .tech-metrics-grid { grid-template-columns:1fr 1fr; }
    .formula { grid-template-columns:1fr; }
}

/* Portrait tablets and large phones: one model per row for better readability. */
@media(max-width:900px) {
    .container { width:calc(100% - 16px); }
    #modelsTable tbody { grid-template-columns:1fr; }
    .other-models-table tbody { grid-template-columns:1fr; }
    .tech-grid { grid-template-columns:1fr; }
}

/* Phones. */
@media(max-width:600px) {
    .container { width:calc(100% - 12px); padding-top:7px; }
    .hero { padding:14px 12px; }
    h1 { font-size:25px; }
    .hero p { font-size:11px; }
    .status { width:100%; justify-content:center; }

    .summary-grid { grid-template-columns:1fr 1fr; gap:6px; }
    .summary-card { padding:8px 9px; }
    .summary-value { font-size:16px; }
    .summary-value.small { font-size:10px; overflow-wrap:anywhere; }

    .toolbar {
        grid-template-columns:1fr;
        padding:7px;
    }
    .search { grid-column:1; }
    .toolbar-note { display:none; }
    .tech-toggle { justify-self:stretch; text-align:center; }

    .visual-legend { grid-template-columns:1fr 1fr; gap:5px; }
    .legend-item { padding:5px 6px; font-size:8px; }
    .legend-item b { font-size:8px; }

    #modelsTable tbody .model-row td {
        padding:7px 9px;
        gap:8px;
    }
    #modelsTable tbody .model-row td::before {
        min-width:95px;
        font-size:8px;
    }
    #modelsTable .model-name { font-size:15px; }
    #modelsTable .view-cell { padding:8px !important; }

    .results-main-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }

    .other-models-head { padding:12px; }
    .other-models-head h2 { font-size:17px; }
    .other-table-wrap { padding:6px; }
    .other-models-table td,
    .other-models-table td:nth-child(3),
    .other-models-table td:nth-child(4),
    .other-models-table td:nth-child(5),
    .other-models-table td:nth-child(6) {
        grid-template-columns:minmax(92px,36%) minmax(0,1fr);
        padding:6px 8px;
    }
    .candidate-status { font-size:7px; padding:3px 5px; }

    .tech-metrics-grid { grid-template-columns:1fr 1fr; }
    .info-card { padding:13px; }
    .info-card h2 { font-size:17px; }
    .info-card p,.info-card li { font-size:11px; }
}

/* Very narrow phones. */
@media(max-width:430px) {
    .summary-grid { grid-template-columns:1fr; }
    .visual-legend { grid-template-columns:1fr; }
    .results-main-grid { grid-template-columns:1fr; }
    .tech-metrics-grid { grid-template-columns:1fr; }

    #modelsTable tbody .model-row td {
        display:grid;
        grid-template-columns:minmax(95px,40%) minmax(0,1fr);
        align-items:start;
    }
    #modelsTable tbody .model-row td::before {
        min-width:0;
    }
    #modelsTable .view-cell {
        display:block !important;
    }

    .other-models-table td,
    .other-models-table td:nth-child(3),
    .other-models-table td:nth-child(4),
    .other-models-table td:nth-child(5),
    .other-models-table td:nth-child(6) {
        grid-template-columns:minmax(86px,39%) minmax(0,1fr);
    }
}

</style>
</head>
<body>
<div class="container">

<section class="hero">
    <div class="hero-top">
        <div>
            <div class="eyebrow">SITCAR System · multi-module AI benchmark</div>
            <h1>SITCAR Огляд Моделей ціна якість</h1>
            <p>
                Порівняння моделей за реально виконаними запитами SITCAR. Вартість більше не прогнозується як ORG × 3 / ORG × 10:
                для кожної моделі складаються фактичні вартості саме тих модулів, які реально були протестовані.
            </p>
        </div>
        <span class="status">✓ Реальні результати по модулях</span>
    </div>
</section>

<div class="summary-grid">
    <div class="summary-card">
        <div class="summary-label">Моделей</div>
        <div class="summary-value">@@MODEL_COUNT@@</div>
    </div>
    <div class="summary-card">
        <div class="summary-label">Результати HTML / PDF</div>
        <div class="summary-value small">@@HTML_COUNT@@ HTML · @@PDF_COUNT@@ PDF</div>
    </div>
    <div class="summary-card">
        <div class="summary-label">Моделей з >1 модулем</div>
        <div class="summary-value">@@MULTI_COUNT@@</div>
    </div>
    <div class="summary-card">
        <div class="summary-label">Податок / націнка</div>
        <div class="summary-value small">@@TAX_NOTE@@</div>
    </div>
</div>

<div class="toolbar">
    <div class="search">
        <input id="modelSearch" type="text" placeholder="Пошук моделі або модуля...">
    </div>
    <div class="toolbar-note">Натисни колонку для сортування</div>
    <button id="techToggle" class="tech-toggle" type="button" aria-pressed="false">+ технічні дані</button>
</div>

<div class="visual-legend">
    <span class="legend-item legend-limit"><b>ВЕРХНЯ МЕЖА</b> model limits / MAX output</span>
    <span class="legend-item legend-free"><b>БЕЗПЛАТНА</b> сума реальних Free-модулів</span>
    <span class="legend-item legend-paid"><b>ПОВНА</b> 9 модулів + окремий FULL-запит</span>
    <span class="legend-item legend-partial"><b>ЧАСТКОВО</b> модель протестована не на всіх напрямках</span>
</div>

<div class="table-card">
<div class="table-scroll">
<table id="modelsTable">
<colgroup>
    <col style="width:16%">
    <col style="width:7%">
    <col style="width:6.5%">
    <col style="width:6.5%">
    <col style="width:6.5%">
    <col style="width:6.5%">
    <col style="width:6.5%">
    <col style="width:6.5%">
    <col style="width:6.5%">
    <col style="width:6.5%">
    <col style="width:25%">
</colgroup>
<thead>
<tr>
    <th rowspan="2" class="model-head sortable" data-column="0">Назва моделі</th>
    <th rowspan="2" class="sortable" data-column="1">AA Intelligence</th>
    <th colspan="2">Тариф за 1M токенів</th>
    <th colspan="2" class="group-limit">Верхня межа моделі</th>
    <th colspan="2" class="group-actual">SITCAR · фактичний output</th>
    <th colspan="2" class="group-max">SITCAR · якщо output = MAX</th>
    <th rowspan="2">Результати</th>
</tr>
<tr>
    <th class="sortable" data-column="2">Input</th>
    <th class="sortable" data-column="3">Output</th>
    <th class="sortable" data-column="4">Max context</th>
    <th class="sortable" data-column="5">Max output</th>
    <th class="sortable" data-column="6">Безплатна</th>
    <th class="sortable" data-column="7">Повна</th>
    <th class="sortable" data-column="8">Безплатна MAX</th>
    <th class="sortable" data-column="9">Повна MAX</th>
</tr>
</thead>
<tbody>
@@ROWS@@
</tbody>
</table>
</div>
</div>

@@OTHER_MODELS@@

<section id="technicalPanel" class="technical-panel">
    <div class="technical-panel-title">
        <h2>Технічні дані по кожному модулю</h2>
        <p>Фактичні токени, latency, ціна одного конкретного модульного запиту та MAX-сценарій.</p>
    </div>
    @@TECHNICAL@@
</section>

<section class="info-card">
    <h2>Як тепер рахується вартість</h2>
    <p>
        Кожен напрямок SITCAR рахується за його власним фактичним API-запуском. Тобто SYS, STR, INN тощо більше не оцінюються через множення вартості ORG.
        Якщо для моделі є лише ORG — у розрахунку присутній лише ORG. Якщо є ORG + SYS + STR — складаються фактичні вартості саме цих трьох запусків.
    </p>

    <div class="formula">
        <div class="formula-box free">
            <strong>Безплатна · фактичний output</strong>
            Сума фактичної вартості наявних модулів <code>ORG + SYS + STR</code>. Якщо протестована лише частина — біля суми показується покриття, наприклад <code>1/3 мод. · частково</code>.
        </div>
        <div class="formula-box paid">
            <strong>Повна · фактичний output</strong>
            Сума фактичних вартостей дев'яти напрямків <code>ORG, SYS, STR, INN, TAL, CUL, ADA, REP, ARC</code> плюс окремого фінального API-запиту <code>FULL</code>. За повного комплекту це <strong>10 реальних API-запусків</strong>. Непротестовані запуски не вигадуються і не екстраполюються.
        </div>
        <div class="formula-box free">
            <strong>Безплатна · MAX</strong>
            Для кожного наявного Free-модуля береться його реальний input, але output підставляється рівним максимальному output моделі. Після цього модульні значення складаються.
        </div>
        <div class="formula-box paid">
            <strong>Повна · MAX</strong>
            Такий самий MAX-розрахунок для всіх реально наявних запусків повної діагностики, включно з <code>FULL</code>, якщо є його HTML. Це верхній сценарій для вже протестованого набору, а не прогноз вартості відсутніх запусків.
        </div>
    </div>

    <h3>Важливо для інтерпретації</h3>
    <ul>
        <li>У наступних напрямках вхід збільшується, бо до відповідей поточного розділу можуть додаватися результати попередніх напрямків, документи та метрики.</li>
        <li>Тому коректніше використовувати реальну вартість кожного окремого модуля, а не множити ORG на кількість запитів.</li>
        <li>Позначка <strong>частково</strong> означає, що для цієї моделі ще немає всіх необхідних тестів; показана сума — лише за реально наявні HTML-запуски.</li>
        <li>PDF-файли з папки <code>PDF_RESULTS</code> показуються поруч з HTML як альтернативний формат результату, але сам PDF не додає окремої вартості — розрахунок виконується за HTML API-запуском.</li>
        <li>У v15 result HTML працюють у read-only режимі: builder їх не переписує, а лише читає для метрик і формує <code>index.html</code>.</li>
        <li>Колонка <strong>AA Intelligence</strong> показує актуальний Artificial Analysis Intelligence Index для наших протестованих моделей; вищий бал = сильніша модель.</li>
        <li><code>FULL.html</code> — це <strong>окремий фінальний API-запит</strong>, тому його фактичні токени, latency і вартість входять у Повну версію. <code>FULL.pdf</code> — лише альтернативний формат цього самого результату і окремої вартості не додає.</li>
        <li>Окрема презентація / фінальний загальний API-запит не додається до ціни автоматично без реального HTML тестового файла. Якщо HTML з кодом FINAL / PRES / PRESENTATION з'явиться, скрипт підхопить його.</li>
        <li>Сумарний час у назві моделі — сума latency усіх реально протестованих HTML-запусків повної діагностики: 9 напрямків + <code>FULL</code>, якщо він є.</li>
        <li>Податок / націнка (20% для всіх провайдерів): @@TAX_NOTE@@.</li>
        <li>Знайдено файлів до дедуплікації: @@ARTIFACT_COUNT@@; старіших повторів model+module+format приховано: @@DUP_COUNT@@.</li>
    </ul>
</section>

<div class="footer">SITCAR System · LLM multi-module benchmark</div>
</div>

<script>
const searchInput = document.getElementById('modelSearch');
const table = document.getElementById('modelsTable');
const tbody = table.querySelector('tbody');
const techToggle = document.getElementById('techToggle');
const technicalPanel = document.getElementById('technicalPanel');

searchInput.addEventListener('input', function () {
    const value = this.value.trim().toLowerCase();
    tbody.querySelectorAll('.model-row').forEach(row => {
        const searchable = (row.dataset.model + ' ' + row.innerText).toLowerCase();
        row.style.display = searchable.includes(value) ? '' : 'none';
    });
});

techToggle.addEventListener('click', function () {
    const visible = technicalPanel.classList.toggle('visible');
    this.classList.toggle('is-active', visible);
    this.setAttribute('aria-pressed', visible ? 'true' : 'false');
    this.textContent = visible ? '− сховати технічні дані' : '+ технічні дані';
    if (visible && window.innerWidth <= 760) {
        technicalPanel.scrollIntoView({behavior:'smooth', block:'start'});
    }
});

let currentSortColumn = null;
let currentSortAscending = true;

document.querySelectorAll('th.sortable').forEach(th => {
    th.addEventListener('click', () => {
        const column = Number(th.dataset.column);
        if (currentSortColumn === column) {
            currentSortAscending = !currentSortAscending;
        } else {
            currentSortColumn = column;
            currentSortAscending = true;
        }
        sortTable(column, currentSortAscending);
    });
});

function sortTable(column, ascending) {
    const rows = Array.from(tbody.querySelectorAll('.model-row'));
    rows.sort((a,b) => {
        const cellA = a.children[column];
        const cellB = b.children[column];
        const valueA = cellA?.dataset.sort ?? cellA?.innerText ?? '';
        const valueB = cellB?.dataset.sort ?? cellB?.innerText ?? '';
        const numberA = Number(valueA);
        const numberB = Number(valueB);
        const aNum = valueA !== '' && Number.isFinite(numberA);
        const bNum = valueB !== '' && Number.isFinite(numberB);
        let result;
        if (aNum && bNum) result = numberA - numberB;
        else result = valueA.localeCompare(valueB, 'uk', {numeric:true, sensitivity:'base'});
        return ascending ? result : -result;
    });
    rows.forEach(row => tbody.appendChild(row));
}
</script>
</body>
</html>'''

    replacements = {
        "@@ROWS@@": rows,
        "@@TECHNICAL@@": tech,
        "@@OTHER_MODELS@@": other_models,
        "@@MODEL_COUNT@@": str(len(items)),
        "@@HTML_COUNT@@": str(used_html_count),
        "@@PDF_COUNT@@": str(used_pdf_count),
        "@@MULTI_COUNT@@": str(models_with_more_than_org),
        "@@TAX_NOTE@@": html.escape(tax_note),
        "@@ARTIFACT_COUNT@@": str(artifacts_count),
        "@@DUP_COUNT@@": str(hidden_duplicates),
    }

    result = html_template
    for key, value in replacements.items():
        result = result.replace(key, value)

    return result


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    print()
    print("=" * 76)
    print("SITCAR — MULTI-MODULE MODEL INDEX BUILDER v15 (HTML + PDF + FULL API + INTELLIGENCE)")
    print("=" * 76)
    print(f"Директорія: {BASE_DIR}")
    print(f"PDF_RESULTS: {PDF_RESULTS_DIR}")
    print(f"Files:       {FILES_DIR}")
    print()

    artifacts = discover_artifacts()

    if not artifacts:
        print("Не знайдено файлів за шаблоном MODEL_MODULE_YYYY-MM-DD_HH-MM-SS.html/pdf")
        print(f"Підтримувані модулі: {', '.join(ALL_CODES)}")
        return

    newest, hidden_duplicates = newest_by_model_module_format(artifacts)
    grouped = group_data(newest)
    all_items = sorted(grouped.values(), key=model_sort_key)
    # Усі реально протестовані моделі показуємо у верхній таблиці.
    # Fable більше НЕ ховаємо: якщо є HTML/PDF результат — він має бути видимим.
    items = list(all_items)

    html_count = sum(1 for a in artifacts if a.ext == "html")
    pdf_count = sum(1 for a in artifacts if a.ext == "pdf")

    print(f"Знайдено файлів: {len(artifacts)}")
    print(f"  HTML: {html_count}")
    print(f"  PDF:  {pdf_count}")
    print(f"Повторів приховано: {hidden_duplicates}")
    print(f"Основних моделей: {len(items)}")
    print(f"Відкинутих/інших у довіднику: {len(OTHER_MODEL_CATALOG)}")
    print()

    for item in items:
        modules = ", ".join(item["tested_modules"]) or "—"
        print(
            f"OK  {item['model_name']:<28} "
            f"[{modules}]  "
            f"час={fmt_duration(item['total_latency'])}  "
            f"факт={fmt_money(item['all_actual'])}"
        )

    index_html = build_index(
        items=items,
        all_items=all_items,
        artifacts_count=len(artifacts),
        hidden_duplicates=hidden_duplicates,
    )

    INDEX_FILE.write_text(index_html, encoding="utf-8")

    print()
    print("-" * 76)
    print("Result HTML не змінювалися (read-only); згенеровано лише index.html")
    print(f"ГОТОВО: {INDEX_FILE}")
    print("=" * 76)
    print()


if __name__ == "__main__":
    main()
