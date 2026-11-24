"""从 stackdiagram 源 JSON 生成简洁版架构网页。

用法：python build_architecture_html.py [src.json out.html]
缺省生成 services（docs/architecture.html）；传 stackdiagram-dataflow.json 生成主链路页。

视觉对齐 stackdiagram 第一版：米色纸面、虚线边框、等宽技术标注。
布局：卡片按带内统一高度、边框对齐、间距加大；配置了第二行的组排到主流程下方。
内容与源 JSON 逐字一致，带顺序与带内节点顺序取自源文件的 groups 数组。
"""

import html
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
INTROS = {
    "stackdiagram-services.json": (
        "绒星 2.0（Fuzzstar）系统架构",
        "猫主人的健康管理与陪伴 App：建档 → 数字分身 → 规则初筛（红线零 LLM）→ 饮食体重 → 聊天 / 叫声解读。",
    ),
    "stackdiagram-dataflow.json": (
        "绒星 2.0 · 数字分身初筛主链路",
        "从建档到日常陪伴的完整数据流：红线命中直接出「建议立即就医」卡，不占配额、绝不经 LLM；未命中才消耗配额走 AI 建议。",
    ),
}

# 排在主流程下方第二行的组（旁路），避免长连线横穿主流程卡片
ROW2 = {"stackdiagram-dataflow.json": ["g_async_flow"]}
ROW_GAP = 72

CARD_W = 210
PAD = 12
COL_GAP = 28
BAND_GAP = 48
BAND_PAD = 20
BAND_HEAD = 34
NODE_GAP = 24
MAX_PER_COL = 6

CANVAS = "#F2EADB"
BAND_FILL = "#F8F2E4"
BAND_STROKE = "#B7B3A6"
CARD_FILL = "#FFFDF6"
CARD_STROKE = "#8F8D84"
TEXT = "#3A3831"
MUTED = "#6B6A62"
EDGE = "#8A8577"

TOKEN = re.compile(r"[A-Za-z0-9_./+-]+|\s+|.")


def tw(s, size):
    return sum(size if ord(c) > 0x2E80 else size * 0.55 for c in s)


def wrap(s, size, max_w):
    """按像素宽度折行。CJK 逐字断行，ASCII 词不拆开；超宽单词硬切。"""
    lines, cur = [], ""
    for t in TOKEN.findall(s):
        if t.isspace():
            if cur:
                cur += " "
            continue
        if not cur:
            if tw(t, size) > max_w:
                part = ""
                for ch in t:
                    if tw(part + ch, size) > max_w:
                        lines.append(part)
                        part = ch
                    else:
                        part += ch
                cur = part
            else:
                cur = t
        elif tw(cur + t, size) <= max_w:
            cur += t
        else:
            lines.append(cur.rstrip())
            cur = t
    if cur:
        lines.append(cur.rstrip())
    return lines


def esc(s):
    return html.escape(s, quote=True)


def card_parts(node):
    """(标题行, 技术行, 说明行, 自然高度)"""
    max_tw = CARD_W - 2 * PAD
    title_lines = wrap(node["label"], 13, max_tw)[:2]
    tech_lines = wrap(node.get("tech", ""), 11, max_tw)[:2] if node.get("tech") else []
    note_lines = wrap(node.get("note", ""), 11, max_tw) if node.get("note") else []
    h = PAD + len(title_lines) * 19 + len(tech_lines) * 16
    if note_lines:
        h += 5 + len(note_lines) * 16
    h += PAD
    return title_lines, tech_lines, note_lines, h


