"""MCP Apps -- HTML5 visualizations for diagnose and audit.

Served via ui:// scheme with content type text/html;profile=mcp-app.
Rendered in a sandboxed iframe in VS Code / Copilot.
"""

from __future__ import annotations

import html
import json
from typing import Any


def render_gap_matrix(report_data: dict[str, Any]) -> str:
    """Render the diagnose gap report as a color-coded HTML5 gap matrix.

    Shows severity distribution, gap types, expandable endpoint details,
    and a summary bar -- screenshot-ready for the README.
    """
    summary = report_data.get("summary", {})
    gaps = report_data.get("gaps", [])

    total = summary.get("total_gaps", 0)
    critical = summary.get("by_severity", {}).get("critical", 0)
    warning = summary.get("by_severity", {}).get("warning", 0)
    info = summary.get("by_severity", {}).get("info", 0)
    by_type = summary.get("by_type", {})
    total_endpoints = summary.get("total_endpoints", 0)

    # group gaps by endpoint
    by_endpoint: dict[str, list[dict]] = {}
    for gap in gaps:
        key = f"{gap.get('method', '').upper()} {gap.get('endpoint', '')}"
        by_endpoint.setdefault(key, []).append(gap)

    # sort endpoints by gap count descending
    sorted_endpoints = sorted(by_endpoint.items(), key=lambda x: len(x[1]), reverse=True)

    # build type distribution bars
    type_bars = ""
    if by_type:
        max_count = max(by_type.values()) if by_type else 1
        for gap_type, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            pct = (count / max_count) * 100
            label = gap_type.replace("_", " ")
            type_bars += f"""
            <div class="type-row">
              <span class="type-label">{_esc(label)}</span>
              <div class="type-bar-bg">
                <div class="type-bar" style="width:{pct}%"></div>
              </div>
              <span class="type-count">{count}</span>
            </div>"""

    # build endpoint rows
    endpoint_rows = ""
    for endpoint, ep_gaps in sorted_endpoints[:30]:
        ep_critical = sum(1 for g in ep_gaps if g.get("severity") == "critical")
        ep_warning = sum(1 for g in ep_gaps if g.get("severity") == "warning")
        ep_info = sum(1 for g in ep_gaps if g.get("severity") == "info")
        severity_class = "critical" if ep_critical else ("warning" if ep_warning else "info")

        gap_items = ""
        for g in ep_gaps[:20]:
            sev = g.get("severity", "info")
            param = f" &middot; {_esc(g.get('parameter', ''))}" if g.get("parameter") else ""
            msg = _esc(g.get("message", ""))[:120]
            gap_items += f'<div class="gap-item {sev}">{_esc(g.get("gap_type", ""))}{param}: {msg}</div>'

        endpoint_rows += f"""
        <details class="endpoint-row {severity_class}">
          <summary>
            <span class="ep-name">{_esc(endpoint)}</span>
            <span class="badges">
              {f'<span class="badge critical">{ep_critical}</span>' if ep_critical else ''}
              {f'<span class="badge warning">{ep_warning}</span>' if ep_warning else ''}
              {f'<span class="badge info">{ep_info}</span>' if ep_info else ''}
            </span>
          </summary>
          <div class="gap-details">{gap_items}</div>
        </details>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gap matrix -- ReadMe Doc Healer</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0d1117; color: #c9d1d9; padding: 20px; font-size: 14px; }}
  h1 {{ font-size: 18px; margin-bottom: 4px; color: #f0f6fc; }}
  .subtitle {{ color: #8b949e; font-size: 13px; margin-bottom: 16px; }}

  /* summary bar */
  .summary-bar {{ display: flex; gap: 16px; margin-bottom: 20px; }}
  .stat-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                padding: 12px 16px; flex: 1; text-align: center; }}
  .stat-value {{ font-size: 28px; font-weight: 700; }}
  .stat-label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }}
  .stat-card.critical .stat-value {{ color: #f85149; }}
  .stat-card.warning .stat-value {{ color: #d29922; }}
  .stat-card.info .stat-value {{ color: #58a6ff; }}
  .stat-card.total .stat-value {{ color: #f0f6fc; }}

  /* severity distribution donut (css only) */
  .donut-container {{ display: flex; align-items: center; gap: 20px; margin-bottom: 20px; }}
  .donut {{ width: 80px; height: 80px; border-radius: 50%; position: relative;
            background: conic-gradient(
              #f85149 0deg {_deg(critical, total)}deg,
              #d29922 {_deg(critical, total)}deg {_deg(critical + warning, total)}deg,
              #58a6ff {_deg(critical + warning, total)}deg 360deg
            ); }}
  .donut-hole {{ position: absolute; top: 15px; left: 15px; width: 50px; height: 50px;
                 border-radius: 50%; background: #0d1117; display: flex;
                 align-items: center; justify-content: center; font-weight: 700; font-size: 16px; }}

  /* type distribution */
  .section-title {{ font-size: 14px; color: #f0f6fc; margin: 16px 0 8px; font-weight: 600; }}
  .type-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
  .type-label {{ width: 160px; font-size: 12px; color: #8b949e; text-align: right; }}
  .type-bar-bg {{ flex: 1; height: 14px; background: #21262d; border-radius: 3px; overflow: hidden; }}
  .type-bar {{ height: 100%; background: #58a6ff; border-radius: 3px; transition: width 0.3s; }}
  .type-count {{ width: 32px; font-size: 12px; color: #8b949e; }}

  /* endpoint list */
  .endpoint-row {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px;
                   margin-bottom: 4px; overflow: hidden; }}
  .endpoint-row summary {{ padding: 8px 12px; cursor: pointer; display: flex;
                           align-items: center; justify-content: space-between; }}
  .endpoint-row summary:hover {{ background: #1c2128; }}
  .endpoint-row.critical {{ border-left: 3px solid #f85149; }}
  .endpoint-row.warning {{ border-left: 3px solid #d29922; }}
  .endpoint-row.info {{ border-left: 3px solid #58a6ff; }}
  .ep-name {{ font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }}
  .badges {{ display: flex; gap: 4px; }}
  .badge {{ padding: 2px 6px; border-radius: 10px; font-size: 11px; font-weight: 600; }}
  .badge.critical {{ background: #f8514933; color: #f85149; }}
  .badge.warning {{ background: #d2992233; color: #d29922; }}
  .badge.info {{ background: #58a6ff33; color: #58a6ff; }}
  .gap-details {{ padding: 4px 12px 8px; border-top: 1px solid #21262d; }}
  .gap-item {{ font-size: 12px; padding: 3px 0; color: #8b949e; }}
  .gap-item.critical {{ color: #f85149; }}
  .gap-item.warning {{ color: #d29922; }}
  .gap-item.info {{ color: #8b949e; }}

  .footer {{ margin-top: 16px; color: #484f58; font-size: 11px; text-align: center; }}
</style>
</head>
<body>
  <h1>Gap matrix</h1>
  <div class="subtitle">{total_endpoints} endpoints &middot; {total} gaps found</div>

  <div class="summary-bar">
    <div class="stat-card total"><div class="stat-value">{total}</div><div class="stat-label">Total gaps</div></div>
    <div class="stat-card critical"><div class="stat-value">{critical}</div><div class="stat-label">Critical</div></div>
    <div class="stat-card warning"><div class="stat-value">{warning}</div><div class="stat-label">Warning</div></div>
    <div class="stat-card info"><div class="stat-value">{info}</div><div class="stat-label">Info</div></div>
  </div>

  <div class="donut-container">
    <div class="donut"><div class="donut-hole">{total}</div></div>
    <div>
      <div style="color:#f85149">&#9679; Critical: {critical} ({_pct(critical, total)}%)</div>
      <div style="color:#d29922">&#9679; Warning: {warning} ({_pct(warning, total)}%)</div>
      <div style="color:#58a6ff">&#9679; Info: {info} ({_pct(info, total)}%)</div>
    </div>
  </div>

  <div class="section-title">Gap types</div>
  {type_bars}

  <div class="section-title" style="margin-top:20px">Endpoints (worst first)</div>
  {endpoint_rows}

  <div class="footer">ReadMe Doc Healer -- gap analysis</div>
</body>
</html>"""


