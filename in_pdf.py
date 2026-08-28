import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
)


# ============================================================
# НАЛАШТУВАННЯ
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "PDF_RESULTS"

OUTPUT_DIR.mkdir(exist_ok=True)


# ============================================================
# ШРИФТИ ДЛЯ УКРАЇНСЬКОЇ
# ============================================================

def register_fonts():
    """
    Шукаємо шрифти Windows.
    Arial нормально підтримує українську.
    """

    regular_candidates = [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\calibri.ttf"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
    ]

    bold_candidates = [
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\calibrib.ttf"),
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
    ]

    regular = next(
        (p for p in regular_candidates if p.exists()),
        None
    )

    bold = next(
        (p for p in bold_candidates if p.exists()),
        None
    )

    if regular is None:
        raise FileNotFoundError(
            "Не знайдено шрифт Arial/Calibri/Segoe UI "
            "у C:\\Windows\\Fonts"
        )

    if bold is None:
        bold = regular

    pdfmetrics.registerFont(
        TTFont("MainFont", str(regular))
    )

    pdfmetrics.registerFont(
        TTFont("MainFontBold", str(bold))
    )


register_fonts()


# ============================================================
# ПЕРЕКЛАД ПОЛІВ
# ============================================================

STATUS_NAMES = {
    "complete": "Повний",
    "limited": "Обмежений",
    "insufficient": "Недостатньо даних",
}


SEVERITY_NAMES = {
    "critical": "Критичний",
    "high": "Високий",
    "medium": "Середній",
    "low": "Низький",
}


# ============================================================
# СТИЛІ PDF
# ============================================================

styles = getSampleStyleSheet()


TITLE_STYLE = ParagraphStyle(
    "TitleUA",
    fontName="MainFontBold",
    fontSize=21,
    leading=25,
    textColor=colors.HexColor("#1F4E79"),
    alignment=TA_CENTER,
    spaceAfter=12,
)


SECTION_STYLE = ParagraphStyle(
    "SectionUA",
    fontName="MainFontBold",
    fontSize=14,
    leading=17,
    textColor=colors.HexColor("#1F4E79"),
    spaceBefore=10,
    spaceAfter=6,
)


SUBSECTION_STYLE = ParagraphStyle(
    "SubsectionUA",
    fontName="MainFontBold",
    fontSize=11.5,
    leading=14,
    textColor=colors.HexColor("#2E75B6"),
    spaceBefore=7,
    spaceAfter=4,
)


BODY_STYLE = ParagraphStyle(
    "BodyUA",
    fontName="MainFont",
    fontSize=10,
    leading=14,
    textColor=colors.HexColor("#222222"),
    spaceAfter=5,
)


BODY_BOLD_STYLE = ParagraphStyle(
    "BodyBoldUA",
    fontName="MainFontBold",
    fontSize=10,
    leading=14,
    textColor=colors.HexColor("#222222"),
    spaceAfter=4,
)


BULLET_STYLE = ParagraphStyle(
    "BulletUA",
    fontName="MainFont",
    fontSize=10,
    leading=14,
    leftIndent=13,
    firstLineIndent=-7,
    bulletIndent=5,
    spaceAfter=3,
)


SMALL_STYLE = ParagraphStyle(
    "SmallUA",
    fontName="MainFont",
    fontSize=8.5,
    leading=11,
    textColor=colors.HexColor("#666666"),
)


CALLOUT_STYLE = ParagraphStyle(
    "CalloutUA",
    fontName="MainFontBold",
    fontSize=11,
    leading=15,
    textColor=colors.HexColor("#1F4E79"),
)


# ============================================================
# HTML -> JSON
# ============================================================

def clean_json_text(text):
    text = text.strip()

    text = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