def layout_band(g, nodes, x, top):
    """单行布局一个带，返回 (placed, band_w, band_h)。"""
    ids = [i for i in g["children"] if i in nodes]
    cols = [ids[i:i + MAX_PER_COL] for i in range(0, len(ids), MAX_PER_COL)]

    parts = {nid: card_parts(nodes[nid]) for nid in ids}
    card_h = max(p[3] for p in parts.values())

    placed, col_x = {}, []
    cx = x + BAND_PAD
    for _ in cols:
        col_x.append(cx)
        cx += CARD_W + COL_GAP
    band_w = BAND_PAD * 2 + len(cols) * CARD_W + (len(cols) - 1) * COL_GAP

    col_h = []
    for ci, col in enumerate(cols):
        y = 0
        for nid in col:
            t, tech, notes, _ = parts[nid]
            placed[nid] = {"x": col_x[ci], "y": y, "h": card_h,
                           "t": t, "tech": tech, "notes": notes, "n": nodes[nid]}
            y += card_h + NODE_GAP
        col_h.append(y - NODE_GAP)

    band_h = BAND_HEAD + BAND_PAD + max(col_h) + BAND_PAD
    for p in placed.values():
        p["y"] += top + BAND_HEAD + BAND_PAD
    return placed, band_w, band_h, top