def render_audit_dashboard(report_data: dict[str, Any]) -> str:
    """Render the audit triage report as an HTML5 dashboard.

    Shows score gauges, ranked worst pages, failed searches,
    and negative feedback -- screenshot-ready for the README.
    """
    pq = report_data.get("page_quality", {})
    st = report_data.get("search_terms", {})
    fb = report_data.get("feedback", {})
    project = report_data.get("project", "unknown")
    offline = report_data.get("offline", False)
    avg_score = pq.get("average_score", 0)

    # worst pages table
    worst_rows = ""
    for page in pq.get("worst_pages", [])[:10]:
        score = page.get("score", 0)
        color = "#f85149" if score < 30 else ("#d29922" if score < 60 else "#3fb950")
        title = _esc(page.get("title", ""))
        errors = page.get("errors", 0)
        warnings = page.get("warnings", 0)
        worst_rows += f"""
        <tr>
          <td class="pg-title">{title}</td>
          <td style="color:{color}; font-weight:700">{score}</td>
          <td class="err">{errors}</td>
          <td class="warn">{warnings}</td>
        </tr>"""

    # zero-result searches
    zero_items = ""
    for term in st.get("top_no_results", [])[:10]:
        zero_items += f"""
        <div class="search-item">
          <span class="search-term">"{_esc(term.get('term', ''))}"</span>
          <span class="search-count">{term.get('searches', 0)} searches</span>
        </div>"""

    # low-result searches
    low_items = ""
    for term in st.get("top_low_results", [])[:5]:
        low_items += f"""
        <div class="search-item">
          <span class="search-term">"{_esc(term.get('term', ''))}"</span>
          <span class="search-count">{term.get('searches', 0)} searches, {term.get('results', 0)} results</span>
        </div>"""

    # feedback section
    feedback_items = ""
    for page in fb.get("negative_pages", [])[:5]:
        title = _esc(page.get("title", ""))
        down = page.get("thumbs_down", 0)
        up = page.get("thumbs_up", 0)
        comments_html = ""
        for c in page.get("comments", [])[:3]:
            comments_html += f'<div class="fb-comment">"{_esc(c)}"</div>'
        feedback_items += f"""
        <div class="fb-page">
          <div class="fb-header">
            <span class="fb-title">{title}</span>
            <span class="fb-votes">&#x1F44E; {down} / &#x1F44D; {up}</span>
          </div>
          {comments_html}
        </div>"""

    mode_label = "Offline (fixture data)" if offline else "Live"
    gauge_color = "#f85149" if avg_score < 40 else ("#d29922" if avg_score < 70 else "#3fb950")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Audit dashboard -- ReadMe Doc Healer</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0d1117; color: #c9d1d9; padding: 20px; font-size: 14px; }}
  h1 {{ font-size: 18px; margin-bottom: 4px; color: #f0f6fc; }}
  .subtitle {{ color: #8b949e; font-size: 13px; margin-bottom: 16px; }}
  .mode-badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
                 font-size: 11px; background: #21262d; color: #8b949e; }}

  /* score gauge */
  .gauge-container {{ display: flex; align-items: center; gap: 20px; margin: 16px 0; }}
  .gauge {{ width: 100px; height: 100px; border-radius: 50%; position: relative;
            background: conic-gradient(
              {gauge_color} 0deg {avg_score * 3.6}deg,
              #21262d {avg_score * 3.6}deg 360deg
            ); }}
  .gauge-hole {{ position: absolute; top: 15px; left: 15px; width: 70px; height: 70px;
                 border-radius: 50%; background: #0d1117; display: flex; flex-direction: column;
                 align-items: center; justify-content: center; }}
  .gauge-value {{ font-size: 24px; font-weight: 700; color: {gauge_color}; }}
  .gauge-label {{ font-size: 9px; color: #8b949e; }}

  /* sections */
  .section {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
              padding: 16px; margin-bottom: 16px; }}
  .section-title {{ font-size: 14px; color: #f0f6fc; font-weight: 600; margin-bottom: 10px; }}

  /* worst pages table */
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; font-size: 11px; color: #8b949e; text-transform: uppercase;
        letter-spacing: 0.5px; padding: 6px 8px; border-bottom: 1px solid #30363d; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #21262d; font-size: 13px; }}
  .pg-title {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .err {{ color: #f85149; }}
  .warn {{ color: #d29922; }}

  /* search items */
  .search-item {{ display: flex; justify-content: space-between; padding: 4px 0;
                  border-bottom: 1px solid #21262d; }}
  .search-term {{ color: #c9d1d9; }}
  .search-count {{ color: #8b949e; font-size: 12px; }}

  /* feedback */
  .fb-page {{ margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #21262d; }}
  .fb-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }}
  .fb-title {{ font-weight: 600; }}
  .fb-votes {{ font-size: 12px; color: #8b949e; }}
  .fb-comment {{ font-size: 12px; color: #8b949e; font-style: italic; padding: 2px 0 2px 12px;
                 border-left: 2px solid #30363d; margin: 3px 0; }}

  .footer {{ margin-top: 16px; color: #484f58; font-size: 11px; text-align: center; }}
</style>
</head>
<body>
  <h1>Audit dashboard</h1>
  <div class="subtitle">{_esc(project)} <span class="mode-badge">{mode_label}</span></div>

  <div class="gauge-container">
    <div class="gauge">
      <div class="gauge-hole">
        <div class="gauge-value">{avg_score}</div>
        <div class="gauge-label">/100</div>
      </div>
    </div>
    <div>
      <div style="font-weight:600;color:#f0f6fc">Page quality score</div>
      <div style="color:#8b949e;font-size:12px">Average across all pages</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Worst pages by quality score</div>
    <table>
      <thead><tr><th>Page</th><th>Score</th><th>Errors</th><th>Warnings</th></tr></thead>
      <tbody>{worst_rows}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-title">Zero-result searches</div>
    {zero_items if zero_items else '<div style="color:#8b949e">No zero-result searches found</div>'}
  </div>

  {f'''<div class="section">
    <div class="section-title">Low-result searches</div>
    {low_items}
  </div>''' if low_items else ''}

  <div class="section">
    <div class="section-title">Negative feedback</div>
    {feedback_items if feedback_items else '<div style="color:#8b949e">No negative feedback found</div>'}
  </div>

  <div class="footer">ReadMe Doc Healer -- audit triage</div>
</body>
</html>"""


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return html.escape(str(text)) if text else ""


def _pct(part: int, total: int) -> int:
    """Calculate percentage, safe from division by zero."""
    return round(part / total * 100) if total > 0 else 0


def _deg(part: int, total: int) -> float:
    """Calculate degrees for a conic gradient arc."""
    return round(part / total * 360, 1) if total > 0 else 0