def parse_json_text(text):
    text = clean_json_text(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Якщо модель додала якийсь текст навколо JSON
    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        candidate = text[start:end + 1]

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    return None


def extract_json_from_html(html_path):
    """
    Беремо ТІЛЬКИ результат AI.

    Пріоритет:
    1. Parsed JSON
    2. Raw model output
    """

    html_text = html_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    soup = BeautifulSoup(html_text, "html.parser")

    # --------------------------------------------------------
    # 1. Parsed JSON
    # --------------------------------------------------------

    for details in soup.find_all("details"):
        summary = details.find("summary")

        if not summary:
            continue

        title = summary.get_text(
            " ",
            strip=True
        ).lower()

        if "parsed json" in title:
            pre = details.find("pre")

            if pre:
                data = parse_json_text(
                    pre.get_text("\n", strip=False)
                )

                if isinstance(data, dict):
                    return data, "Parsed JSON"

    # --------------------------------------------------------
    # 2. Raw model output
    # --------------------------------------------------------

    for details in soup.find_all("details"):
        summary = details.find("summary")

        if not summary:
            continue

        title = summary.get_text(
            " ",
            strip=True
        ).lower()

        if "raw model output" in title:
            pre = details.find("pre")

            if pre:
                data = parse_json_text(
                    pre.get_text("\n", strip=False)
                )

                if isinstance(data, dict):
                    return data, "Raw model output"

    return None, None


# ============================================================
# БЕЗПЕЧНИЙ ТЕКСТ ДЛЯ REPORTLAB
# ============================================================

def escape_text(value):
    if value is None:
        return ""

    value = str(value)

    return (
        value
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def p(text, style=BODY_STYLE):
    return Paragraph(
        escape_text(text),
        style
    )


def bold_label(label, value):
    return Paragraph(
        f"<b>{escape_text(label)}:</b> "
        f"{escape_text(value)}",
        BODY_STYLE,
    )


# ============================================================
# СПИСКИ
# ============================================================

def add_bullets(story, values):
    if not values:
        return

    for value in values:
        story.append(
            Paragraph(
                f"- {escape_text(value)}",
                BULLET_STYLE,
            )
        )


# ============================================================
# SCORE BLOCK
# ============================================================

def add_score_block(story, data):
    score = data.get("score")
    confidence = data.get("confidence")
    status = data.get("analysis_status")

    row = []

    if score is not None:
        row.append(
            Paragraph(
                f"<b>Оцінка</b><br/>{score}/100",
                BODY_STYLE,
            )
        )

    if confidence is not None:
        row.append(
            Paragraph(
                f"<b>Впевненість</b><br/>{confidence}%",
                BODY_STYLE,
            )
        )

    if status:
        status_text = STATUS_NAMES.get(
            str(status).lower(),
            status
        )

        row.append(
            Paragraph(
                f"<b>Статус</b><br/>"
                f"{escape_text(status_text)}",
                BODY_STYLE,
            )
        )

    if not row:
        return

    table = Table(
        [row],
        colWidths=[
            170 * mm / len(row)
        ] * len(row),
    )

    table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, -1),
                    colors.HexColor("#EAF2F8"),
                ),
                (
                    "BOX",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    colors.HexColor("#9EBCD3"),
                ),
                (
                    "INNERGRID",
                    (0, 0),
                    (-1, -1),
                    0.3,
                    colors.HexColor("#C7D9E7"),
                ),
                (
                    "ALIGN",
                    (0, 0),
                    (-1, -1),
                    "CENTER",
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE",
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
            ]
        )
    )

    story.append(table)
    story.append(Spacer(1, 8))


# ============================================================
# HEADLINE
# ============================================================

def add_headline(story, data):
    headline = data.get("headline")

    if not headline:
        return

    table = Table(
        [[
            Paragraph(
                escape_text(headline),
                CALLOUT_STYLE,
            )
        ]],
        colWidths=[170 * mm],
    )

    table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, -1),
                    colors.HexColor("#D9EAF7"),
                ),
                (
                    "BOX",
                    (0, 0),
                    (-1, -1),
                    0.7,
                    colors.HexColor("#9EBCD3"),
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    12,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    12,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    10,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    10,
                ),
            ]
        )
    )

    story.append(table)
    story.append(Spacer(1, 8))


# ============================================================
# DIMENSION SCORES
# ============================================================

def add_dimensions(story, dimensions):
    if not dimensions:
        return

    story.append(
        Paragraph(
            "Оцінка за напрямками",
            SECTION_STYLE,
        )
    )

    for item in dimensions:
        if not isinstance(item, dict):
            continue

        dimension = item.get(
            "dimension",
            "Напрямок"
        )

        score = item.get("score")

        if score is not None:
            title = f"{dimension} - {score}/100"
        else:
            title = dimension

        elements = [
            Paragraph(
                escape_text(title),
                SUBSECTION_STYLE,
            )
        ]

        evidence = item.get("evidence", [])

        if evidence:
            elements.append(
                Paragraph(
                    "<b>Підтвердження:</b>",
                    BODY_STYLE,
                )
            )

            for value in evidence:
                elements.append(
                    Paragraph(
                        f"- {escape_text(value)}",
                        BULLET_STYLE,
                    )
                )

        gaps = item.get("gaps", [])

        if gaps:
            elements.append(
                Paragraph(
                    "<b>Прогалини:</b>",
                    BODY_STYLE,
                )
            )

            for value in gaps:
                elements.append(
                    Paragraph(
                        f"- {escape_text(value)}",
                        BULLET_STYLE,
                    )
                )

        story.append(KeepTogether(elements))
        story.append(Spacer(1, 3))


