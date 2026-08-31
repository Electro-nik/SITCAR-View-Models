"""Build and validate the local SITCAR diagnostics/strategy portal.

Run ``python build_site.py`` to patch report navigation and attachments, rebuild
all four indexes, and validate every published artifact. Use ``--check`` for a
read-only verification pass.
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import re
import sys
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType
from urllib.parse import quote, unquote, urlsplit


sys.dont_write_bytecode = True

SITE_ROOT = Path(__file__).resolve().parent
DIAGNOSTICS_DIR = SITE_ROOT / "diagnostics"
DIAGNOSTICS_BUILDER = DIAGNOSTICS_DIR / "build_sitcar_index_v15.py"
MAIN_INDEX = SITE_ROOT / "index.html"

STRATEGY_FILE_RE = re.compile(
    r"^(?P<model>.+)_STEP_(?P<step>\d+)_"
    r"(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<time>\d{2}-\d{2}-\d{2})"
    r"(?:\((?P<dup>\d+)\))?\."
    r"(?P<ext>html|pdf)$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class Stage:
    number: int
    name: str
    artifact_step: int | None
    source_names: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class StrategyConfig:
    key: str
    directory: Path
    title: str
    eyebrow: str
    description: str
    stages: tuple[Stage, ...]


@dataclass(frozen=True)
class StrategyArtifact:
    path: Path
    model_name: str
    step: int
    ext: str
    run_dt: datetime


@dataclass
class BuildStats:
    title: str
    models: int
    html_files: int
    pdf_files: int
    source_files: int
    complete_models: int
    expected_steps: int
    missing_results: list[str]


STRATEGIES = (
    StrategyConfig(
        key="business",
        directory=SITE_ROOT / "strategy" / "business",
        title="Стратегія для існуючого бізнесу (Соларвест)",
        eyebrow="SITCAR · Існуючий бізнес",
        description=(
            "Анкета, ринок, компанія, профіль СІТКАР, стратегічний вибір, "
            "формалізація, GTM та інтегрований підсумок для Solar West."
        ),
        stages=(
            Stage(
                1,
                "Анкета",
                None,
                ("Паспорт_бізнесу_шаблон (2).xlsx",),
                "Вхідна анкета",
            ),
            Stage(2, "Ринок", 1),
            Stage(3, "Компанія", 2),
            Stage(4, "Профіль СІТКАР", 3),
            Stage(5, "Вибір стратегії", 4),
            Stage(6, "Формалізація", 5),
            Stage(7, "GTM", 6),
            Stage(8, "Інтеграція", 7, note="Об’єднаний результат"),
        ),
    ),
    StrategyConfig(
        key="startup",
        directory=SITE_ROOT / "strategy" / "startup",
        title="Стратегія для стартапу (Geovizor)",
        eyebrow="SITCAR · Стартап",
        description=(
            "Сім послідовних етапів розробки стратегії стартапу GeoVizor — "
            "від дослідження ринку до інтегрованої стратегії."
        ),
        stages=(
            Stage(1, "Дослідження ринку", 1),
            Stage(2, "Можливості стартапу та засновників", 2),
            Stage(3, "Профіль СІТКАР", 3),
            Stage(4, "Вибір стратегії розвитку", 4),
            Stage(5, "Формалізація та фінансова модель", 5),
            Stage(6, "GTM", 6),
            Stage(7, "Інтегрована стратегія", 7, note="Об’єднаний результат"),
        ),
    ),
)


def load_diagnostics_builder() -> ModuleType:
    if not DIAGNOSTICS_BUILDER.is_file():
        raise FileNotFoundError(f"Не знайдено генератор діагностики: {DIAGNOSTICS_BUILDER}")

    spec = importlib.util.spec_from_file_location(
        "sitcar_diagnostics_builder",
        DIAGNOSTICS_BUILDER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Не вдалося завантажити: {DIAGNOSTICS_BUILDER}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_if_changed(path: Path, content: str) -> bool:
    current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
    if current == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def url_for(path: Path, relative_to: Path) -> str:
    rel = path.resolve().relative_to(relative_to.resolve()).as_posix()
    return quote(rel, safe="/")


def normalized_filename(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def strip_markup(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value, flags=re.DOTALL)
    return html.unescape(value).strip()


def build_file_lookups(files_dir: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    exact_candidates: dict[str, list[Path]] = defaultdict(list)
    normalized_candidates: dict[str, list[Path]] = defaultdict(list)

    if not files_dir.is_dir():
        return {}, {}

    for path in sorted(files_dir.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file():
            continue
        exact_candidates[path.name.casefold()].append(path)
        normalized_candidates[normalized_filename(path.name)].append(path)

    exact = {
        key: paths[0]
        for key, paths in exact_candidates.items()
        if len(paths) == 1
    }
    normalized = {
        key: paths[0]
        for key, paths in normalized_candidates.items()
        if len(paths) == 1
    }
    return exact, normalized


def patch_attachment_tables(document: str, section_dir: Path) -> tuple[str, int, list[str]]:
    """Rebuild attachment cells from exact filesystem names.

    Replacing the whole first ``<td><strong>…`` cell also repairs three legacy
    reports that contain nested anchors for similarly named XLSX files.
    """

    exact, normalized = build_file_lookups(section_dir / "Files")
    if not exact:
        return document, 0, []

    section_re = re.compile(
        r"(<section\b[^>]*class=[\"'][^\"']*\battachments-card\b[^\"']*[\"'][^>]*>)"
        r"(.*?)"
        r"(</section>)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    cell_re = re.compile(
        r"<td>\s*<strong>(?P<body>.*?)</strong>\s*</td>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    linked = 0
    unresolved: list[str] = []

    def patch_section(section_match: re.Match[str]) -> str:
        nonlocal linked
        opening, body, closing = section_match.groups()

        def patch_cell(cell_match: re.Match[str]) -> str:
            nonlocal linked
            cell_body = cell_match.group("body")
            visible_name = strip_markup(cell_body)
            target = exact.get(visible_name.casefold())

            if target is None:
                target = normalized.get(normalized_filename(visible_name))

            if target is None:
                href_match = re.search(
                    r"href\s*=\s*[\"'](?P<href>.*?)[\"']",
                    cell_body,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if href_match:
                    href_name = Path(unquote(urlsplit(href_match.group("href")).path)).name
                    target = exact.get(href_name.casefold())

            if target is None:
                if visible_name and visible_name not in unresolved:
                    unresolved.append(visible_name)
                return cell_match.group(0)

            href = url_for(target, section_dir)
            linked += 1
            return (
                "<td><strong>"
                f'<a class="attachment-file-link" href="{html.escape(href)}" '
                f'download title="Завантажити {html.escape(target.name)}">'
                f"{html.escape(target.name)}</a>"
                "</strong></td>"
            )

        return opening + cell_re.sub(patch_cell, body) + closing

    patched = section_re.sub(patch_section, document)

    css_marker = "/* SITCAR_SITE_ATTACHMENT_LINKS */"
    if linked and css_marker not in patched and "</style>" in patched:
        css = """
