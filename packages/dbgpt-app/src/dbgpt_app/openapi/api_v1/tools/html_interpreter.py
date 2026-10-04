"""html_interpreter tool — render HTML as an interactive report."""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from dbgpt.agent.resource.tool.base import tool

logger = logging.getLogger(__name__)


def make_html_interpreter(react_state: Dict[str, Any], skills_dir: str):
    @tool(
        description=(
            "Render HTML as an interactive web report. This is the only way to show a web report to the user. "
            "[One-shot] Render the [complete] report in a single call; do NOT call this tool repeatedly for the "
            "same report. If the goal is achieved after a successful render, call terminate directly. "
            "[Default usage] Pass the complete HTML string directly: "
            '{"html": "<html>...</html>", "title": "report title"}. '
            "You need to generate the complete HTML code yourself "
            "(including <!DOCTYPE html>, <html>, <head>, <body>, etc.), "
            "then pass it via the html parameter. "
            "HTML can be very long with no length limit; no need to split it into parts. "
            "If the report has multiple sections, merge them into the [same] HTML and render once. "
            "Do not generate multiple reports in separate calls. "
            "[Prohibited] Do not use code_interpreter to write HTML and print it. "
            "Do not use code_interpreter to write HTML to a file and read it back. "
            "Pass the HTML directly to this tool. "
            "[Skill mode - optional, only when using a skill] "
            "If you are using a skill, you may use template mode: "
            '{"template_path": "skill-name/templates/template.html", '
            '"data": {"KEY": "value"}, "title": "title"}. '
            'You may also use file mode: {"file_path": "/path/to/report.html"}'
        )
    )
    async def html_interpreter(
        html: str = "",
        title: str = "Report",
        file_path: str = "",
        template_path: str = "",
        data: dict | str = None,
    ) -> str:
        """Render HTML as an interactive web report."""
        from dbgpt.configs.model_config import STATIC_MESSAGE_IMG_PATH

        skills_path = Path(skills_dir).expanduser().resolve()

        # ── Mode 1: template_path + data ──
        if template_path and template_path.strip():
            tp = template_path.strip()
            target = (skills_path / tp).resolve()
            try:
                target.relative_to(skills_path)
            except ValueError:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Invalid template_path: {tp}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if not target.is_file():
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"Template not found: {tp}. "
                                    "Please retry using the `html` parameter "
                                    "directly — "
                                    "generate complete HTML yourself and pass it via "
                                    '{"html": "<html>...</html>", "title": "title"}.'
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            try:
                raw_template = target.read_text(encoding="utf-8")
            except Exception as e:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Error reading template: {e}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

            replacements = data
            if isinstance(replacements, str):
                try:
                    replacements = json.loads(replacements)
                except Exception:
                    try:
                        fixed = str(replacements).rstrip()
                        if not fixed.endswith("}"):
                            fixed += '"}' if not fixed.endswith('"') else "}"
                        replacements = json.loads(fixed)
                    except Exception:
                        replacements = {}
            if not isinstance(replacements, dict):
                replacements = {}

            auto_data = react_state.get("auto_data", {})
            if isinstance(auto_data, dict):
                replacements = {**auto_data, **replacements}

            ratio_data = react_state.get("ratio_data", {})
            if isinstance(ratio_data, dict):
                replacements = {**ratio_data, **replacements}

            image_url_map = react_state.get("image_url_map", {})
            if isinstance(image_url_map, dict):
                for stem, url in image_url_map.items():
                    chart_key = f"CHART_{stem.upper()}"
                    if chart_key not in replacements:
                        replacements[chart_key] = url

            def _replace_placeholder(m):
                key = m.group(1)
                return str(replacements.get(key, ""))

            html = re.sub(r"\{\{([A-Z_0-9]+)\}\}", _replace_placeholder, raw_template)
            if not title or title == "Report":
                title = target.stem

        # ── Mode 2: file_path ──
        elif file_path and file_path.strip():
            fp = file_path.strip()
            if not os.path.isfile(fp):
                cid = react_state.get("conv_id") or "default"
                from dbgpt.configs.model_config import PILOT_PATH

                alt = os.path.join(PILOT_PATH, "data", cid, os.path.basename(fp))
                if os.path.isfile(alt):
                    fp = alt
                else:
                    return json.dumps(
                        {
                            "chunks": [
                                {
                                    "output_type": "text",
                                    "content": f"File not found: {file_path}",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    html = f.read()
                if not title or title == "Report":
                    title = os.path.splitext(os.path.basename(fp))[0]
            except Exception as e:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Error reading file: {e}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        # ── Mode 3: inline html ──
        if html and isinstance(html, str) and not template_path and not file_path:
            if "\\n" in html:
                html = html.replace("\\n", "\n")
            if "\\t" in html:
                html = html.replace("\\t", "\t")

        if not html or not html.strip():
            return json.dumps(
                {
                    "chunks": [
                        {"output_type": "text", "content": "No HTML content provided"}
                    ]
                },
                ensure_ascii=False,
            )

        # Post-process: fix image URLs
        fixed_html = html.strip()
        try:
            IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
            name_to_served: Dict[str, str] = {}
            if os.path.isdir(STATIC_MESSAGE_IMG_PATH):
                for fname in os.listdir(STATIC_MESSAGE_IMG_PATH):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in IMAGE_EXTS:
                        continue
                    m = re.match(r"^[0-9a-f]{8}_(.+)$", fname, re.IGNORECASE)
                    if m:
                        name_to_served[m.group(1).lower()] = f"/images/{fname}"

            if name_to_served:

                def _fix_img_src(match: re.Match) -> str:
                    prefix = match.group(1)
                    raw_path = match.group(2)
                    quote = match.group(3)
                    filename = raw_path.rsplit("/", 1)[-1].lower()
                    if re.match(r"^[0-9a-f]{8}_.+$", filename, re.IGNORECASE):
                        return match.group(0)
                    if filename in name_to_served:
                        return f"{prefix}{name_to_served[filename]}{quote}"
                    return match.group(0)

                fixed_html = re.sub(
                    r"""(src\s*=\s*["'])([^"']+\.(?:png|jpg|jpeg|gif|svg|webp))(["'])""",
                    _fix_img_src,
                    fixed_html,
                    flags=re.IGNORECASE,
                )
        except Exception:
            pass

        # Auto-append missing images
        try:
            gen_images = react_state.get("generated_images", [])
            if gen_images:
                html_img_stems = set(
                    re.sub(r"^[0-9a-f]+_", "", os.path.basename(src))
                    for src in re.findall(
                        r'<img[^>]+src=["\']([^"\']+)["\']', fixed_html, re.IGNORECASE
                    )
                )

                def _img_stem(url):
                    return re.sub(r"^[0-9a-f]+_", "", os.path.basename(url))

                missing = [
                    url
                    for url in gen_images
                    if url not in fixed_html and _img_stem(url) not in html_img_stems
                ]
                if missing:
                    imgs_html = "".join(
                        f'<div style="margin:16px 0">'
                        f'<img src="{url}" '
                        f'style="max-width:100%;height:auto;border-radius:8px">'
                        f"</div>"
                        for url in missing
                    )
                    section = (
                        '<div style="margin-top:32px"><h2>📊 Analysis Charts</h2>'
                        f"{imgs_html}</div>"
                    )
                    if "</body>" in fixed_html.lower():
                        fixed_html = re.sub(
                            r"(</body>)",
                            section + r"\1",
                            fixed_html,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                    else:
                        fixed_html += section
        except Exception:
            pass

        chunks: List[Dict[str, Any]] = [
            {
                "output_type": "text",
                "content": (
                    "✅ HTML report has been successfully rendered and shown to the user. The report task is complete. "
                    "Do not call html_interpreter again to generate the report. "
                    "If all goals are achieved, call terminate directly to finish."
                ),
            },
            {"output_type": "html", "content": fixed_html, "title": title},
        ]
        return json.dumps({"chunks": chunks}, ensure_ascii=False)

    return html_interpreter