# ============================================================
# RED FLAGS
# ============================================================

def add_red_flags(story, items):
    if not items:
        return

    story.append(
        Paragraph(
            "Критичні сигнали та ризики",
            SECTION_STYLE,
        )
    )

    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue

        severity_raw = str(
            item.get("severity", "")
        ).lower()

        severity = SEVERITY_NAMES.get(
            severity_raw,
            item.get("severity", "")
        )

        signal = item.get("signal", "")
        why = item.get("why_it_matters", "")

        if severity_raw == "critical":
            bg = colors.HexColor("#FCE4D6")
        elif severity_raw == "high":
            bg = colors.HexColor("#FFF2CC")
        elif severity_raw == "medium":
            bg = colors.HexColor("#FFF9E6")
        else:
            bg = colors.HexColor("#E2F0D9")

        content = [
            Paragraph(
                f"<b>{i}. {escape_text(signal)}</b>",
                BODY_STYLE,
            )
        ]

        if severity:
            content.append(
                Paragraph(
                    f"<b>Рівень ризику:</b> "
                    f"{escape_text(severity)}",
                    BODY_STYLE,
                )
            )

        if why:
            content.append(
                Paragraph(
                    f"<b>Чому це важливо:</b> "
                    f"{escape_text(why)}",
                    BODY_STYLE,
                )
            )

        table = Table(
            [[content]],
            colWidths=[170 * mm],
        )

        table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, -1),
                        bg,
                    ),
                    (
                        "BOX",
                        (0, 0),
                        (-1, -1),
                        0.5,
                        colors.HexColor("#C0C0C0"),
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        10,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        10,
                    ),
                    (
                        "TOPPADDING",
                        (0, 0),
                        (-1, -1),
                        8,
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        8,
                    ),
                ]
            )
        )

        story.append(table)
        story.append(Spacer(1, 6))


# ============================================================
# RECOMMENDATIONS
# ============================================================

def add_recommendations(story, items):
    if not items:
        return

    story.append(
        Paragraph(
            "Рекомендації",
            SECTION_STYLE,
        )
    )

    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue

        action = item.get(
            "action",
            f"Рекомендація {i}"
        )

        parts = [
            Paragraph(
                f"<b>{i}. {escape_text(action)}</b>",
                SUBSECTION_STYLE,
            )
        ]

        fields = [
            ("Навіщо", item.get("why")),
            (
                "Очікуваний результат",
                item.get("expected_result")
            ),
            (
                "Відповідальний",
                item.get("owner_role")
            ),
            (
                "Строк",
                item.get("deadline")
            ),
        ]

        for label, value in fields:
            if value:
                parts.append(
                    Paragraph(
                        f"<b>{escape_text(label)}:</b> "
                        f"{escape_text(value)}",
                        BODY_STYLE,
                    )
                )

        table = Table(
            [[parts]],
            colWidths=[170 * mm],
        )

        table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, -1),
                        colors.HexColor("#EAF2F8"),
                    ),
                    (
                        "BOX",
                        (0, 0),
                        (-1, -1),
                        0.5,
                        colors.HexColor("#A9C6DB"),
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        10,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        10,
                    ),
                    (
                        "TOPPADDING",
                        (0, 0),
                        (-1, -1),
                        8,
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        8,
                    ),
                ]
            )
        )

        story.append(table)
        story.append(Spacer(1, 7))


# ============================================================
# FOOTER / НОМЕР СТОРІНКИ
# ============================================================

def add_page_number(canvas, doc):
    canvas.saveState()

    canvas.setFont(
        "MainFont",
        8
    )

    canvas.setFillColor(
        colors.HexColor("#777777")
    )

    page_num = canvas.getPageNumber()

    canvas.drawCentredString(
        A4[0] / 2,
        9 * mm,
        f"Сторінка {page_num}",
    )

    canvas.restoreState()


# ============================================================
# СТВОРЕННЯ PDF
# ============================================================