def main():
    argv = sys.argv[1:]
    src = HERE / (argv[0] if len(argv) > 0 else "stackdiagram-services.json")
    out = HERE / (argv[1] if len(argv) > 1 else "architecture.html")
    doc = json.loads(src.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in doc["nodes"]}
    h1, sub = INTROS[src.name]
    row2_ids = set(ROW2.get(src.name, []))

    grouped = [c for g in doc["groups"] for c in g["children"]]
    loose = [nid for nid in nodes if nid not in grouped]
    assert not loose, f"存在未归组节点: {loose}"

    # ---- 布局：第一行主流程，第二行旁路 ----
    row1 = [g for g in doc["groups"] if g["id"] not in row2_ids]
    row2 = [g for g in doc["groups"] if g["id"] in row2_ids]

    bands = []
    x, top = 36, 20
    for g in row1:
        placed, w, h, _ = layout_band(g, nodes, x, top)
        bands.append({"gid": g["id"], "label": g["label"], "row": 1,
                      "x": x, "w": w, "h": h, "top": top, "placed": placed})
        x += w + BAND_GAP
    row1_bottom = max(b["top"] + b["h"] for b in bands)

    x2 = bands[-1]["x"] if row2 else 0
    top2 = row1_bottom + ROW_GAP
    for g in row2:
        placed, w, h, _ = layout_band(g, nodes, x2, top2)
        bands.append({"gid": g["id"], "label": g["label"], "row": 2,
                      "x": x2, "w": w, "h": h, "top": top2, "placed": placed})
        x2 += w + BAND_GAP

    width = max(x - BAND_GAP, x2 - BAND_GAP if row2 else 0) + 36
    height = max(b["top"] + b["h"] for b in bands) + 48

    pos = {nid: {"x": p["x"], "y": p["y"], "w": CARD_W, "h": p["h"], "row": b["row"]}
           for b in bands for nid, p in b["placed"].items()}

    # ---- 连线路径 ----
    paths = []
    for e in doc["edges"]:
        s, t = pos.get(e["from"]), pos.get(e["to"])
        if not s or not t:
            continue
        if s["row"] != t["row"]:
            sx, sy = s["x"] + CARD_W / 2, s["y"] + s["h"]
            tx, ty = t["x"] + CARD_W / 2, t["y"]
            mid = (sy + ty) / 2
            d = f"M {sx:.0f} {sy:.0f} C {sx:.0f} {mid:.0f} {tx:.0f} {mid:.0f} {tx:.0f} {ty:.0f}"
        else:
            sx, sy = s["x"] + CARD_W, s["y"] + s["h"] / 2
            tx, ty = t["x"], t["y"] + t["h"] / 2
            if tx > sx:
                dx = max(40, (tx - sx) * 0.45)
                d = f"M {sx:.0f} {sy:.0f} C {sx + dx:.0f} {sy:.0f} {tx - dx:.0f} {ty:.0f} {tx:.0f} {ty:.0f}"
            else:
                d = (f"M {sx:.0f} {sy:.0f} C {sx + 30:.0f} {sy + 60:.0f} "
                     f"{tx - 30:.0f} {ty + 60:.0f} {tx:.0f} {ty:.0f}")
        tip = f"{nodes[e['from']]['label']} → {nodes[e['to']]['label']}"
        if e.get("label"):
            tip += f"：{e['label']}"
        paths.append((d, tip))

    # ---- SVG ----
    o = []
    o.append(f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'aria-labelledby="dgTitle dgDesc" xmlns="http://www.w3.org/2000/svg">')
    o.append(f'<title id="dgTitle">{esc(h1)}</title>')
    o.append(f'<desc id="dgDesc">按边界排布的 {len(nodes)} 个组件，'
             f'共 {len(doc["edges"])} 条调用关系。</desc>')
    o.append('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
             'markerHeight="7" orient="auto-start-reverse"><path d="M2 1.5L8.5 5L2 8.5" fill="none" '
             f'stroke="{EDGE}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>'
             '</marker></defs>')

    o.append('<g class="bands">')
    for b in bands:
        o.append(f'<rect x="{b["x"]}" y="{b["top"]}" width="{b["w"]}" height="{b["h"]}" rx="10" '
                 f'fill="{BAND_FILL}" stroke="{BAND_STROKE}" stroke-width="1" '
                 f'stroke-dasharray="5 4"/>')
    o.append('</g>')

    for b in bands:
        o.append(f'<text class="bl" x="{b["x"] + BAND_PAD}" y="{b["top"] + 21}">{esc(b["label"])}</text>')

    g = ['<g class="edges">']
    for d, tip in paths:
        g.append(f'<path d="{d}" fill="none" stroke="{EDGE}" stroke-width="1" '
                 f'marker-end="url(#ah)"><title>{esc(tip)}</title></path>')
    g.append('</g>')
    o.append("".join(g))

    for b in bands:
        g = ['<g class="nodes">']
        for nid, p in b["placed"].items():
            n = p["n"]
            tip = n["label"]
            if n.get("tech"):
                tip += f"\n{n['tech']}"
            if n.get("note"):
                tip += f"\n{n['note']}"
            g.append(f'<g class="node"><title>{esc(tip)}</title>')
            g.append(f'<rect x="{p["x"]}" y="{p["y"]}" width="{CARD_W}" height="{p["h"]}" rx="7" '
                     f'fill="{CARD_FILL}" stroke="{CARD_STROKE}" stroke-width="1" '
                     f'stroke-dasharray="4 3"/>')
            ty = p["y"] + PAD
            for ln in p["t"]:
                g.append(f'<text class="tt" x="{p["x"] + PAD}" y="{ty + 10}">{esc(ln)}</text>')
                ty += 19
            for ln in p["tech"]:
                g.append(f'<text class="tech" x="{p["x"] + PAD}" y="{ty + 9}">{esc(ln)}</text>')
                ty += 16
            if p["notes"]:
                ty += 5
                for ln in p["notes"]:
                    g.append(f'<text class="note" x="{p["x"] + PAD}" y="{ty + 9}">{esc(ln)}</text>')
                    ty += 16
            g.append('</g>')
        g.append('</g>')
        o.append("".join(g))

    o.append('</svg>')
    svg = "".join(o)

    rows = []
    for e in doc["edges"]:
        rows.append(f'<tr><td class="a">{esc(nodes[e["from"]]["label"])}</td>'
                    f'<td class="arrow">→</td><td class="b">{esc(nodes[e["to"]]["label"])}</td>'
                    f'<td class="l">{esc(e.get("label", ""))}</td></tr>')
    table = "".join(rows)
    n_nodes, n_edges = len(doc["nodes"]), len(doc["edges"])

    page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(h1)}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:{CANVAS};color:{TEXT};
 font:13px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1560px;margin:0 auto;padding:36px 32px 72px}}
