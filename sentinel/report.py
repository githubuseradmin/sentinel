"""Render a monitoring snapshot as a status page (HTML), text, or JSON.

Pure string builders: they take a plain snapshot dict (built by the engine) and
never touch the network or DB, so they are easy to unit-test. The HTML is a
self-contained dark-theme page (inline CSS, no JS) — drop it on any static host
or open it locally. Every dynamic value is HTML-escaped.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

_DOT = {"up": "#36d399", "degraded": "#fbbd23", "down": "#f87272", "unknown": "#8a99a3"}


def _ts(value) -> str:
    if not value:
        return "—"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _latency(ms) -> str:
    return f"{ms:.0f} ms" if isinstance(ms, (int, float)) else "—"


def _uptime(pct) -> str:
    return f"{pct:.2f}%" if isinstance(pct, (int, float)) else "—"


# ---------------------------------------------------------------------------
def render_text(snapshot: dict) -> str:
    lines = [f"sentinel — {snapshot['overall'].upper()}  ({_ts(snapshot.get('generated_at'))})", ""]
    for t in snapshot.get("targets", []):
        lines.append(
            f"  [{t['status']:<8}] {t['name']:<16} {_latency(t.get('latency_ms')):>8}  "
            f"up {_uptime(t.get('uptime')):>8}  {t.get('detail', '')}"
        )
    incidents = snapshot.get("incidents", [])
    if incidents:
        lines.append("")
        lines.append("Open incidents:")
        for inc in incidents:
            lines.append(f"  - {inc['target']}: {inc.get('detail', '')} (since {_ts(inc.get('started_ts'))})")
    return "\n".join(lines)


def render_json(snapshot: dict) -> str:
    return json.dumps(snapshot, indent=2, ensure_ascii=False)


def render_html(snapshot: dict) -> str:
    overall = snapshot.get("overall", "unknown")
    rows = []
    for t in snapshot.get("targets", []):
        st = t["status"]
        rows.append(
            "<tr>"
            f'<td><span class="dot" style="background:{_DOT.get(st, _DOT["unknown"])}"></span>'
            f'<span class="st st--{html.escape(st)}">{html.escape(st)}</span></td>'
            f'<td class="name">{html.escape(t["name"])}</td>'
            f'<td class="muted">{html.escape(str(t.get("target", "")))}</td>'
            f"<td>{_latency(t.get('latency_ms'))}</td>"
            f"<td>{_uptime(t.get('uptime'))}</td>"
            f'<td class="muted">{html.escape(str(t.get("detail", "")))}</td>'
            "</tr>"
        )

    incidents = "".join(
        f'<li><b>{html.escape(i["target"])}</b> — {html.escape(str(i.get("detail", "")))} '
        f'<span class="muted">(since {_ts(i.get("started_ts"))})</span></li>'
        for i in snapshot.get("incidents", [])
    ) or '<li class="muted">No open incidents.</li>'

    events = "".join(
        f'<li><span class="ev ev--{html.escape(e.get("severity", "info"))}">'
        f'{html.escape(e.get("severity", "info"))}</span> '
        f'{html.escape(e.get("title", ""))} '
        f'<span class="muted">{_ts(e.get("ts"))}</span></li>'
        for e in snapshot.get("events", [])
    ) or '<li class="muted">No events yet.</li>'

    return f"""<!DOCTYPE html>
<html lang="en" data-status="{html.escape(overall)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sentinel — status</title>
<style>
  :root {{ --bg:#0a0e13; --panel:#0f161e; --line:#1d2935; --text:#d6e0e4; --muted:#8a99a3; --accent:#36d399; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; line-height:1.5; }}
  .wrap {{ max-width:920px; margin:0 auto; padding:2rem 1.25rem; }}
  h1 {{ font-size:1.4rem; margin:0 0 .25rem; }}
  .overall {{ display:inline-block; font-family:ui-monospace,Consolas,monospace; font-weight:700;
    padding:.2rem .7rem; border-radius:999px; }}
  .overall--up {{ background:rgba(54,211,153,.15); color:#36d399; }}
  .overall--degraded {{ background:rgba(251,189,35,.15); color:#fbbd23; }}
  .overall--down {{ background:rgba(248,114,114,.15); color:#f87272; }}
  .overall--unknown {{ background:rgba(138,153,163,.15); color:#8a99a3; }}
  .muted {{ color:var(--muted); }}
  table {{ width:100%; border-collapse:collapse; margin:1.25rem 0; background:var(--panel);
    border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  th, td {{ text-align:left; padding:.6rem .8rem; border-bottom:1px solid var(--line); font-size:.92rem; }}
  th {{ color:var(--muted); font-weight:600; text-transform:uppercase; font-size:.7rem; letter-spacing:.06em; }}
  tr:last-child td {{ border-bottom:0; }}
  .dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:.5rem; }}
  .name {{ font-family:ui-monospace,Consolas,monospace; }}
  .st {{ font-family:ui-monospace,Consolas,monospace; font-size:.85rem; }}
  h2 {{ font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:1.5rem 0 .5rem; }}
  ul {{ list-style:none; margin:0; padding:0; }}
  li {{ padding:.4rem 0; border-bottom:1px solid var(--line); font-size:.9rem; }}
  .ev {{ font-family:ui-monospace,Consolas,monospace; font-size:.75rem; padding:.1rem .4rem;
    border-radius:4px; background:rgba(138,153,163,.15); }}
  .ev--critical {{ background:rgba(248,114,114,.18); color:#f87272; }}
  .ev--warning {{ background:rgba(251,189,35,.18); color:#fbbd23; }}
  .ev--recovery {{ background:rgba(54,211,153,.18); color:#36d399; }}
  footer {{ color:var(--muted); font-size:.8rem; margin-top:2rem; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>sentinel</h1>
  <p><span class="overall overall--{html.escape(overall)}">{html.escape(overall.upper())}</span></p>
  <table>
    <thead><tr><th>Status</th><th>Target</th><th>Address</th><th>Latency</th><th>Uptime</th><th>Detail</th></tr></thead>
    <tbody>{"".join(rows) or '<tr><td colspan="6" class="muted">No targets.</td></tr>'}</tbody>
  </table>
  <h2>Open incidents</h2>
  <ul>{incidents}</ul>
  <h2>Recent events</h2>
  <ul>{events}</ul>
  <footer>Generated {_ts(snapshot.get('generated_at'))} · sentinel</footer>
</div>
</body>
</html>
"""