def create_pdf(data, output_path):
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=str(
            data.get(
                "module_name",
                "AI Analysis"
            )
        ),
    )

    story = []

    # --------------------------------------------------------
    # НАЗВА
    # --------------------------------------------------------

    module_name = data.get(
        "module_name",
        "Результат аналізу"
    )

    story.append(
        Paragraph(
            escape_text(module_name),
            TITLE_STYLE,
        )
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    add_score_block(
        story,
        data
    )

    # --------------------------------------------------------
    # HEADLINE
    # --------------------------------------------------------

    add_headline(
        story,
        data
    )

    # --------------------------------------------------------
    # ДІАГНОСТИКА
    # --------------------------------------------------------

    diagnosis = data.get("diagnosis")

    if diagnosis:
        story.append(
            Paragraph(
                "Діагностика",
                SECTION_STYLE,
            )
        )

        story.append(
            p(diagnosis)
        )

    # --------------------------------------------------------
    # НАПРЯМКИ
    # --------------------------------------------------------

    add_dimensions(
        story,
        data.get(
            "dimension_scores",
            []
        )
    )

    # --------------------------------------------------------
    # СИЛЬНІ СТОРОНИ
    # --------------------------------------------------------

    strengths = data.get("strengths", [])

    if strengths:
        story.append(
            Paragraph(
                "Сильні сторони",
                SECTION_STYLE,
            )
        )

        add_bullets(
            story,
            strengths
        )

    # --------------------------------------------------------
    # СЛАБКІ СТОРОНИ
    # --------------------------------------------------------

    weaknesses = data.get("weaknesses", [])

    if weaknesses:
        story.append(
            Paragraph(
                "Слабкі сторони",
                SECTION_STYLE,
            )
        )

        add_bullets(
            story,
            weaknesses
        )

    # --------------------------------------------------------
    # RED FLAGS
    # --------------------------------------------------------

    add_red_flags(
        story,
        data.get(
            "red_flags",
            []
        )
    )

    # --------------------------------------------------------
    # СУПЕРЕЧНОСТІ
    # --------------------------------------------------------

    contradictions = data.get(
        "contradictions",
        []
    )

    if contradictions:
        story.append(
            Paragraph(
                "Суперечності",
                SECTION_STYLE,
            )
        )

        add_bullets(
            story,
            contradictions
        )

    # --------------------------------------------------------
    # DATA GAPS
    # --------------------------------------------------------

    gaps = data.get(
        "data_gaps",
        []
    )

    if gaps:
        story.append(
            Paragraph(
                "Прогалини в даних",
                SECTION_STYLE,
            )
        )

        add_bullets(
            story,
            gaps
        )

    # --------------------------------------------------------
    # РЕКОМЕНДАЦІЇ
    # --------------------------------------------------------

    add_recommendations(
        story,
        data.get(
            "recommendations",
            []
        )
    )

    # --------------------------------------------------------
    # ПІДСУМОК ДЛЯ КЕРІВНИКА
    # --------------------------------------------------------

    manager_summary = data.get(
        "manager_summary"
    )

    if manager_summary:
        story.append(
            Paragraph(
                "Підсумок для керівника",
                SECTION_STYLE,
            )
        )

        table = Table(
            [[
                Paragraph(
                    escape_text(manager_summary),
                    BODY_BOLD_STYLE,
                )
            ]],
            colWidths=[170 * mm],
        )

        table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, -1),
                        colors.HexColor("#E2F0D9"),
                    ),
                    (
                        "BOX",
                        (0, 0),
                        (-1, -1),
                        0.6,
                        colors.HexColor("#A9D08E"),
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        12,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        12,
                    ),
                    (
                        "TOPPADDING",
                        (0, 0),
                        (-1, -1),
                        10,
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        10,
                    ),
                ]
            )
        )

        story.append(table)

    doc.build(
        story,
        onFirstPage=add_page_number,
        onLaterPages=add_page_number,
    )


# ============================================================
# ОБРОБКА ВСІХ HTML
# ============================================================

def main():
    html_files = sorted(
        BASE_DIR.glob("*.html")
    )

    if not html_files:
        print(
            "У директорії немає HTML-файлів."
        )
        return

    print("=" * 75)
    print("HTML AI RESULTS -> CLEAN PDF")
    print("=" * 75)

    success = 0
    skipped = 0
    errors = 0

    for html_path in html_files:
        print()
        print(
            f"Читаю: {html_path.name}"
        )

        try:
            data, source = extract_json_from_html(
                html_path
            )

            if data is None:
                print(
                    "  SKIP: відповіді AI не знайдено."
                )

                skipped += 1
                continue

            output_path = (
                OUTPUT_DIR /
                f"{html_path.stem}.pdf"
            )

            create_pdf(
                data,
                output_path
            )

            print(
                f"  OK: {source}"
            )

            print(
                f"  -> {output_path.name}"
            )

            success += 1

        except Exception as e:
            print(
                f"  ERROR: {e}"
            )

            errors += 1

    print()
    print("=" * 75)
    print("ГОТОВО")
    print("=" * 75)
    print(
        f"PDF створено: {success}"
    )
    print(
        f"Пропущено:    {skipped}"
    )
    print(
        f"Помилок:      {errors}"
    )
    print(
        f"Папка:        {OUTPUT_DIR}"
    )


if __name__ == "__main__":
    main()