/* SITCAR_SITE_ATTACHMENT_LINKS */
.attachments-card a.attachment-file-link {
    color:var(--accent);
    font-weight:700;
    text-decoration:none;
    overflow-wrap:anywhere;
}
.attachments-card a.attachment-file-link:hover { text-decoration:underline; }
"""
        patched = patched.replace("</style>", css + "</style>", 1)

    return patched, linked, unresolved


def patch_back_link(document: str, label: str) -> tuple[str, bool]:
    original = document
    anchor_re = re.compile(
        r"(?P<open><a\b[^>]*class=[\"'][^\"']*\bback\b[^\"']*[\"'][^>]*>)"
        r".*?"
        r"(?P<close></a>)",
        flags=re.IGNORECASE | re.DOTALL,
    )

    def replace(match: re.Match[str]) -> str:
        opening = re.sub(
            r"href\s*=\s*[\"'][^\"']*[\"']",
            'href="index.html"',
            match.group("open"),
            count=1,
            flags=re.IGNORECASE,
        )
        if not re.search(r"\bhref\s*=", opening, flags=re.IGNORECASE):
            opening = opening[:-1] + ' href="index.html">'
        return opening + html.escape(label) + match.group("close")

    patched = anchor_re.sub(replace, document, count=1)
    return patched, patched != original


def patch_result_files(
    directory: Path,
    filename_re: re.Pattern[str],
    back_label: str,
    diagnostics_builder: ModuleType,
    add_diagnostics_tax: bool,
) -> dict[str, object]:
    stats: dict[str, object] = {
        "checked": 0,
        "changed": 0,
        "links": 0,
        "tax": 0,
        "back": 0,
        "unresolved": [],
    }

    for path in sorted(directory.glob("*.html"), key=lambda item: item.name.casefold()):
        if not filename_re.match(path.name):
            continue

        stats["checked"] = int(stats["checked"]) + 1
        original = path.read_text(encoding="utf-8", errors="replace")
        document, linked, unresolved = patch_attachment_tables(original, directory)
        stats["links"] = int(stats["links"]) + linked
        cast_unresolved = stats["unresolved"]
        assert isinstance(cast_unresolved, list)
        cast_unresolved.extend(f"{path.name}: {name}" for name in unresolved)

        document, back_changed = patch_back_link(document, back_label)
        stats["back"] = int(stats["back"]) + int(back_changed)

        if add_diagnostics_tax:
            # The legacy helper is safe only for the legacy TOTAL COST layout.
            # Newer Subtotal/Tax/TOTAL WITH TAX reports must remain untouched.
            if re.search(
                r"<span>\s*TOTAL\s+COST\s*</span>",
                document,
                flags=re.IGNORECASE,
            ):
                before_tax = document
                document, _ = diagnostics_builder.patch_total_with_tax(document)
                if document != before_tax:
                    stats["tax"] = int(stats["tax"]) + 1

        if write_if_changed(path, document):
            stats["changed"] = int(stats["changed"]) + 1

    return stats


def discover_strategy_artifacts(
    config: StrategyConfig,
    diagnostics_builder: ModuleType,
) -> tuple[dict[tuple[str, int, str], StrategyArtifact], int, int, int]:
    candidates = list(config.directory.glob("*.html"))
    candidates.extend(config.directory.glob("*.pdf"))
    candidates.extend((config.directory / "pdf_result").glob("*.pdf"))

    newest: dict[tuple[str, int, str], StrategyArtifact] = {}
    discovered = 0
    duplicates = 0

    for path in sorted(candidates, key=lambda item: str(item).casefold()):
        match = STRATEGY_FILE_RE.match(path.name)
        if not match or not path.is_file():
            continue

        ext = match.group("ext").lower()
        if ext == "htm":
            ext = "html"
        model_name = diagnostics_builder.filename_model_to_name(match.group("model"))
        run_dt = datetime.strptime(
            f"{match.group('date')} {match.group('time')}",
            "%Y-%m-%d %H-%M-%S",
        )
        artifact = StrategyArtifact(
            path=path,
            model_name=model_name,
            step=int(match.group("step")),
            ext=ext,
            run_dt=run_dt,
        )
        discovered += 1
        key = (model_name.casefold(), artifact.step, ext)
        previous = newest.get(key)
        if previous is None or (artifact.run_dt, artifact.path.name) > (
            previous.run_dt,
            previous.path.name,
        ):
            if previous is not None:
                duplicates += 1
            newest[key] = artifact
        else:
            duplicates += 1

    html_count = sum(1 for item in newest.values() if item.ext == "html")
    pdf_count = sum(1 for item in newest.values() if item.ext == "pdf")
    return newest, discovered, html_count, pdf_count


def group_strategy_artifacts(
    artifacts: dict[tuple[str, int, str], StrategyArtifact],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}

    for artifact in artifacts.values():
        key = artifact.model_name.casefold()
        item = grouped.setdefault(
            key,
            {"model_name": artifact.model_name, "steps": defaultdict(dict)},
        )
        steps = item["steps"]
        assert isinstance(steps, defaultdict)
        steps[artifact.step][artifact.ext] = artifact

    return list(grouped.values())


def strategy_model_sort_key(item: dict[str, object], diagnostics_builder: ModuleType) -> tuple[int, str]:
    model_name = str(item["model_name"])
    key = diagnostics_builder.normalize_model_name(model_name)
    return diagnostics_builder.MODEL_ORDER_MAP.get(key, 10_000), key


def strategy_run_metrics(path: Path, diagnostics_builder: ModuleType) -> tuple[float | None, float | None]:
    document = path.read_text(encoding="utf-8", errors="replace")
    metrics = diagnostics_builder.extract_metrics(document)
    latency = diagnostics_builder.metric_number(
        metrics,
        "Latency",
        "Duration",
        "Elapsed",
        "Elapsed time",
    )
    total_with_tax = diagnostics_builder.metric_number(
        metrics,
        "TOTAL WITH TAX",
        "TOTAL + 20%",
    )

    if total_with_tax is None:
        subtotal = diagnostics_builder.metric_number(
            metrics,
            "Subtotal",
            "TOTAL COST",
            "Total cost",
        )
        tax = diagnostics_builder.metric_number(metrics, "Tax (20%)", "Tax")
        if subtotal is not None:
            total_with_tax = subtotal + (tax if tax is not None else subtotal * 0.20)

    return latency, total_with_tax


def source_files(directory: Path) -> list[Path]:
    files_dir = directory / "Files"
    if not files_dir.is_dir():
        return []
    return sorted(
        (path for path in files_dir.rglob("*") if path.is_file()),
        key=lambda item: item.name.casefold(),
    )


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def make_source_button(path: Path, directory: Path, compact: bool = False) -> str:
    extension = path.suffix.lstrip(".").upper() or "FILE"
    href = url_for(path, directory)
    label = extension if compact else html.escape(path.name)
    return (
        f'<a class="source-link" href="{html.escape(href)}" download '
        f'title="Завантажити {html.escape(path.name)}">'
        f'<span class="source-ext">{html.escape(extension)}</span>'
        f'<span class="source-name">{label}</span>'
        f'<span class="source-size">{html.escape(format_size(path.stat().st_size))}</span>'
        "</a>"
    )


def make_stage_card(
    stage: Stage,
    step_files: dict[str, StrategyArtifact] | None,
    config: StrategyConfig,
    sources_by_name: dict[str, Path],
) -> str:
    buttons: list[str] = []

    if stage.artifact_step is None:
        for source_name in stage.source_names:
            source = sources_by_name.get(source_name.casefold())
            if source is None:
                continue
            extension = source.suffix.lstrip(".").upper() or "FILE"
            buttons.append(
                f'<a class="result-btn file" href="{html.escape(url_for(source, config.directory))}" '
                f'download title="Завантажити {html.escape(source.name)}">{html.escape(extension)}</a>'
            )
    elif step_files:
        html_artifact = step_files.get("html")
        pdf_artifact = step_files.get("pdf")
        if html_artifact:
            buttons.append(
                f'<a class="result-btn html" href="{html.escape(url_for(html_artifact.path, config.directory))}" '
                f'title="{html.escape(stage.name)} · HTML">HTML</a>'
            )
        if pdf_artifact:
            buttons.append(
                f'<a class="result-btn pdf" href="{html.escape(url_for(pdf_artifact.path, config.directory))}" '
                f'title="{html.escape(stage.name)} · PDF">PDF</a>'
            )

    css_classes = ["stage-card"]
    if not buttons:
        css_classes.append("unavailable")
    if stage.note:
        css_classes.append("integrated")

    note = f'<span class="stage-note">{html.escape(stage.note)}</span>' if stage.note else ""
    actions = (
        '<div class="stage-actions">' + "".join(buttons) + "</div>"
        if buttons
        else '<div class="stage-missing">Немає окремого файла</div>'
    )
    return (
        f'<div class="{" ".join(css_classes)}">'
        '<div class="stage-head">'
        f'<span class="stage-number">{stage.number}</span>'
        f'<span class="stage-name">{html.escape(stage.name)}</span>'
        "</div>"
        f"{note}{actions}</div>"
    )


def make_strategy_model_card(
    item: dict[str, object],
    config: StrategyConfig,
    diagnostics_builder: ModuleType,
    sources_by_name: dict[str, Path],
) -> tuple[str, int, int, list[str]]:
    model_name = str(item["model_name"])
    steps = item["steps"]
    assert isinstance(steps, defaultdict)
    generated_stages = [stage for stage in config.stages if stage.artifact_step is not None]

    available_generated = 0
    latency_values: list[float] = []
    cost_values: list[float] = []
    missing_steps: list[str] = []
    cards: list[str] = []

    for stage in config.stages:
        step_files = steps.get(stage.artifact_step, {}) if stage.artifact_step is not None else None
        if stage.artifact_step is not None:
            has_html = bool(step_files and step_files.get("html"))
            has_pdf = bool(step_files and step_files.get("pdf"))
            if has_html:
                latency, cost = strategy_run_metrics(
                    step_files["html"].path,
                    diagnostics_builder,
                )
                if latency is not None:
                    latency_values.append(latency)
                if cost is not None:
                    cost_values.append(cost)
            if has_html and has_pdf:
                available_generated += 1
            else:
                missing_formats = []
                if not has_html:
                    missing_formats.append("HTML")
                if not has_pdf:
                    missing_formats.append("PDF")
                missing_steps.append(
                    f"{stage.number} ({'+'.join(missing_formats)})"
                )
        cards.append(make_stage_card(stage, step_files, config, sources_by_name))

    questionnaire_available = all(
        name.casefold() in sources_by_name
        for stage in config.stages
        if stage.artifact_step is None
        for name in stage.source_names
    )
    static_stage_count = sum(1 for stage in config.stages if stage.artifact_step is None)
    available_total = available_generated + (static_stage_count if questionnaire_available else 0)
    total_latency = sum(latency_values) if latency_values else None
    total_cost = sum(cost_values) if cost_values else None
    provider_key, provider_label = diagnostics_builder.detect_provider(
        model_name,
        "",
        "",
        model_name,
    )
    del provider_key

    search_text = " ".join(
        [model_name, provider_label, *(stage.name for stage in config.stages)]
    ).casefold()
    coverage_class = "complete" if available_total == len(config.stages) else "partial"
    card = f"""
