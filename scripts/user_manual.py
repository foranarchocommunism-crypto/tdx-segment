"""Render the user README's small Markdown subset as an offline HTML manual."""
from __future__ import annotations

import html
import re


def inline(text: str) -> str:
    text = html.escape(text, quote=True)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    def image(match: re.Match[str]) -> str:
        alt, target = match.groups()
        if target.startswith("https://img.shields.io/"):
            return f'<span class="badge">{alt}</span>'
        return f'<img src="{target}" alt="{alt}">'

    text = re.sub(r"!\[([^\]]*)\]\(([^\s)]+)\)", image, text)
    text = re.sub(r"\[([^\[\]]+)\]\(([^\s)]+)\)", r'<a href="\2">\1</a>', text)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)


def render_manual(markdown: str) -> str:
    """Only support syntax used by README; stop on unsupported fenced code."""
    output: list[str] = []
    paragraph: list[str] = []
    in_list = False
    in_table = False

    def flush() -> None:
        if paragraph:
            output.append("<p>" + inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            raise ValueError("The user manual does not support fenced developer commands")
        if in_list and not line.startswith("- "):
            output.append("</ul>")
            in_list = False
        if in_table and not line.startswith("|"):
            output.append("</tbody></table></div>")
            in_table = False
        if not line:
            flush()
        elif re.match(r"</?(div|details|summary)\b", line):
            flush()
            output.append(line)
        elif match := re.match(r"^(#{1,6})\s+(.+)$", line):
            flush()
            level, title = len(match[1]), match[2]
            anchor = re.sub(r"[^\w\-\s]", "", title.lower()).replace(" ", "-")
            output.append(f'<h{level} id="{anchor}">{inline(title)}</h{level}>')
        elif line == "---":
            flush()
            output.append("<hr>")
        elif line.startswith("|"):
            flush()
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                continue
            if not in_table:
                output.append('<div class="table-scroll"><table><thead><tr>' + "".join("<th>" + inline(cell) + "</th>" for cell in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                output.append("<tr>" + "".join("<td>" + inline(cell) + "</td>" for cell in cells) + "</tr>")
        elif line.startswith("- "):
            flush()
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append("<li>" + inline(line[2:]) + "</li>")
        else:
            paragraph.append(line)
    flush()
    if in_list:
        output.append("</ul>")
    if in_table:
        output.append("</tbody></table></div>")
    body = "\n".join(output)
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>缠论线段自动划分 · 使用说明</title>
<style>
:root{color-scheme:light;--ink:#233249;--muted:#62738a;--accent:#247565}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#edf2f7;color:var(--ink);font:16px/1.85 "Microsoft YaHei","PingFang SC",sans-serif}
main{max-width:1040px;margin:36px auto;padding:42px 54px;background:white;border:1px solid #dfe6ee;border-radius:22px;box-shadow:0 12px 42px #2030490c}
h1{font-size:34px;letter-spacing:1px;line-height:1.4;margin:8px 0 14px}h2{font-size:25px;margin:42px 0 18px;padding-bottom:10px;border-bottom:1px solid #dfe6ee}h3{font-size:20px;margin-top:30px}
p{margin:15px 0}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}img{display:block;width:100%;height:auto;margin:24px auto;border-radius:12px}
code{font:0.9em Consolas,monospace;background:#eef3f7;border-radius:5px;padding:3px 6px;overflow-wrap:anywhere}strong{font-weight:650}
.badge{display:inline-block;padding:2px 12px;margin:0 4px;border:1px solid #cfe5dd;border-radius:20px;background:#f0f8f4;color:#316f60;font-size:13px}
.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;margin:20px 0;font-size:15px}th{text-align:left;background:#f0f5f8}th,td{padding:12px 16px;border:1px solid #dfe6ee}
details{margin:24px 0;border:1px solid #dfe6ee;border-radius:12px;padding:14px 20px}summary{cursor:pointer;color:#36566a}li{margin:10px 0}hr{border:0;border-top:1px solid #dfe6ee;margin-top:34px}
@media(max-width:700px){main{margin:0;border:0;border-radius:0;padding:26px 20px}h1{font-size:28px}h2{font-size:23px}th,td{padding:10px}body{font-size:15px}}
@media print{body{background:white}main{margin:0;border:0;box-shadow:none;padding:0}details{break-inside:avoid}h2,h3{break-after:avoid}}
</style></head><body><main>""" + body + "</main></body></html>\n"
