import json
import re
from pathlib import Path
from html import escape as html_escape

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
    HRFlowable,
)


# ============================================================
# НАЛАШТУВАННЯ
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "PDF_RESULTS"
OUTPUT_DIR.mkdir(exist_ok=True)

# FULL часто містить і готовий красивий final_text_for_user, і структуровані
# поля. Щоб нічого не втрачати, структуровані поля додаються як додаток.
INCLUDE_STRUCTURED_APPENDIX = True

# Найнадійніше джерело відповіді: текст кандидата у Full API response.
# Якщо його немає — Raw model output, потім Parsed JSON.
PREFER_FULL_API_RESPONSE = True


# ============================================================
# ШРИФТИ
# ============================================================

def register_fonts():
    regular_candidates = [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\calibri.ttf"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        # Linux fallback — потрібен лише для переносимості/тестів.
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]

    bold_candidates = [
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\calibrib.ttf"),
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    ]

    regular = next((p for p in regular_candidates if p.exists()), None)
    bold = next((p for p in bold_candidates if p.exists()), None)

    if regular is None:
        raise FileNotFoundError(
            "Не знайдено шрифт із підтримкою кирилиці. "
            "На Windows очікується Arial/Calibri/Segoe UI."
        )

    if bold is None:
        bold = regular

    pdfmetrics.registerFont(TTFont("MainFont", str(regular)))
    pdfmetrics.registerFont(TTFont("MainFontBold", str(bold)))


register_fonts()


# ============================================================
# СТИЛІ
# ============================================================

styles = getSampleStyleSheet()

TITLE_STYLE = ParagraphStyle(
    "TitleUA",
    fontName="MainFontBold",
    fontSize=20,
    leading=24,
    textColor=colors.HexColor("#1F4E79"),
    alignment=TA_CENTER,
    spaceAfter=12,
)

H2_STYLE = ParagraphStyle(
    "H2UA",
    fontName="MainFontBold",
    fontSize=14,
    leading=18,
    textColor=colors.HexColor("#1F4E79"),
    spaceBefore=10,
    spaceAfter=6,
)

H3_STYLE = ParagraphStyle(
    "H3UA",
    fontName="MainFontBold",
    fontSize=11.5,
    leading=15,
    textColor=colors.HexColor("#2E75B6"),
    spaceBefore=7,
    spaceAfter=4,
)

H4_STYLE = ParagraphStyle(
    "H4UA",
    fontName="MainFontBold",
    fontSize=10.5,
    leading=14,
    textColor=colors.HexColor("#385D8A"),
    spaceBefore=6,
    spaceAfter=3,
)

BODY_STYLE = ParagraphStyle(
    "BodyUA",
    fontName="MainFont",
    fontSize=9.8,
    leading=14,
    textColor=colors.HexColor("#222222"),
    spaceAfter=5,
)

BODY_BOLD_STYLE = ParagraphStyle(
    "BodyBoldUA",
    fontName="MainFontBold",
    fontSize=9.8,
    leading=14,
    textColor=colors.HexColor("#222222"),
    spaceAfter=5,
)

BULLET_STYLE = ParagraphStyle(
    "BulletUA",
    fontName="MainFont",
    fontSize=9.7,
    leading=13.5,
    leftIndent=13,
    firstLineIndent=-7,
    bulletIndent=5,
    spaceAfter=3,
)

NUMBER_STYLE = ParagraphStyle(
    "NumberUA",
    fontName="MainFont",
    fontSize=9.7,
    leading=13.5,
    leftIndent=16,
    firstLineIndent=-10,
    spaceAfter=4,
)

SMALL_STYLE = ParagraphStyle(
    "SmallUA",
    fontName="MainFont",
    fontSize=8.2,
    leading=11,
    textColor=colors.HexColor("#666666"),
    spaceAfter=3,
)

CODE_STYLE = ParagraphStyle(
    "CodeUA",
    fontName="MainFont",
    fontSize=8.7,
    leading=11.5,
    textColor=colors.HexColor("#222222"),
    leftIndent=2,
    rightIndent=2,
)