<article class="model-card" data-search="{html.escape(search_text)}">
    <header class="model-header">
        <div>
            <h2>{html.escape(model_name)}</h2>
            <div class="provider">{html.escape(provider_label)}</div>
        </div>
        <div class="model-metrics">
            <span class="coverage {coverage_class}">{available_total}/{len(config.stages)} етапів</span>
            <span>{html.escape(diagnostics_builder.fmt_duration(total_latency))}</span>
            <strong>{diagnostics_builder.fmt_money(total_cost)}</strong>
            <small>разом із податком</small>
        </div>
    </header>
    <div class="results-grid">{"".join(cards)}</div>
</article>
"""
    return card, available_total, len(generated_stages), missing_steps


STRATEGY_STYLE = r"""
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
a { color:inherit; }
.container { width:min(1900px,calc(100% - 24px)); margin:auto; padding:14px 0 70px; }
.site-nav { display:flex; gap:8px; margin-bottom:9px; }
.home-link {
    display:inline-flex;
    align-items:center;
    gap:6px;
    min-height:36px;
    padding:7px 12px;
    border:1px solid var(--border);
    border-radius:9px;
    background:var(--card);
    color:#79c0ff;
    font-size:12px;
    font-weight:800;
    text-decoration:none;
}
.home-link:hover { border-color:rgba(88,166,255,.55); background:rgba(88,166,255,.09); }
.hero {
    background:radial-gradient(circle at 85% 10%,rgba(88,166,255,.14),transparent 35%),linear-gradient(135deg,#161b22,#111820);
    border:1px solid var(--border);
    border-radius:16px;
    padding:20px;
    margin-bottom:9px;
}
.eyebrow { color:var(--blue); font-size:10px; font-weight:900; letter-spacing:.09em; text-transform:uppercase; }
h1 { margin:3px 0 0; font-size:clamp(26px,3vw,40px); line-height:1.12; }
.hero p { max-width:1050px; margin:8px 0 0; color:var(--muted); font-size:13px; }
.summary-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; margin-bottom:9px; }
.summary-card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:10px 12px; }
.summary-label { color:var(--muted); font-size:9px; font-weight:900; letter-spacing:.06em; text-transform:uppercase; }
.summary-value { margin-top:2px; font-size:19px; font-weight:900; }
.toolbar { display:flex; align-items:center; gap:9px; margin-bottom:9px; padding:8px; border:1px solid var(--border); border-radius:12px; background:var(--card); }
.toolbar input { width:100%; min-width:0; padding:9px 11px; border:1px solid var(--border); border-radius:8px; background:#0d1117; color:var(--text); outline:none; }
.toolbar input:focus { border-color:var(--blue); }
.toolbar span { flex:0 0 auto; color:var(--muted); font-size:10px; }
.stage-legend { display:grid; grid-template-columns:repeat(var(--stage-count),minmax(0,1fr)); gap:5px; margin-bottom:9px; }
.legend-stage { min-width:0; padding:6px 7px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.legend-stage b { display:block; color:#79c0ff; font-size:9px; }
.legend-stage span { display:block; margin-top:2px; color:var(--muted); font-size:9px; line-height:1.2; overflow-wrap:anywhere; }
.models { display:flex; flex-direction:column; gap:9px; }
.model-card { overflow:hidden; border:1px solid var(--border); border-radius:14px; background:var(--card); }
.model-header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:11px 13px; border-bottom:1px solid var(--border); background:linear-gradient(135deg,rgba(88,166,255,.07),rgba(188,140,255,.03)); }
.model-header h2 { margin:0; font-size:17px; }
.provider { margin-top:1px; color:var(--blue); font-size:10px; font-weight:700; }
.model-metrics { display:flex; align-items:center; justify-content:flex-end; gap:8px; color:var(--muted); font-size:10px; text-align:right; }
.model-metrics strong { color:#d2a8ff; font-size:14px; }
.model-metrics small { font-size:8px; }
.coverage { padding:3px 6px; border-radius:999px; border:1px solid rgba(227,179,65,.35); color:#e3b341; background:rgba(227,179,65,.08); font-weight:800; }
.coverage.complete { border-color:rgba(63,185,80,.35); color:#7ee787; background:rgba(63,185,80,.08); }
.results-grid { display:grid; grid-template-columns:repeat(var(--stage-count),minmax(0,1fr)); gap:5px; padding:8px; }
.stage-card { min-width:0; min-height:94px; display:flex; flex-direction:column; padding:7px; border:1px solid rgba(88,166,255,.20); border-radius:9px; background:rgba(255,255,255,.018); }
.stage-card.integrated { border-color:rgba(188,140,255,.34); background:rgba(188,140,255,.045); }
.stage-card.unavailable { border-style:dashed; opacity:.68; }
.stage-head { min-width:0; display:flex; align-items:flex-start; gap:5px; }
.stage-number { flex:0 0 auto; display:grid; place-items:center; width:19px; height:19px; border-radius:5px; background:rgba(88,166,255,.13); color:#79c0ff; font-size:9px; font-weight:900; }
.stage-name { min-width:0; color:#c9d1d9; font-size:clamp(8px,.68vw,10px); font-weight:850; line-height:1.18; overflow-wrap:anywhere; }
.stage-note { margin:4px 0 0 24px; color:#d2a8ff; font-size:8px; line-height:1.1; }
.stage-actions { display:grid; grid-template-columns:repeat(auto-fit,minmax(35px,1fr)); gap:4px; margin-top:auto; padding-top:7px; }
.result-btn { display:grid; place-items:center; min-height:27px; border:1px solid rgba(88,166,255,.38); border-radius:6px; color:#79c0ff; background:rgba(88,166,255,.07); font-size:8px; font-weight:900; text-decoration:none; }
.result-btn:hover { background:rgba(88,166,255,.17); }
.result-btn.pdf { border-color:rgba(248,81,73,.35); color:#ff7b72; background:rgba(248,81,73,.06); }
.result-btn.file { border-color:rgba(63,185,80,.35); color:#7ee787; background:rgba(63,185,80,.06); }
.stage-missing { margin-top:auto; padding-top:7px; color:var(--muted); font-size:8px; line-height:1.15; text-align:center; }
.source-section { margin-top:12px; padding:14px; border:1px solid var(--border); border-radius:14px; background:var(--card); }
.section-heading h2 { margin:0; font-size:18px; }
.section-heading p { margin:4px 0 0; color:var(--muted); font-size:11px; }
.source-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(245px,1fr)); gap:6px; margin-top:10px; }
.source-link { min-width:0; display:grid; grid-template-columns:auto minmax(0,1fr) auto; align-items:center; gap:8px; padding:8px; border:1px solid rgba(63,185,80,.22); border-radius:8px; background:rgba(63,185,80,.035); text-decoration:none; }
.source-link:hover { border-color:rgba(63,185,80,.55); background:rgba(63,185,80,.08); }
.source-ext { padding:3px 5px; border-radius:5px; background:rgba(63,185,80,.12); color:#7ee787; font-size:8px; font-weight:900; }
.source-name { min-width:0; color:#c9d1d9; font-size:10px; overflow-wrap:anywhere; }
.source-size { color:var(--muted); font-size:8px; white-space:nowrap; }
.info-card { margin-top:9px; padding:13px 15px; border:1px solid var(--border); border-radius:12px; background:var(--card); color:var(--muted); font-size:11px; }
.info-card strong { color:var(--text); }
.footer { padding-top:18px; color:var(--muted); font-size:10px; text-align:center; }
@media(max-width:1100px) {
    .stage-legend,.results-grid { grid-template-columns:repeat(4,minmax(0,1fr)); }
}
@media(max-width:720px) {
    .container { width:min(100% - 16px,1900px); padding-top:8px; }
    .summary-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .stage-legend,.results-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .model-header { align-items:flex-start; }
    .model-metrics { flex-direction:column; align-items:flex-end; gap:2px; }
    .toolbar span,.model-metrics small { display:none; }
}
@media(max-width:420px) {
    .stage-legend,.results-grid { grid-template-columns:1fr; }
}
"""


def build_strategy_index(
    config: StrategyConfig,
    diagnostics_builder: ModuleType,
) -> BuildStats:
    artifacts, discovered, html_count, pdf_count = discover_strategy_artifacts(
        config,
        diagnostics_builder,
    )
    del discovered
    models = group_strategy_artifacts(artifacts)
    models.sort(key=lambda item: strategy_model_sort_key(item, diagnostics_builder))
    combined_pdfs = build_combined_strategy_pdfs(config, models)
    published_pdf_count = pdf_count + len(combined_pdfs)

    sources = source_files(config.directory)
    sources_by_name = {path.name.casefold(): path for path in sources}
    model_cards: list[str] = []
    complete_models = 0
    missing_results: list[str] = []

    for item in models:
        card, available, generated_total, missing_steps = make_strategy_model_card(
            item,
            config,
            diagnostics_builder,
            sources_by_name,
            combined_pdfs,
        )
        model_cards.append(card)
        static_total = len(config.stages) - generated_total
        if available == generated_total + static_total:
            complete_models += 1
        if missing_steps:
            missing_results.append(
                f"{item['model_name']}: етапи {', '.join(map(str, missing_steps))}"
            )

    legend = "".join(
        '<div class="legend-stage">'
        f"<b>{stage.number:02d}</b>"
        f"<span>{html.escape(stage.name)}</span>"
        "</div>"
        for stage in config.stages
    )
    source_links = "".join(make_source_button(path, config.directory) for path in sources)
    missing_note = (
        "Не всі моделі мають результат формалізації; відсутні формати позначено без неіснуючих посилань."
        if missing_results
        else (
            "Для кожної моделі наявні HTML- та PDF-результати всіх етапів; "
            "у восьмій колонці бізнес-стратегії доступний один PDF, об’єднаний з етапів 1–7."
            if config.key == "business"
            else "Для кожної моделі наявні HTML- та PDF-результати всіх згенерованих етапів."
        )
    )
    toolbar_note = (
        "HTML відкриває детальний звіт · PDF відкриває його PDF-версію · "
        "восьмий PDF об’єднує етапи 1–7"
        if config.key == "business"
        else "HTML відкриває детальний звіт · PDF відкриває його PDF-версію"
    )
    document = f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(config.title)}</title>
<style>
{STRATEGY_STYLE}
</style>
</head>
<body>
<div class="container">
<nav class="site-nav" aria-label="Головна навігація">
    <a class="home-link" href="../../index.html">← На головну</a>
</nav>
<section class="hero">
    <div class="eyebrow">{html.escape(config.eyebrow)}</div>
    <h1>{html.escape(config.title)}</h1>
    <p>{html.escape(config.description)}</p>
</section>
<section class="summary-grid" aria-label="Підсумок">
    <div class="summary-card"><div class="summary-label">Моделей</div><div class="summary-value">{len(models)}</div></div>
    <div class="summary-card"><div class="summary-label">HTML результатів</div><div class="summary-value">{html_count}</div></div>
    <div class="summary-card"><div class="summary-label">PDF результатів</div><div class="summary-value">{published_pdf_count}</div></div>
    <div class="summary-card"><div class="summary-label">Вхідних файлів</div><div class="summary-value">{len(sources)}</div></div>
</section>
<div class="toolbar">
    <input id="modelSearch" type="search" placeholder="Пошук моделі або етапу…" aria-label="Пошук">
    <span>{html.escape(toolbar_note)}</span>
</div>
<section class="stage-legend" style="--stage-count:{len(config.stages)}" aria-label="Етапи">
    {legend}
</section>
<main id="models" class="models" style="--stage-count:{len(config.stages)}">
    {''.join(model_cards)}
</main>
<section class="source-section">
    <div class="section-heading">
        <h2>Вхідні матеріали</h2>
        <p>Усі вихідні документи доступні за прямими посиланнями для завантаження.</p>
    </div>
    <div class="source-grid">{source_links}</div>
</section>
<section class="info-card">
    <strong>Перевірка комплектності:</strong> {html.escape(missing_note)}
    Кнопка «← До огляду стратегії» у кожному детальному HTML повертає саме на цю сторінку.
</section>
<div class="footer">SITCAR System · Strategy model comparison</div>
</div>
<script>
const search = document.getElementById('modelSearch');
const cards = Array.from(document.querySelectorAll('.model-card'));
search.addEventListener('input', () => {{
    const value = search.value.trim().toLowerCase();
    cards.forEach(card => {{
        card.hidden = !card.dataset.search.includes(value);
    }});
}});
</script>
</body>
</html>
"""
    write_if_changed(config.directory / "index.html", document)

    return BuildStats(
        title=config.title,
        models=len(models),
        html_files=html_count,
        pdf_files=published_pdf_count,
        source_files=len(sources),
        complete_models=complete_models,
        expected_steps=len(config.stages),
        missing_results=missing_results,
    )


def diagnostics_source_section() -> str:
    sources = source_files(DIAGNOSTICS_DIR)
    links = "".join(make_source_button(path, DIAGNOSTICS_DIR) for path in sources)
    return f"""
<!-- SITCAR_DIAGNOSTICS_SOURCES_START -->
<section class="site-source-section">
    <div class="site-source-heading">
        <h2>Вхідні матеріали</h2>
        <p>Документи, використані для діагностики. Натисни на файл, щоб завантажити.</p>
    </div>
    <div class="site-source-grid">{links}</div>
</section>
<!-- SITCAR_DIAGNOSTICS_SOURCES_END -->
"""


def enhance_diagnostics_index() -> None:
    path = DIAGNOSTICS_DIR / "index.html"
    document = path.read_text(encoding="utf-8", errors="replace")
    css_marker = "/* SITCAR_PORTAL_ENHANCEMENTS */"
    if css_marker not in document and "</style>" in document:
        css = """
/* SITCAR_PORTAL_ENHANCEMENTS */
.site-portal-nav { display:flex; gap:8px; margin-bottom:9px; }
.site-portal-home {
    display:inline-flex; align-items:center; min-height:36px; padding:7px 12px;
    border:1px solid var(--border); border-radius:9px; background:var(--card);
    color:#79c0ff; font-size:12px; font-weight:800; text-decoration:none;
}
.site-portal-home:hover { border-color:rgba(88,166,255,.55); background:rgba(88,166,255,.09); }
.site-source-section { margin:12px 0; padding:14px; border:1px solid var(--border); border-radius:14px; background:var(--card); }
.site-source-heading h2 { margin:0; font-size:18px; }
.site-source-heading p { margin:4px 0 0; color:var(--muted); font-size:11px; }
.site-source-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(245px,1fr)); gap:6px; margin-top:10px; }
.site-source-grid .source-link { min-width:0; display:grid; grid-template-columns:auto minmax(0,1fr) auto; align-items:center; gap:8px; padding:8px; border:1px solid rgba(63,185,80,.22); border-radius:8px; background:rgba(63,185,80,.035); text-decoration:none; }
.site-source-grid .source-link:hover { border-color:rgba(63,185,80,.55); background:rgba(63,185,80,.08); }
.site-source-grid .source-ext { padding:3px 5px; border-radius:5px; background:rgba(63,185,80,.12); color:#7ee787; font-size:8px; font-weight:900; }
.site-source-grid .source-name { min-width:0; color:#c9d1d9; font-size:10px; overflow-wrap:anywhere; }
.site-source-grid .source-size { color:var(--muted); font-size:8px; white-space:nowrap; }
"""
        document = document.replace("</style>", css + "</style>", 1)

    nav = """<!-- SITCAR_PORTAL_NAV_START -->
<nav class="site-portal-nav" aria-label="Головна навігація">
    <a class="site-portal-home" href="../index.html">← На головну</a>
</nav>
<!-- SITCAR_PORTAL_NAV_END -->"""
    document = re.sub(
        r"\s*<!-- SITCAR_PORTAL_NAV_START -->.*?<!-- SITCAR_PORTAL_NAV_END -->\s*",
        "\n",
        document,
        flags=re.DOTALL,
    )
    document = re.sub(
        r'<div class="container">\s*',
        '<div class="container">\n' + nav + "\n\n",
        document,
        count=1,
    )

    document = re.sub(
        r"\s*<!-- SITCAR_DIAGNOSTICS_SOURCES_START -->.*?<!-- SITCAR_DIAGNOSTICS_SOURCES_END -->\s*",
        "\n",
        document,
        flags=re.DOTALL,
    )
    source_section = diagnostics_source_section().strip()
    if '<section class="info-card">' in document:
        document = re.sub(
            r'\s*<section class="info-card">',
            "\n\n" + source_section + '\n\n<section class="info-card">',
            document,
            count=1,
        )
    else:
        document = document.replace("</div>\n\n<script>", source_section + "\n</div>\n\n<script>", 1)

    write_if_changed(path, document)


MAIN_STYLE = r"""
:root { --bg:#0d1117; --card:#161b22; --border:#30363d; --text:#f0f6fc; --muted:#8b949e; --blue:#58a6ff; --green:#3fb950; --purple:#bc8cff; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; background:radial-gradient(circle at 50% -10%,rgba(88,166,255,.15),transparent 34%),var(--bg); color:var(--text); font-family:Segoe UI,Inter,Arial,sans-serif; line-height:1.45; }
.container { width:min(1180px,calc(100% - 28px)); margin:auto; padding:52px 0 70px; }
.hero { text-align:center; margin-bottom:28px; }
.eyebrow { color:var(--blue); font-size:11px; font-weight:900; letter-spacing:.12em; text-transform:uppercase; }
h1 { margin:7px 0 0; font-size:clamp(36px,6vw,66px); line-height:1; }
.hero p { max-width:720px; margin:14px auto 0; color:var(--muted); font-size:14px; }
.portal-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }
.portal-card { min-width:0; display:flex; flex-direction:column; min-height:300px; padding:22px; border:1px solid var(--border); border-radius:18px; background:linear-gradient(145deg,rgba(255,255,255,.025),rgba(255,255,255,.008)),var(--card); box-shadow:0 16px 44px rgba(0,0,0,.18); }
.portal-card:hover { transform:translateY(-2px); border-color:rgba(88,166,255,.45); transition:.16s ease; }
.portal-number { display:grid; place-items:center; width:36px; height:36px; border:1px solid rgba(88,166,255,.32); border-radius:10px; color:#79c0ff; background:rgba(88,166,255,.08); font-size:12px; font-weight:900; }
.portal-card.business .portal-number { color:#d2a8ff; border-color:rgba(188,140,255,.35); background:rgba(188,140,255,.08); }
.portal-card.startup .portal-number { color:#7ee787; border-color:rgba(63,185,80,.35); background:rgba(63,185,80,.08); }
.portal-card h2 { margin:20px 0 0; font-size:clamp(20px,2.2vw,27px); line-height:1.15; overflow-wrap:anywhere; }
.portal-card p { margin:10px 0 0; color:var(--muted); font-size:12px; }
.portal-meta { display:flex; flex-wrap:wrap; gap:5px; margin-top:13px; }
.portal-meta span { padding:4px 7px; border:1px solid var(--border); border-radius:999px; color:#c9d1d9; background:rgba(255,255,255,.02); font-size:9px; }
.portal-link { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:auto; padding:11px 13px; border:1px solid rgba(88,166,255,.36); border-radius:10px; color:#79c0ff; background:rgba(88,166,255,.075); font-size:11px; font-weight:900; text-decoration:none; }
.portal-link:hover { background:rgba(88,166,255,.15); }
.footer { padding-top:26px; color:var(--muted); font-size:10px; text-align:center; }
@media(max-width:850px) { .portal-grid { grid-template-columns:1fr; } .portal-card { min-height:230px; } .container { padding-top:32px; } }
"""


def build_main_index(diagnostics_count: int, strategy_stats: list[BuildStats]) -> None:
    by_title = {stat.title: stat for stat in strategy_stats}
    business = by_title["Стратегія для існуючого бізнесу (Соларвест)"]
    startup = by_title["Стратегія для стартапу (Geovizor)"]
    document = f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SITCAR · Головна</title>
<style>
{MAIN_STYLE}
</style>
</head>
<body>
<div class="container">
<header class="hero">
    <div class="eyebrow">SITCAR System</div>
    <h1>Огляд моделей і стратегій</h1>
    <p>Оберіть потрібний напрям. Усередині доступні HTML-звіти, PDF-версії та вихідні файли для завантаження.</p>
</header>
<main class="portal-grid">
    <article class="portal-card diagnostics">
        <div class="portal-number">01</div>
        <h2>Діагностика SITCAR</h2>
        <p>Порівняння моделей за модулями організаційної діагностики, вартістю, токенами та часом.</p>
        <div class="portal-meta"><span>{diagnostics_count} HTML-звітів</span><span>HTML + PDF</span><span>вхідні файли</span></div>
        <a class="portal-link" href="diagnostics/index.html"><span>Відкрити діагностику</span><span>→</span></a>
    </article>
    <article class="portal-card business">
        <div class="portal-number">02</div>
        <h2>Стратегія для існуючого бізнесу (Соларвест)</h2>
        <p>Сім HTML/PDF-етапів від аналізу ринку до інтегрованої стратегії та восьмий, об’єднаний PDF Solar West.</p>
        <div class="portal-meta"><span>{business.models} моделі</span><span>{business.html_files} HTML</span><span>{business.pdf_files} PDF</span></div>
        <a class="portal-link" href="strategy/business/index.html"><span>Відкрити стратегію</span><span>→</span></a>
    </article>
    <article class="portal-card startup">
        <div class="portal-number">03</div>
        <h2>Стратегія для стартапу (Geovizor)</h2>
        <p>Сім етапів створення стратегії GeoVizor — дослідження, вибір, фінансова модель, GTM та інтеграція.</p>
        <div class="portal-meta"><span>{startup.models} моделі</span><span>{startup.html_files} HTML</span><span>{startup.pdf_files} PDF</span></div>
        <a class="portal-link" href="strategy/startup/index.html"><span>Відкрити стратегію</span><span>→</span></a>
    </article>
</main>
<div class="footer">SITCAR System · Локальний портал матеріалів</div>
</div>
</body>
</html>
"""
    write_if_changed(MAIN_INDEX, document)


class LocalLinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.anchor_depth = 0
        self.nested_anchors = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag == "a":
            if self.anchor_depth:
                self.nested_anchors += 1
            self.anchor_depth += 1
        for name, value in attrs:
            if value is None:
                continue
            if (tag == "a" and name.casefold() == "href") or name.casefold() == "src":
                self.links.append((name.casefold(), value))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a":
            self.anchor_depth = max(0, self.anchor_depth - 1)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)


def local_target(source: Path, raw_url: str) -> Path | None:
    value = html.unescape(raw_url).strip()
    if not value or value.startswith("#") or value.startswith("//"):
        return None

    if re.match(r"^[A-Za-z]:[\\/]", value):
        return Path(value).resolve()

    split = urlsplit(value)
    if split.scheme.casefold() in {"http", "https", "mailto", "tel", "data", "javascript"}:
        return None
    if split.scheme.casefold() == "file":
        decoded_file = unquote(split.path)
        if split.netloc:
            decoded_file = f"//{split.netloc}{decoded_file}"
        elif re.match(r"^/[A-Za-z]:/", decoded_file):
            decoded_file = decoded_file[1:]
        return Path(decoded_file).resolve()
    if split.scheme:
        return None

    decoded = unquote(split.path)
    if not decoded:
        return None
    if decoded.startswith("/"):
        return Path(decoded).resolve()
    return (source.parent / Path(decoded)).resolve()


def validate_attachment_completeness(source: Path, document: str) -> list[str]:
    errors: list[str] = []
    section_re = re.compile(
        r"<section\b[^>]*class=[\"'][^\"']*\battachments-card\b[^\"']*[\"'][^>]*>"
        r"(?P<body>.*?)"
        r"</section>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    row_re = re.compile(r"<tr\b[^>]*>(?P<body>.*?)</tr>", flags=re.IGNORECASE | re.DOTALL)
    cell_re = re.compile(r"<td\b[^>]*>(?P<body>.*?)</td>", flags=re.IGNORECASE | re.DOTALL)

    for section in section_re.finditer(document):
        for row in row_re.finditer(section.group("body")):
            first_cell = cell_re.search(row.group("body"))
            if first_cell is None:
                continue
            cell_body = first_cell.group("body")
            visible_name = strip_markup(cell_body)
            anchor = re.search(
                r"<a\b(?P<attrs>[^>]*)href\s*=\s*[\"'](?P<href>.*?)[\"'][^>]*>",
                cell_body,
                flags=re.IGNORECASE | re.DOTALL,
            )
            rel_source = source.relative_to(SITE_ROOT)
            if anchor is None:
                errors.append(f"Вкладення без посилання: {rel_source} -> {visible_name}")
                continue
            if not re.search(r"\bdownload(?:\s|=|$)", anchor.group(0), flags=re.IGNORECASE):
                errors.append(f"Вкладення без download: {rel_source} -> {visible_name}")
            target = local_target(source, anchor.group("href"))
            if target is not None:
                try:
                    target.relative_to((source.parent / "Files").resolve())
                except ValueError:
                    errors.append(
                        f"Вкладення веде поза Files/: {rel_source} -> {anchor.group('href')}"
                    )
            if target is not None and target.name.casefold() != visible_name.casefold():
                errors.append(
                    f"Посилання вкладення веде не на той файл: {rel_source} -> "
                    f"{visible_name} != {target.name}"
                )

    return errors


def validate_site() -> list[str]:
    errors: list[str] = []
    html_files = [MAIN_INDEX, DIAGNOSTICS_DIR / "index.html"]
    html_files.extend(DIAGNOSTICS_DIR.glob("*.html"))
    for config in STRATEGIES:
        html_files.extend(config.directory.glob("*.html"))

    seen: set[Path] = set()
    for source in sorted(html_files, key=lambda item: str(item).casefold()):
        source = source.resolve()
        if source in seen:
            continue
        seen.add(source)
        if not source.is_file():
            errors.append(f"Відсутній HTML: {source.relative_to(SITE_ROOT)}")
            continue

        parser = LocalLinkCollector()
        try:
            document = source.read_text(encoding="utf-8", errors="replace")
            parser.feed(document)
        except Exception as exc:
            errors.append(f"Не вдалося розібрати {source.relative_to(SITE_ROOT)}: {exc}")
            continue

        errors.extend(validate_attachment_completeness(source, document))

        if parser.nested_anchors:
            errors.append(
                f"Вкладені <a> у {source.relative_to(SITE_ROOT)}: {parser.nested_anchors}"
            )

        for attr, raw_url in parser.links:
            target = local_target(source, raw_url)
            if target is None:
                continue
            try:
                target.relative_to(SITE_ROOT.resolve())
            except ValueError:
                errors.append(
                    f"Посилання виходить за межі сайту: {source.relative_to(SITE_ROOT)} -> {raw_url}"
                )
                continue
            if not target.exists():
                errors.append(
                    f"Бите {attr}: {source.relative_to(SITE_ROOT)} -> {raw_url}"
                )

    errors.extend(validate_artifact_integrity())
    return errors


def validate_artifact_integrity() -> list[str]:
    """Check report pairs and basic signatures of every published artifact."""

    errors: list[str] = []
    html_paths: set[Path] = {MAIN_INDEX, DIAGNOSTICS_DIR / "index.html"}
    pdf_paths: set[Path] = set()
    source_paths: set[Path] = set()

    html_paths.update(DIAGNOSTICS_DIR.glob("*.html"))
    pdf_paths.update(DIAGNOSTICS_DIR.glob("*.pdf"))
    pdf_paths.update((DIAGNOSTICS_DIR / "PDF_RESULTS").glob("*.pdf"))
    source_paths.update(source_files(DIAGNOSTICS_DIR))

    for config in STRATEGIES:
        html_paths.update(config.directory.glob("*.html"))
        pdf_paths.update(config.directory.glob("*.pdf"))
        pdf_paths.update((config.directory / "pdf_result").glob("*.pdf"))
        source_paths.update(source_files(config.directory))

    for path in sorted(html_paths, key=lambda item: str(item).casefold()):
        if not path.is_file():
            continue
        document = path.read_text(encoding="utf-8", errors="replace")
        lowered = document.casefold()
        if "<html" not in lowered or "</html>" not in lowered:
            errors.append(f"Неповний HTML: {path.relative_to(SITE_ROOT)}")
        if '<meta charset="utf-8"' not in lowered and "<meta charset='utf-8'" not in lowered:
            errors.append(f"HTML без UTF-8 meta: {path.relative_to(SITE_ROOT)}")

    for path in sorted(pdf_paths | {p for p in source_paths if p.suffix.casefold() == ".pdf"}, key=lambda item: str(item).casefold()):
        if not path.is_file():
            errors.append(f"Відсутній PDF: {path.relative_to(SITE_ROOT)}")
            continue
        with path.open("rb") as stream:
            header = stream.read(8)
            stream.seek(max(path.stat().st_size - 4096, 0))
            trailer = stream.read()
        if not header.startswith(b"%PDF-") or b"%%EOF" not in trailer:
            errors.append(f"Пошкоджена сигнатура PDF: {path.relative_to(SITE_ROOT)}")

    office_extensions = {".docx", ".xlsx"}
    for path in sorted(
        (p for p in source_paths if p.suffix.casefold() in office_extensions),
        key=lambda item: str(item).casefold(),
    ):
        if not zipfile.is_zipfile(path):
            errors.append(f"Пошкоджений Office-файл: {path.relative_to(SITE_ROOT)}")
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                corrupt_member = archive.testzip()
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append(f"Не читається Office-файл {path.relative_to(SITE_ROOT)}: {exc}")
            continue
        if corrupt_member:
            errors.append(
                f"Пошкоджений елемент {corrupt_member} у {path.relative_to(SITE_ROOT)}"
            )

    diagnostic_html = {
        path.stem: path
        for path in DIAGNOSTICS_DIR.glob("*.html")
        if path.name.casefold() != "index.html"
    }
    diagnostic_pdf = {
        path.stem: path
        for path in [
            *DIAGNOSTICS_DIR.glob("*.pdf"),
            *(DIAGNOSTICS_DIR / "PDF_RESULTS").glob("*.pdf"),
        ]
    }
    for stem in sorted(diagnostic_html.keys() - diagnostic_pdf.keys()):
        errors.append(f"Немає парного PDF: diagnostics/{stem}.html")
    for stem in sorted(diagnostic_pdf.keys() - diagnostic_html.keys()):
        errors.append(
            f"Немає парного HTML: {diagnostic_pdf[stem].relative_to(SITE_ROOT)}"
        )

    for config in STRATEGIES:
        report_html = {
            path.stem: path
            for path in config.directory.glob("*.html")
            if STRATEGY_FILE_RE.match(path.name)
        }
        report_pdf = {
            path.stem: path
            for path in [
                *config.directory.glob("*.pdf"),
                *(config.directory / "pdf_result").glob("*.pdf"),
            ]
            if STRATEGY_FILE_RE.match(path.name)
        }
        for stem in sorted(report_html.keys() - report_pdf.keys()):
            errors.append(f"Немає парного PDF: {config.key}/{stem}.html")
        for stem in sorted(report_pdf.keys() - report_html.keys()):
            errors.append(
                f"Немає парного HTML: {report_pdf[stem].relative_to(SITE_ROOT)}"
            )

    return errors


def count_diagnostics_reports(diagnostics_builder: ModuleType) -> int:
    return sum(
        1
        for path in DIAGNOSTICS_DIR.glob("*.html")
        if diagnostics_builder.FILE_RE.match(path.name)
    )


def run_build() -> int:
    diagnostics_builder = load_diagnostics_builder()
    unresolved_attachments: list[str] = []

    print("SITCAR site builder")
    print(f"Корінь: {SITE_ROOT}")
    print()

    diagnostic_patch = patch_result_files(
        DIAGNOSTICS_DIR,
        diagnostics_builder.FILE_RE,
        "← До огляду діагностики",
        diagnostics_builder,
        add_diagnostics_tax=True,
    )
    print(
        "Діагностика: "
        f"перевірено {diagnostic_patch['checked']}, "
        f"оновлено {diagnostic_patch['changed']}, "
        f"посилань на вкладення {diagnostic_patch['links']}"
    )

    for config in STRATEGIES:
        strategy_patch = patch_result_files(
            config.directory,
            STRATEGY_FILE_RE,
            "← До огляду стратегії",
            diagnostics_builder,
            add_diagnostics_tax=False,
        )
        print(
            f"{config.key}: перевірено {strategy_patch['checked']}, "
            f"оновлено {strategy_patch['changed']}, "
            f"посилань на вкладення {strategy_patch['links']}"
        )
        unresolved = strategy_patch["unresolved"]
        assert isinstance(unresolved, list)
        for warning in unresolved:
            unresolved_attachments.append(f"{config.key}: {warning}")
            print(f"  ERROR вкладення: {warning}")

    unresolved_diagnostics = diagnostic_patch["unresolved"]
    assert isinstance(unresolved_diagnostics, list)
    for warning in unresolved_diagnostics:
        unresolved_attachments.append(f"diagnostics: {warning}")
        print(f"  ERROR вкладення diagnostics: {warning}")

    diagnostics_builder.AUTO_PATCH_RESULT_HTML = False
    diagnostics_builder.main()
    enhance_diagnostics_index()

    strategy_stats = [
        build_strategy_index(config, diagnostics_builder)
        for config in STRATEGIES
    ]
    build_main_index(count_diagnostics_reports(diagnostics_builder), strategy_stats)

    print("Комплектність стратегій:")
    for stat in strategy_stats:
        print(
            f"  {stat.title}: {stat.models} мод., "
            f"HTML={stat.html_files}, PDF={stat.pdf_files}, Files={stat.source_files}"
        )
        for missing in stat.missing_results:
            print(f"    INFO відсутній результат: {missing}")

    errors = [
        f"Не вдалося зіставити вкладення: {item}"
        for item in unresolved_attachments
    ]
    errors.extend(validate_site())
    if errors:
        print()
        print(f"ПОМИЛКИ ПОСИЛАНЬ: {len(errors)}")
        for error in errors:
            print(f"  ERROR {error}")
        return 1

    print()
    print(
        "ГОТОВО: index.html згенеровано, посилання справні, "
        "пари результатів і цілісність файлів перевірено."
    )
    return 0


def run_check() -> int:
    errors = validate_site()
    if errors:
        print(f"Знайдено проблем: {len(errors)}")
        for error in errors:
            print(f"  ERROR {error}")
        return 1
    print(
        "Перевірка успішна: посилання справні, пари HTML/PDF повні, "
        "а PDF/DOCX/XLSX проходять перевірку цілісності."
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Генерує головний портал, індекси SITCAR та перевіряє локальні посилання."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="лише перевірити вже згенерований сайт без зміни файлів",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    return run_check() if args.check else run_build()


if __name__ == "__main__":
    raise SystemExit(main())
