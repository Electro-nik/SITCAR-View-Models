from __future__ import annotations

import html as html_lib
import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# ============================================================
# НАЛАШТУВАННЯ
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "pdf_result"

SUPPORTED_EXTENSIONS = {".html", ".htm"}
TIMEOUT_SECONDS = 180

# Якщо True - PDF буде мати білий "документний" стиль,
# незалежно від темного стилю вихідного HTML.
CLEAN_DOCUMENT_STYLE = True


# ============================================================
# ЗАЛЕЖНОСТІ
# ============================================================

def ensure_package(import_name: str, pip_name: str | None = None) -> None:
    try:
        importlib.import_module(import_name)
        return
    except ImportError:
        pass

    package = pip_name or import_name

    print(f"[INFO] Встановлюю залежність: {package}")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", package],
        stdout=subprocess.DEVNULL,
    )


ensure_package("bs4", "beautifulsoup4")
ensure_package("markdown_it", "markdown-it-py")

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt


# ============================================================
# ПОШУК EDGE / CHROME / CHROMIUM
# ============================================================

def find_browser() -> Path:
    candidates: list[Path] = []

    for name in (
        "msedge",
        "msedge.exe",
        "chrome",
        "chrome.exe",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]

        for root in filter(None, roots):
            r = Path(root)
            candidates.extend(
                [
                    r / "Microsoft/Edge/Application/msedge.exe",
                    r / "Google/Chrome/Application/chrome.exe",
                    r / "Chromium/Application/chrome.exe",
                ]
            )

    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            ]
        )

    for p in candidates:
        try:
            if p.is_file():
                return p.resolve()
        except OSError:
            pass

    raise FileNotFoundError(
        "Не знайдено Microsoft Edge / Google Chrome / Chromium.\n"
        "На Windows достатньо встановленого Microsoft Edge."
    )


# ============================================================
# ВИТЯГУВАННЯ ТІЛЬКИ ВІДПОВІДІ МОДЕЛІ
# ============================================================

ANSWER_HEADING_MARKERS = (
    "ПОВНА ВІДПОВІДЬ МОДЕЛІ",
    "ВІДПОВІДЬ МОДЕЛІ",
    "FULL MODEL RESPONSE",
    "MODEL RESPONSE",
)


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().upper()


def extract_model_answer(source: Path) -> str:
    """
    Витягує ТІЛЬКИ текст відповіді моделі.

    Для твоїх HTML шукає конструкцію на кшталт:
        <section class="result-card">
            <h2>ПОВНА ВІДПОВІДЬ МОДЕЛІ</h2>
            <pre>...</pre>
        </section>

    Блоки Full API response, tokens, cost, attachments тощо
    у PDF НЕ потрапляють.
    """
    raw = source.read_bytes()

    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        raise RuntimeError("Не вдалося визначити кодування HTML")

    soup = BeautifulSoup(text, "html.parser")

    # --------------------------------------------------------
    # Варіант 1: знаходимо заголовок "ПОВНА ВІДПОВІДЬ МОДЕЛІ"
    # --------------------------------------------------------
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        heading_text = normalize_text(heading.get_text(" ", strip=True))

        if any(marker in heading_text for marker in ANSWER_HEADING_MARKERS):
            container = heading.find_parent(["section", "article", "div"])

            if container is not None:
                pre = container.find("pre")
                if pre is not None:
                    answer = pre.get_text()
                    answer = html_lib.unescape(answer).strip()
                    if answer:
                        return answer

            # Якщо pre лежить не у тому ж контейнері.
            next_pre = heading.find_next("pre")
            if next_pre is not None:
                # Не беремо pre, який знаходиться всередині <details>,
                # бо там у твоїх звітах лежить сирий API response.
                if next_pre.find_parent("details") is None:
                    answer = next_pre.get_text()
                    answer = html_lib.unescape(answer).strip()
                    if answer:
                        return answer

    # --------------------------------------------------------
    # Варіант 2: fallback для схожих HTML-шаблонів.
    # Беремо найбільший <pre>, але НЕ з <details>.
    # --------------------------------------------------------
    candidates: list[str] = []

    for pre in soup.find_all("pre"):
        if pre.find_parent("details") is not None:
            continue

        parent_text = ""
        parent = pre.find_parent(["section", "article", "div"])
        if parent is not None:
            parent_text = normalize_text(parent.get_text(" ", strip=True)[:300])

        # Відсікаємо технічні/API блоки.
        if "FULL API RESPONSE" in parent_text:
            continue
        if "RAW API" in parent_text:
            continue

        candidate = html_lib.unescape(pre.get_text()).strip()
        if len(candidate) >= 200:
            candidates.append(candidate)

    if candidates:
        return max(candidates, key=len)

    raise RuntimeError(
        'Не знайдено блок "ПОВНА ВІДПОВІДЬ МОДЕЛІ". '
        "Файл пропущено, щоб випадково не надрукувати весь HTML."
    )