CALLOUT_STYLE = ParagraphStyle(
    "CalloutUA",
    fontName="MainFontBold",
    fontSize=10.5,
    leading=15,
    textColor=colors.HexColor("#1F4E79"),
)


# ============================================================
# НАЗВИ JSON-ПОЛІВ
# ============================================================

FIELD_LABELS = {
    "analysis_status": "Статус аналізу",
    "overall_score": "Загальна оцінка",
    "score": "Оцінка",
    "confidence": "Впевненість",
    "headline": "Головний висновок",
    "executive_summary": "Резюме",
    "direction_scores": "Оцінка напрямків",
    "dimension_scores": "Оцінка напрямків",
    "main_strengths": "Сильні сторони",
    "strengths": "Сильні сторони",
    "main_weaknesses": "Слабкі сторони",
    "weaknesses": "Слабкі сторони",
    "systemic_problems": "Системні проблеми",
    "critical_red_flags": "Критичні сигнали та ризики",
    "red_flags": "Критичні сигнали та ризики",
    "cross_direction_conflicts": "Міжнапрямкові конфлікти",
    "contradictions": "Суперечності",
    "behavioral_profile": "Поведінковий профіль",
    "top_3_recommendations": "ТОП-рекомендації",
    "recommendations": "Рекомендації",
    "action_plan_7_30_90": "План дій 7 / 30 / 90 днів",
    "data_gaps": "Прогалини в даних",
    "final_text_for_user": "Фінальний текст для користувача",
    "module_name": "Назва модуля",
    "diagnosis": "Діагностика",
    "manager_summary": "Підсумок для керівника",
    "direction": "Напрямок",
    "dimension": "Напрямок",
    "status": "Статус",
    "problem": "Проблема",
    "root_cause": "Першопричина",
    "affected_directions": "Зачеплені напрямки",
    "business_impact": "Вплив на бізнес",
    "signal": "Сигнал",
    "severity": "Рівень ризику",
    "why_it_matters": "Чому це важливо",
    "conflict": "Конфлікт",
    "natural_strength": "Природна сила",
    "shadow_risk": "Тіньовий ризик",
    "leadership_implication": "Управлінський висновок",
    "action": "Дія",
    "why": "Навіщо",
    "expected_result": "Очікуваний результат",
    "owner_role": "Відповідальний",
    "deadline": "Строк",
    "kpi": "KPI",
    "days_7": "Перші 7 днів",
    "days_30": "Протягом 30 днів",
    "days_90": "Протягом 90 днів",
    "evidence": "Підтвердження",
    "gaps": "Прогалини",
    "обов’язковість_action": "Обов'язкова дія",
    "mandatory_action": "Обов'язкова дія",
}

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


def humanize_key(key):
    key = str(key)
    if key in FIELD_LABELS:
        return FIELD_LABELS[key]
    return key.replace("_", " ").strip().capitalize()


# ============================================================
# HTML -> ВІДПОВІДЬ МОДЕЛІ
# ============================================================