header{{border-bottom:1px solid {BAND_STROKE};padding-bottom:20px;margin-bottom:26px}}
h1{{font-size:18px;font-weight:500;margin:0 0 8px;letter-spacing:.2px}}
.sub{{color:{MUTED};font-size:13px;max-width:920px}}
.meta{{color:{MUTED};font-size:12px;margin-top:12px;display:flex;gap:20px;flex-wrap:wrap}}
.meta b{{font-weight:500;color:{TEXT}}}
.bar{{display:flex;gap:10px;align-items:center;margin:0 0 22px}}
button{{font:inherit;font-size:12px;color:{TEXT};background:transparent;
 border:1px dashed {CARD_STROKE};border-radius:7px;padding:6px 13px;cursor:pointer}}
button[aria-pressed="false"]{{color:{MUTED};border-color:{BAND_STROKE}}}
button:hover{{border-color:{TEXT}}}
.scroll{{overflow-x:auto;border:1px solid {BAND_STROKE};border-radius:12px;
 padding:6px;background:{CANVAS}}}
svg text{{font-family:inherit}}
svg .tt{{font-size:13px;font-weight:500;fill:{TEXT}}}
svg .bl{{font-size:12px;font-weight:500;fill:{MUTED};letter-spacing:.5px}}
svg .tech{{font-size:11px;fill:{MUTED};font-family:ui-monospace,Menlo,Consolas,monospace}}
svg .note{{font-size:11px;fill:{MUTED}}}
body.no-notes svg .note{{display:none}}
section{{margin-top:30px}}
h2{{font-size:14px;font-weight:500;margin:0 0 12px}}
table{{border-collapse:collapse;width:100%;font-size:12px;
 background:{CARD_FILL};border:1px solid {BAND_STROKE};border-radius:10px}}
th,td{{text-align:left;padding:7px 12px;border-bottom:1px solid #E4DDCB;vertical-align:top}}
th{{color:{MUTED};font-weight:500}}
tr:last-child td{{border-bottom:none}}
td.a{{white-space:nowrap}} td.b{{white-space:nowrap}} td.arrow{{color:{MUTED};width:18px}}
td.l{{color:{MUTED}}}
footer{{margin-top:40px;color:{MUTED};font-size:12px;
 border-top:1px solid {BAND_STROKE};padding-top:16px}}
</style></head><body>
<div class="wrap">
<header>
<h1>{esc(h1)}</h1>
<div class="sub">{esc(sub)}</div>
<div class="meta">
<span><b>{n_nodes}</b> 个组件</span><span><b>{n_edges}</b> 条调用关系</span>
<span><b>{len(doc['groups'])}</b> 个部署边界</span><span>来源 <b>docs/{esc(src.name)}</b></span>
</div>
</header>

<div class="bar">
<button id="t-notes" aria-pressed="true">隐藏组件说明</button>
<button id="t-list" aria-pressed="false">展开连线说明</button>
</div>

<div class="scroll">{svg}</div>

<section id="conns" hidden>
<h2>连线说明</h2>
<table><thead><tr><th>来源</th><th></th><th>目标</th><th>说明</th></tr></thead>
<tbody>{table}</tbody></table>
</section>

<footer>节点 hover 可看完整说明；连线 hover 可看来源、目标与说明。内容与 stackdiagram 文档逐字一致，仅重排呈现。</footer>
</div>
<script>
var b1=document.getElementById('t-notes'),b2=document.getElementById('t-list'),
    s=document.getElementById('conns');
b1.onclick=function(){{var on=document.body.classList.toggle('no-notes');
 b1.textContent=on?'显示组件说明':'隐藏组件说明';b1.setAttribute('aria-pressed',String(!on));}};
b2.onclick=function(){{var on=s.hasAttribute('hidden');if(on)s.removeAttribute('hidden');else s.setAttribute('hidden','');
 b2.textContent=on?'收起连线说明':'展开连线说明';b2.setAttribute('aria-pressed',String(on));}};
</script>
</body></html>"""

    out.write_text(page, encoding="utf-8")
    print(f"written {out}  ({len(page)} bytes, canvas {width}x{height})")


if __name__ == "__main__":
    sys.exit(main())