# ============================================================
# MARKDOWN -> ЧИСТИЙ HTML
# ============================================================

def markdown_to_html(markdown_text: str) -> str:
    md = (
        MarkdownIt(
            "commonmark",
            {
                "html": False,
                "breaks": False,
                "linkify": False,
                "typographer": False,
            },
        )
        .enable("table")
        .enable("strikethrough")
    )

    rendered = md.render(markdown_text)

    # Додаткова постобробка таблиць.
    soup = BeautifulSoup(rendered, "html.parser")

    for table in soup.find_all("table"):
        first_row = table.find("tr")
        cols = len(first_row.find_all(["th", "td"])) if first_row else 0

        classes = list(table.get("class", []))

        if cols >= 7:
            classes.append("table-very-wide")
        elif cols >= 5:
            classes.append("table-wide")

        table["class"] = classes

    # Зовнішні посилання у PDF не повинні ламати верстку.
    for a in soup.find_all("a"):
        a["target"] = "_blank"
        a["rel"] = "noopener noreferrer"

    return str(soup)


# ============================================================
# ЧИСТИЙ СТИЛЬ PDF
# ============================================================

DOCUMENT_CSS = r"""
@page {
    size: A4 portrait;
    margin: 14mm 13mm 16mm 13mm;
}

* {
    box-sizing: border-box;
}

html {
    background: white;
}

body {
    margin: 0;
    background: #ffffff;
    color: #222f3e;
    font-family: "Segoe UI", Arial, "DejaVu Sans", sans-serif;
    font-size: 10.6pt;
    line-height: 1.52;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}

.document {
    width: 100%;
    max-width: 100%;
}

h1,
h2,
h3,
h4,
h5,
h6 {
    font-family: "Segoe UI", Arial, "DejaVu Sans", sans-serif;
    color: #245b86;
    line-height: 1.22;
    break-after: avoid-page;
    page-break-after: avoid;
}

h1 {
    font-size: 21pt;
    text-align: center;
    margin: 0 0 18px;
    padding: 0 0 11px;
    border-bottom: 2px solid #d7e5ef;
}

h2 {
    font-size: 15.5pt;
    margin: 22px 0 10px;
}

h3 {
    font-size: 12.8pt;
    margin: 18px 0 8px;
}

h4 {
    font-size: 11.2pt;
    margin: 15px 0 6px;
}

h5,
h6 {
    font-size: 10.7pt;
    margin: 13px 0 5px;
}

p {
    margin: 0 0 9px;
    orphans: 3;
    widows: 3;
}

strong {
    color: #172b3a;
    font-weight: 700;
}

em {
    color: #405261;
}

ul,
ol {
    margin: 5px 0 11px 22px;
    padding: 0;
}

li {
    margin: 3px 0;
}

li > p {
    margin: 2px 0;
}

hr {
    border: 0;
    border-top: 1px solid #d7e1e8;
    margin: 17px 0;
}

blockquote {
    margin: 12px 0;
    padding: 10px 13px;
    border-left: 4px solid #78a8ca;
    background: #f1f7fb;
    color: #334e62;
    break-inside: avoid-page;
}

a {
    color: #1d64a0;
    text-decoration: none;
    overflow-wrap: anywhere;
}

code {
    font-family: Consolas, "Courier New", monospace;
    font-size: 8.9pt;
    background: #f1f4f6;
    border: 1px solid #e2e7eb;
    border-radius: 3px;
    padding: 1px 3px;
    overflow-wrap: anywhere;
}

pre {
    margin: 10px 0 14px;
    padding: 10px 11px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: break-word;
    background: #f5f7f9;
    border: 1px solid #d8e0e6;
    border-radius: 5px;
    color: #263746;
    font-family: Consolas, "Courier New", monospace;
    font-size: 8.1pt;
    line-height: 1.42;
}

pre code {
    border: 0;
    background: transparent;
    padding: 0;
    font-size: inherit;
}

table {
    width: 100%;
    max-width: 100%;
    border-collapse: collapse;
    margin: 10px 0 16px;
    table-layout: fixed;
    font-size: 8.8pt;
    line-height: 1.35;
}

thead {
    display: table-header-group;
}

tr {
    break-inside: avoid-page;
    page-break-inside: avoid;
}

th,
td {
    border: 1px solid #cbd8e2;
    padding: 6px 7px;
    vertical-align: top;
    overflow-wrap: anywhere;
    word-break: normal;
}

th {
    background: #e8f1f7;
    color: #245b86;
    font-weight: 700;
}

tbody tr:nth-child(even) td {
    background: #fafcfd;
}

table.table-wide {
    font-size: 7.6pt;
}

table.table-wide th,
table.table-wide td {
    padding: 5px 5px;
}

table.table-very-wide {
    font-size: 6.8pt;
}

table.table-very-wide th,
table.table-very-wide td {
    padding: 4px 4px;
}

img,
svg {
    max-width: 100%;
    height: auto;
}

@media print {
    body {
        background: white !important;
    }
}
"""