def clean_json_text(text):
    text = (text or "").strip().lstrip("\ufeff")

    text = re.sub(
        r"^\s*```(?:json|javascript|js)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*```\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _json_loads_tolerant(text):
    """JSON-парсер, який приймає literal newlines усередині string values.

    Саме це трапляється у Gemini FULL у final_text_for_user.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass

    # Ще один типовий дефект LLM — trailing comma.
    repaired = re.sub(r",\s*([}\]])", r"\1", text)
    if repaired != text:
        try:
            return json.loads(repaired, strict=False)
        except json.JSONDecodeError:
            pass

    return None


def parse_json_text(text):
    text = clean_json_text(text)
    if not text:
        return None

    data = _json_loads_tolerant(text)
    if data is not None:
        return data

    # Якщо модель додала текст до/після JSON — пробуємо вирізати об'єкт/масив.
    candidates = []

    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start >= 0 and obj_end > obj_start:
        candidates.append(text[obj_start:obj_end + 1])

    arr_start = text.find("[")
    arr_end = text.rfind("]")
    if arr_start >= 0 and arr_end > arr_start:
        candidates.append(text[arr_start:arr_end + 1])

    for candidate in candidates:
        data = _json_loads_tolerant(candidate)
        if data is not None:
            return data

    return None


def _details_map(soup):
    result = {}
    for details in soup.find_all("details"):
        summary = details.find("summary")
        if not summary:
            continue
        title = summary.get_text(" ", strip=True).strip().lower()
        result[title] = details
    return result


def _pre_text(details):
    if not details:
        return ""
    pre = details.find("pre")
    if not pre:
        return ""
    return pre.get_text("\n", strip=False)


def _collect_text_from_full_api(data):
    """Дістає саме відповідь асистента з Full API response.

    Підтримує Gemini, OpenAI Responses/Chat Completions і Anthropic-подібний
    формат. Thinking/reasoning parts навмисно не додаються до PDF.
    """
    texts = []

    if not isinstance(data, dict):
        return ""

    # Gemini: candidates[].content.parts[].text
    for candidate in data.get("candidates", []) or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        for part in content.get("parts", []) or []:
            if not isinstance(part, dict):
                continue
            if part.get("thought") is True:
                continue
            value = part.get("text")
            if isinstance(value, str) and value:
                texts.append(value)

    if texts:
        return "\n".join(texts).strip()

    # OpenAI Responses API: output[].content[].text
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("role") not in (None, "assistant"):
            continue
        for part in item.get("content", []) or []:
            if not isinstance(part, dict):
                continue
            value = part.get("text")
            if isinstance(value, str) and value:
                texts.append(value)

    if texts:
        return "\n".join(texts).strip()

    # OpenAI Chat Completions: choices[].message.content
    for choice in data.get("choices", []) or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content:
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                value = part.get("text")
                if isinstance(value, str) and value:
                    texts.append(value)

    if texts:
        return "\n".join(texts).strip()

    # Anthropic: content[].text
    content = data.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if str(part.get("type", "")).lower() in {"thinking", "reasoning"}:
                continue
            value = part.get("text")
            if isinstance(value, str) and value:
                texts.append(value)

    if texts:
        return "\n".join(texts).strip()

    # Іноді SDK вже дає агрегований output_text.
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text.strip()

    return ""


def extract_result_from_html(html_path):
    html_text = html_path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")
    details = _details_map(soup)

    # 1) Full API response — джерело істини, коли воно є.
    if PREFER_FULL_API_RESPONSE:
        full_text = _pre_text(details.get("full api response"))
        if full_text:
            full_data = _json_loads_tolerant(full_text.strip())
            model_text = _collect_text_from_full_api(full_data)
            if model_text:
                parsed = parse_json_text(model_text)
                return {
                    "kind": "json" if parsed is not None else "text",
                    "data": parsed if parsed is not None else model_text,
                    "raw_text": model_text,
                    "source": "Full API response -> model text",
                }

    # 2) Raw model output — зазвичай це той самий текст, але простіший шлях.
    raw_text = _pre_text(details.get("raw model output"))
    if raw_text.strip():
        parsed = parse_json_text(raw_text)
        return {
            "kind": "json" if parsed is not None else "text",
            "data": parsed if parsed is not None else raw_text.strip(),
            "raw_text": raw_text.strip(),
            "source": "Raw model output",
        }

    # 3) Parsed JSON — лише fallback. Воно може бути НЕПОВНИМ.
    parsed_text = _pre_text(details.get("parsed json"))
    if parsed_text.strip():
        parsed = parse_json_text(parsed_text)
        if parsed is not None:
            return {
                "kind": "json",
                "data": parsed,
                "raw_text": parsed_text.strip(),
                "source": "Parsed JSON (fallback)",
            }

    # 4) Старі HTML: блок "ОБРОБЛЕНИЙ РЕЗУЛЬТАТ".
    for section in soup.find_all("section"):
        h2 = section.find("h2")
        if not h2:
            continue
        if "оброблений результат" not in h2.get_text(" ", strip=True).lower():
            continue
        pre = section.find("pre")
        if pre:
            text = pre.get_text("\n", strip=False).strip()
            if text:
                parsed = parse_json_text(text)
                return {
                    "kind": "json" if parsed is not None else "text",
                    "data": parsed if parsed is not None else text,
                    "raw_text": text,
                    "source": "Processed result",
                }

    return None


# ============================================================
# MARKDOWN -> REPORTLAB
# ============================================================

def escape_text(value):
    if value is None:
        return ""
    return html_escape(str(value), quote=False)


def markdown_inline(text):
    """Мінімальний inline Markdown для ReportLab Paragraph."""
    s = escape_text(text)

    # Markdown links: [текст](url)
    s = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<link href="\2" color="#2E75B6">\1</link>',
        s,
    )

    # Bold, then italic. Працює для типового LLM Markdown.
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__(.+?)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"(?<!_)_([^_\n]+?)_(?!_)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`\n]+)`", r"<b>\1</b>", s)

    return s


def _is_table_separator(line):
    line = line.strip().strip("|")
    if not line:
        return False
    cells = [c.strip() for c in line.split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c) for c in cells)


def _split_md_table_row(line):
    line = line.strip().strip("|")
    return [c.strip() for c in line.split("|")]


def _add_markdown_table(story, rows):
    if not rows:
        return

    max_cols = max(len(r) for r in rows)
    normalized = [r + [""] * (max_cols - len(r)) for r in rows]
    usable_width = 170 * mm
    col_widths = [usable_width / max_cols] * max_cols

    table_data = []
    for r_idx, row in enumerate(normalized):
        style = BODY_BOLD_STYLE if r_idx == 0 else SMALL_STYLE
        table_data.append([Paragraph(markdown_inline(cell), style) for cell in row])

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9EAF7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C7D9E7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 6))


def _add_code_block(story, lines):
    if not lines:
        return
    content = "<br/>".join(escape_text(line).replace(" ", "&nbsp;") for line in lines)
    box = Table([[Paragraph(content, CODE_STYLE)]], colWidths=[170 * mm])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F6F7")),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5D8DC")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(box)
    story.append(Spacer(1, 6))


def add_markdown(story, text, skip_first_h1=False):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    paragraph_lines = []
    first_h1_seen = False
    i = 0

    def flush_paragraph():
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        # Markdown hard-break (два пробіли) -> <br/>.
        joined = " ".join(x.strip() for x in paragraph_lines).strip()
        if joined:
            story.append(Paragraph(markdown_inline(joined), BODY_STYLE))
        paragraph_lines = []

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()

        # fenced code
        if stripped.startswith("```"):
            flush_paragraph()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            _add_code_block(story, code_lines)
            i += 1
            continue

        # Markdown table: header + separator + rows
        if "|" in stripped and i + 1 < len(lines) and _is_table_separator(lines[i + 1]):
            flush_paragraph()
            rows = [_split_md_table_row(stripped)]
            i += 2
            while i < len(lines):
                candidate = lines[i].strip()
                if not candidate or "|" not in candidate:
                    break
                rows.append(_split_md_table_row(candidate))
                i += 1
            _add_markdown_table(story, rows)
            continue

        if not stripped:
            flush_paragraph()
            story.append(Spacer(1, 2))
            i += 1
            continue

        if re.fullmatch(r"-{3,}|_{3,}|\*{3,}", stripped):
            flush_paragraph()
            story.append(Spacer(1, 3))
            story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#C7D9E7")))
            story.append(Spacer(1, 4))
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if m:
            flush_paragraph()
            level = len(m.group(1))
            title = m.group(2).strip()

            if level == 1:
                if skip_first_h1 and not first_h1_seen:
                    first_h1_seen = True
                    i += 1
                    continue
                first_h1_seen = True
                style = TITLE_STYLE
            elif level == 2:
                style = H2_STYLE
            elif level == 3:
                style = H3_STYLE
            else:
                style = H4_STYLE

            story.append(Paragraph(markdown_inline(title), style))
            i += 1
            continue

        # bullet
        m = re.match(r"^\s*[-+*]\s+(.+)$", line)
        if m:
            flush_paragraph()
            story.append(Paragraph("• " + markdown_inline(m.group(1).strip()), BULLET_STYLE))
            i += 1
            continue

        # numbered item
        m = re.match(r"^\s*(\d+)[.)]\s+(.+)$", line)
        if m:
            flush_paragraph()
            story.append(Paragraph(f"{m.group(1)}. " + markdown_inline(m.group(2).strip()), NUMBER_STYLE))
            i += 1
            continue

        # quote/callout
        m = re.match(r"^\s*>\s?(.*)$", line)
        if m:
            flush_paragraph()
            table = Table([[Paragraph(markdown_inline(m.group(1)), CALLOUT_STYLE)]], colWidths=[170 * mm])
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EAF2F8")),
                ("LINEBEFORE", (0, 0), (0, -1), 2, colors.HexColor("#2E75B6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.append(table)
            story.append(Spacer(1, 5))
            i += 1
            continue

        # Інші рядки — звичайний абзац. Рядки з двома пробілами в Markdown
        # тримаємо окремо, щоб не злипались підписи/метадані.
        if raw.endswith("  "):
            paragraph_lines.append(stripped)
            flush_paragraph()
        else:
            paragraph_lines.append(stripped)
        i += 1

    flush_paragraph()


# ============================================================
# УНІВЕРСАЛЬНИЙ JSON -> PDF
# ============================================================

def _scalar_text(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Так" if value else "Ні"
    return str(value)


def _severity_box_color(value):
    value = str(value or "").lower()
    if value == "critical":
        return colors.HexColor("#FCE4D6")
    if value == "high":
        return colors.HexColor("#FFF2CC")
    if value == "medium":
        return colors.HexColor("#FFF9E6")
    return colors.HexColor("#EAF2F8")


def add_object_card(story, obj, index=None):
    parts = []

    # Підбираємо найбільш змістовне поле як заголовок картки.
    title_keys = ["action", "problem", "signal", "conflict", "direction", "dimension", "title", "name"]
    title_key = next((k for k in title_keys if obj.get(k)), None)

    if title_key:
        prefix = f"{index}. " if index is not None else ""
        parts.append(Paragraph(
            f"<b>{prefix}{markdown_inline(_scalar_text(obj[title_key]))}</b>",
            H3_STYLE,
        ))

    for key, value in obj.items():
        if key == title_key:
            continue
        if value in (None, "", [], {}):
            continue

        label = humanize_key(key)

        if isinstance(value, (str, int, float, bool)):
            display = _scalar_text(value)
            if key == "severity":
                display = SEVERITY_NAMES.get(str(value).lower(), display)
            elif key == "analysis_status":
                display = STATUS_NAMES.get(str(value).lower(), display)
            parts.append(Paragraph(
                f"<b>{escape_text(label)}:</b> {markdown_inline(display)}",
                BODY_STYLE,
            ))
        elif isinstance(value, list) and all(not isinstance(x, (dict, list)) for x in value):
            parts.append(Paragraph(f"<b>{escape_text(label)}:</b>", BODY_STYLE))
            for item in value:
                parts.append(Paragraph("• " + markdown_inline(_scalar_text(item)), BULLET_STYLE))
        else:
            # Для глибоко вкладених значень даємо компактний JSON-текст, а не губимо їх.
            nested = json.dumps(value, ensure_ascii=False, indent=2)
            parts.append(Paragraph(f"<b>{escape_text(label)}:</b>", BODY_STYLE))
            parts.append(Paragraph(escape_text(nested).replace("\n", "<br/>"), SMALL_STYLE))

    if not parts:
        return

    bg = _severity_box_color(obj.get("severity")) if "severity" in obj else colors.HexColor("#F8FBFD")
    table = Table([[parts]], colWidths=[170 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#C7D9E7")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    story.append(Spacer(1, 6))


def add_json_value(story, key, value, level=2):
    if value in (None, "", [], {}):
        return

    label = humanize_key(key) if key is not None else None
    heading_style = H2_STYLE if level <= 2 else H3_STYLE if level == 3 else H4_STYLE

    if isinstance(value, dict):
        if label:
            story.append(Paragraph(escape_text(label), heading_style))

        # Якщо це звичайний невеликий об'єкт — одна картка читається краще.
        has_nested = any(isinstance(v, (dict, list)) for v in value.values())
        if not has_nested and len(value) <= 10:
            add_object_card(story, value)
            return

        for child_key, child_value in value.items():
            add_json_value(story, child_key, child_value, level + 1)
        return

    if isinstance(value, list):
        if label:
            story.append(Paragraph(escape_text(label), heading_style))

        if all(not isinstance(x, (dict, list)) for x in value):
            for item in value:
                story.append(Paragraph("• " + markdown_inline(_scalar_text(item)), BULLET_STYLE))
            return

        for idx, item in enumerate(value, start=1):
            if isinstance(item, dict):
                add_object_card(story, item, index=idx)
            elif isinstance(item, list):
                story.append(Paragraph(f"<b>{idx}.</b>", H4_STYLE))
                for sub in item:
                    story.append(Paragraph("• " + markdown_inline(_scalar_text(sub)), BULLET_STYLE))
            else:
                story.append(Paragraph(f"{idx}. " + markdown_inline(_scalar_text(item)), NUMBER_STYLE))
        return

    if isinstance(value, str) and ("\n" in value or value.lstrip().startswith("#")) and len(value) > 250:
        if label:
            story.append(Paragraph(escape_text(label), heading_style))
        add_markdown(story, value)
        return

    if label:
        display = _scalar_text(value)
        if key == "analysis_status":
            display = STATUS_NAMES.get(str(value).lower(), display)
        story.append(Paragraph(
            f"<b>{escape_text(label)}:</b> {markdown_inline(display)}",
            BODY_STYLE,
        ))
    else:
        story.append(Paragraph(markdown_inline(_scalar_text(value)), BODY_STYLE))


def add_score_summary(story, data):
    if not isinstance(data, dict):
        return

    score = data.get("overall_score", data.get("score"))
    confidence = data.get("confidence")
    status = data.get("analysis_status")

    row = []
    if score is not None:
        row.append(Paragraph(f"<b>Оцінка</b><br/>{escape_text(score)}/100", BODY_STYLE))
    if confidence is not None:
        row.append(Paragraph(f"<b>Впевненість</b><br/>{escape_text(confidence)}%", BODY_STYLE))
    if status:
        status_text = STATUS_NAMES.get(str(status).lower(), status)
        row.append(Paragraph(f"<b>Статус</b><br/>{escape_text(status_text)}", BODY_STYLE))

    if not row:
        return

    table = Table([row], colWidths=[170 * mm / len(row)] * len(row))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EAF2F8")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#9EBCD3")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C7D9E7")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(table)
    story.append(Spacer(1, 8))


def add_structured_appendix(story, data):
    if not isinstance(data, dict):
        add_json_value(story, None, data)
        return

    story.append(PageBreak())
    story.append(Paragraph("Структуровані дані аналізу", TITLE_STYLE))
    story.append(Paragraph(
        "Цей додаток містить усі структуровані поля відповіді моделі, щоб жодна частина FULL JSON не була втрачена.",
        SMALL_STYLE,
    ))
    story.append(Spacer(1, 6))

    for key, value in data.items():
        if key == "final_text_for_user":
            continue
        add_json_value(story, key, value, level=2)


# ============================================================
# PDF
# ============================================================

def add_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont("MainFont", 8)
    canvas.setFillColor(colors.HexColor("#777777"))
    page_num = canvas.getPageNumber()
    canvas.drawCentredString(A4[0] / 2, 9 * mm, f"Сторінка {page_num}")
    canvas.restoreState()


def _first_markdown_h1(text):
    for line in (text or "").splitlines():
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            return re.sub(r"[*_`]", "", m.group(1)).strip()
    return None


def create_pdf(result, output_path):
    data = result["data"]
    raw_text = result.get("raw_text", "")

    title = "Результат AI-аналізу"
    if isinstance(data, dict):
        title = str(data.get("module_name") or _first_markdown_h1(data.get("final_text_for_user", "")) or title)
    elif isinstance(data, str):
        title = _first_markdown_h1(data) or title

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="AI result converter",
    )

    story = []

    # FULL JSON із готовим фінальним Markdown — це найкращий основний документ.
    if isinstance(data, dict) and isinstance(data.get("final_text_for_user"), str) and data["final_text_for_user"].strip():
        add_markdown(story, data["final_text_for_user"])

        if INCLUDE_STRUCTURED_APPENDIX:
            add_structured_appendix(story, data)

    # Будь-який інший JSON: рендеримо ВСЮ структуру, а не тільки кілька відомих полів.
    elif isinstance(data, dict):
        module_name = data.get("module_name")
        if module_name:
            story.append(Paragraph(markdown_inline(str(module_name)), TITLE_STYLE))
        else:
            story.append(Paragraph(escape_text(title), TITLE_STYLE))

        add_score_summary(story, data)

        headline = data.get("headline")
        if headline:
            callout = Table([[Paragraph(markdown_inline(headline), CALLOUT_STYLE)]], colWidths=[170 * mm])
            callout.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#D9EAF7")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#9EBCD3")),
                ("LEFTPADDING", (0, 0), (-1, -1), 11),
                ("RIGHTPADDING", (0, 0), (-1, -1), 11),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]))
            story.append(callout)
            story.append(Spacer(1, 7))

        # Не дублюємо те, що вже показане в score/headline/title.
        skipped = {"module_name", "overall_score", "score", "confidence", "analysis_status", "headline"}
        for key, value in data.items():
            if key in skipped:
                continue
            add_json_value(story, key, value, level=2)

    # Parsed JSON іноді є масивом — тепер це теж валідний вхід.
    elif isinstance(data, list):
        story.append(Paragraph(escape_text(title), TITLE_STYLE))
        add_json_value(story, "Результати", data, level=2)

    # ARC та інші відповіді без JSON — рендеримо як Markdown.
    else:
        text = str(data if data is not None else raw_text)
        add_markdown(story, text)

    if not story:
        story.append(Paragraph("Відповідь моделі порожня.", BODY_STYLE))

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)


# ============================================================
# ОБРОБКА ВСІХ HTML
# ============================================================

def main():
    html_files = sorted(BASE_DIR.glob("*.html"))

    if not html_files:
        print("У директорії немає HTML-файлів.")
        return

    print("=" * 75)
    print("HTML AI RESULTS -> CLEAN PDF")
    print("=" * 75)

    success = 0
    skipped = 0
    errors = 0

    for html_path in html_files:
        print(f"\nЧитаю: {html_path.name}")

        try:
            result = extract_result_from_html(html_path)

            if result is None:
                print("  SKIP: відповіді AI не знайдено.")
                skipped += 1
                continue

            output_path = OUTPUT_DIR / f"{html_path.stem}.pdf"
            create_pdf(result, output_path)

            data = result["data"]
            if isinstance(data, dict):
                shape = f"JSON object, fields={len(data)}"
            elif isinstance(data, list):
                shape = f"JSON array, items={len(data)}"
            else:
                shape = f"Markdown/text, chars={len(str(data))}"

            print(f"  OK: {result['source']}")
            print(f"  DATA: {shape}")
            print(f"  -> {output_path.name}")
            success += 1

        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            errors += 1

    print("\n" + "=" * 75)
    print("ГОТОВО")
    print("=" * 75)
    print(f"PDF створено: {success}")
    print(f"Пропущено:    {skipped}")
    print(f"Помилок:      {errors}")
    print(f"Папка:        {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