def build_clean_html(answer_markdown: str, source_name: str) -> str:
    body = markdown_to_html(answer_markdown)

    # Якщо відповідь починається не з заголовка,
    # нічого штучно не додаємо: PDF містить саме відповідь моделі.
    return f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html_lib.escape(source_name)}</title>
<style>
{DOCUMENT_CSS}
</style>
</head>
<body>
<main class="document">
{body}
</main>
</body>
</html>
"""


# ============================================================
# ДРУК ЧИСТОГО HTML У PDF
# ============================================================

def print_html_to_pdf(browser: Path, clean_html: str, output_pdf: Path) -> None:
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    if output_pdf.exists():
        output_pdf.unlink()

    with tempfile.TemporaryDirectory(prefix="sitcar_answer_pdf_") as tmp:
        tmp_dir = Path(tmp)

        html_file = tmp_dir / "answer.html"
        html_file.write_text(clean_html, encoding="utf-8")

        profile = tmp_dir / "browser_profile"
        profile.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=1200",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={output_pdf.resolve()}",
            "--no-pdf-header-footer",
            "--print-to-pdf-no-header",
            html_file.resolve().as_uri(),
        ]

        if os.name != "nt":
            cmd.insert(1, "--no-sandbox")

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Браузер повернув код {result.returncode}\n"
                f"{result.stderr.strip()}"
            )

        if not output_pdf.exists():
            raise RuntimeError("PDF не був створений")

        if output_pdf.stat().st_size < 1000:
            raise RuntimeError("PDF створений, але має підозріло малий розмір")


# ============================================================
# ОБРОБКА ВСІХ HTML У ПОТОЧНІЙ ПАПЦІ
# ============================================================

def collect_html_files() -> list[Path]:
    return sorted(
        [
            p
            for p in BASE_DIR.iterdir()
            if p.is_file()
            and p.suffix.lower() in SUPPORTED_EXTENSIONS
            and not p.name.startswith("~$")
        ],
        key=lambda p: p.name.lower(),
    )


def convert_file(browser: Path, source: Path) -> Path:
    answer = extract_model_answer(source)

    if len(answer.strip()) < 100:
        raise RuntimeError("Знайдена відповідь надто коротка")

    clean_html = build_clean_html(answer, source.stem)

    output_pdf = OUTPUT_DIR / f"{source.stem}.pdf"
    print_html_to_pdf(browser, clean_html, output_pdf)

    return output_pdf


def main() -> int:
    print("=" * 76)
    print("SITCAR - HTML MODEL ANSWER -> CLEAN PDF")
    print("=" * 76)
    print(f"Вхідна папка : {BASE_DIR}")
    print(f"PDF папка    : {OUTPUT_DIR}")
    print()

    try:
        browser = find_browser()
    except Exception as exc:
        print(f"[ERROR] {exc}")
        if os.name == "nt":
            input("\nНатисни Enter...")
        return 1

    print(f"Браузер      : {browser}")
    print()

    files = collect_html_files()

    if not files:
        print("[INFO] У папці немає HTML-файлів.")
        if os.name == "nt":
            input("\nНатисни Enter...")
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    success = 0
    skipped = 0
    failed = 0

    print(f"Знайдено HTML: {len(files)}")
    print("-" * 76)

    for i, source in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {source.name}")

        try:
            answer = extract_model_answer(source)

            print(f"    Витягнуто відповідь: {len(answer):,} символів".replace(",", " "))

            clean_html = build_clean_html(answer, source.stem)
            output_pdf = OUTPUT_DIR / f"{source.stem}.pdf"

            print_html_to_pdf(browser, clean_html, output_pdf)

            size_kb = output_pdf.stat().st_size / 1024
            print(f"    [OK] {output_pdf.name} ({size_kb:.0f} KB)")
            success += 1

        except RuntimeError as exc:
            message = str(exc)

            if "Не знайдено блок" in message:
                print(f"    [SKIP] {message}")
                skipped += 1
            else:
                print(f"    [ERROR] {message}")
                failed += 1

        except subprocess.TimeoutExpired:
            print(f"    [ERROR] Timeout понад {TIMEOUT_SECONDS} секунд")
            failed += 1

        except Exception as exc:
            print(f"    [ERROR] {type(exc).__name__}: {exc}")
            failed += 1

        print()

    print("=" * 76)
    print(f"Успішно : {success}")
    print(f"Пропущено: {skipped}")
    print(f"Помилок : {failed}")
    print(f"Результат: {OUTPUT_DIR}")
    print("=" * 76)

    if os.name == "nt":
        input("\nНатисни Enter для виходу...")

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
