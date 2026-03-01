import os
import time
import random
import json
import datetime as dt
from typing import Optional, Dict, Any, List, Tuple

import requests
from requests.exceptions import ReadTimeout, ConnectionError, Timeout

# =========================
# CONFIG
# =========================

URL = "https://www.kuehne-nagel.com/"
API_KEY = os.environ.get("PSI_API_KEY", "")  # export PSI_API_KEY="..."

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
OUT_DIR = "reports"

CATEGORIES = ["performance", "accessibility", "best-practices", "seo"]

GOOD = 90
OK = 70

# chart settings
CHART_DAYS = 7
CHART_W = 920
CHART_H = 220
CHART_PAD_L = 44
CHART_PAD_R = 16
CHART_PAD_T = 18
CHART_PAD_B = 38

SESSION = requests.Session()


# =========================
# PSI REQUEST
# =========================

def fetch(strategy: str, max_attempts: int = 7) -> Dict[str, Any]:
    params = {"url": URL, "strategy": strategy, "category": CATEGORIES}
    if API_KEY:
        params["key"] = API_KEY

    # (connect timeout, read timeout)
    timeout = (10, 300)

    for attempt in range(1, max_attempts + 1):
        try:
            r = SESSION.get(API, params=params, timeout=timeout)

            if r.status_code == 200:
                return r.json()

            if r.status_code == 429:
                wait = min(90, (2 ** (attempt - 1))) + random.uniform(0, 1.5)
                print("[{}] 429 Too Many Requests → retry {}/{} in {:.1f}s".format(
                    strategy, attempt, max_attempts, wait
                ))
                time.sleep(wait)
                continue

            # Other HTTP errors: show details and fail
            try:
                details = r.json()
            except Exception:
                details = r.text
            raise RuntimeError("[{}] PSI error {}: {}".format(strategy, r.status_code, details))

        except (ReadTimeout, Timeout, ConnectionError) as e:
            wait = min(90, (2 ** (attempt - 1))) + random.uniform(0, 1.5)
            print("[{}] Network/timeout: {} → retry {}/{} in {:.1f}s".format(
                strategy, str(e), attempt, max_attempts, wait
            ))
            time.sleep(wait)
            continue

    raise RuntimeError("[{}] PSI failed after {} attempts (timeouts/429).".format(strategy, max_attempts))


# =========================
# PARSE METRICS
# =========================

def lh_score(data: Dict[str, Any], category: str) -> int:
    return int(round(data["lighthouseResult"]["categories"][category]["score"] * 100))


def audit_display(audits: Dict[str, Any], key: str):
    a = audits.get(key, {})
    return a.get("displayValue") or a.get("numericValue")


def core_web_vitals(data: Dict[str, Any]) -> Dict[str, Any]:
    audits = data["lighthouseResult"]["audits"]
    return {
        "LCP": audit_display(audits, "largest-contentful-paint"),
        "INP": audit_display(audits, "interaction-to-next-paint"),
        "CLS": audit_display(audits, "cumulative-layout-shift"),
        "FCP": audit_display(audits, "first-contentful-paint"),
        "TTFB": audit_display(audits, "server-response-time"),
    }


def status_chip(score: int) -> str:
    if score >= GOOD:
        return "good"
    if score >= OK:
        return "ok"
    return "bad"


def arrow(delta: Optional[int]) -> str:
    if delta is None:
        return "—"
    if delta > 0:
        return "▲ +{}".format(delta)
    if delta < 0:
        return "▼ {}".format(delta)
    return "• 0"


# =========================
# FILE IO
# =========================

def safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def list_last_snapshots(out_dir: str, days: int) -> List[Dict[str, Any]]:
    items: List[Tuple[str, str]] = []
    for name in os.listdir(out_dir):
        if name.startswith("snapshot-") and name.endswith(".json"):
            date_part = name[len("snapshot-"):-len(".json")]
            items.append((date_part, os.path.join(out_dir, name)))

    items.sort(key=lambda x: x[0])  # YYYY-MM-DD sorts lexicographically
    items = items[-days:]

    snapshots: List[Dict[str, Any]] = []
    for date_part, path in items:
        obj = safe_read_json(path)
        if obj and isinstance(obj, dict):
            snapshots.append(obj)

    return snapshots


def svg_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


# =========================
# SVG CHART (7 DAYS PERF)
# =========================

def build_perf_svg(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "<div class='meta'>No history yet (need at least 1 snapshot file).</div>"

    labels: List[str] = []
    mob: List[int] = []
    desk: List[int] = []

    for s in history:
        labels.append(str(s.get("date", ""))[5:])  # MM-DD
        mob.append(int(s["mobile"]["scores"]["performance"]))
        desk.append(int(s["desktop"]["scores"]["performance"]))

    n = len(labels)
    minv = max(0, min(min(mob), min(desk)) - 5)
    maxv = min(100, max(max(mob), max(desk)) + 5)
    if maxv - minv < 10:
        minv = max(0, minv - 5)
        maxv = min(100, maxv + 5)

    plot_w = CHART_W - CHART_PAD_L - CHART_PAD_R
    plot_h = CHART_H - CHART_PAD_T - CHART_PAD_B

    def x(i: int) -> float:
        if n == 1:
            return CHART_PAD_L + plot_w / 2
        return CHART_PAD_L + (plot_w * i / float(n - 1))

    def y(v: int) -> float:
        if maxv == minv:
            return CHART_PAD_T + plot_h / 2
        t = (v - minv) / float(maxv - minv)
        return CHART_PAD_T + (plot_h * (1.0 - t))

    def path(series: List[int]) -> str:
        pts = ["{:.2f},{:.2f}".format(x(i), y(v)) for i, v in enumerate(series)]
        return "M " + " L ".join(pts) if pts else ""

    def points(series: List[int], cls: str) -> str:
        circles = []
        for i, v in enumerate(series):
            circles.append("<circle class='{cls}' cx='{cx:.2f}' cy='{cy:.2f}' r='3.3'/>".format(
                cls=cls, cx=x(i), cy=y(v)
            ))
        return "".join(circles)

    # ticks: min / mid / max
    ticks = [minv, int((minv + maxv) / 2), maxv]

    # grid + y labels
    ygrid = []
    for tv in ticks:
        yy = y(tv)
        ygrid.append("<line x1='{l}' y1='{y:.2f}' x2='{r}' y2='{y:.2f}' class='svg-grid'/>".format(
            l=CHART_PAD_L, r=CHART_PAD_L + plot_w, y=yy
        ))
        ygrid.append("<text x='{x}' y='{y:.2f}' text-anchor='end' class='svg-y'>{tv}</text>".format(
            x=CHART_PAD_L - 8, y=yy + 4, tv=tv
        ))

    # x labels
    xlabels = []
    for i, lab in enumerate(labels):
        xlabels.append("<text x='{:.2f}' y='{}' text-anchor='middle' class='svg-x'>{}</text>".format(
            x(i), CHART_PAD_T + plot_h + 22, svg_escape(lab)
        ))

    # deltas
    last_m = mob[-1]
    last_d = desk[-1]
    prev_m = mob[-2] if len(mob) >= 2 else None
    prev_d = desk[-2] if len(desk) >= 2 else None
    dm = (last_m - prev_m) if prev_m is not None else None
    dd = (last_d - prev_d) if prev_d is not None else None

    legend = """
      <div class="legend">
        <span class="leg"><span class="sw sw-m"></span> Mobile: <b>{m}</b> <span class="delta">{dm}</span></span>
        <span class="leg"><span class="sw sw-d"></span> Desktop: <b>{d}</b> <span class="delta">{dd}</span></span>
      </div>
    """.format(m=last_m, d=last_d, dm=arrow(dm), dd=arrow(dd))

    svg = """{legend}
<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="7-day Performance trend">
  <rect x="0" y="0" width="{w}" height="{h}" rx="16" class="svg-bg"/>
  {ygrid}
  <line x1="{l}" y1="{t}" x2="{l}" y2="{b}" class="svg-axis"/>
  <line x1="{l}" y1="{b}" x2="{r}" y2="{b}" class="svg-axis"/>

  <path d="{mp}" class="svg-line-m"/>
  <g class="svg-pts-m">{mpts}</g>

  <path d="{dp}" class="svg-line-d"/>
  <g class="svg-pts-d">{dpts}</g>

  {xlabels}
</svg>
""".format(
        legend=legend,
        w=CHART_W,
        h=CHART_H,
        ygrid="".join(ygrid),
        l=CHART_PAD_L,
        r=CHART_PAD_L + plot_w,
        t=CHART_PAD_T,
        b=CHART_PAD_T + plot_h,
        mp=path(mob),
        dp=path(desk),
        mpts=points(mob, "ptm"),
        dpts=points(desk, "ptd"),
        xlabels="".join(xlabels),
    )
    return svg


# =========================
# MANAGER HTML
# =========================

def build_manager_html(
    today: str,
    snapshot: Dict[str, Any],
    prev_snapshot: Optional[Dict[str, Any]],
    history: List[Dict[str, Any]]
) -> str:
    now_m = snapshot["mobile"]["scores"]
    now_d = snapshot["desktop"]["scores"]

    prev_m = prev_snapshot["mobile"]["scores"] if prev_snapshot else None
    prev_d = prev_snapshot["desktop"]["scores"] if prev_snapshot else None

    def perf_kpi(label: str, now_scores: Dict[str, Any], prev_scores: Optional[Dict[str, Any]]) -> str:
        now = int(now_scores["performance"])
        prev = int(prev_scores["performance"]) if prev_scores else None
        d = (now - prev) if prev is not None else None
        return """
          <div class="kpi">
            <div class="kpi-title">{label}</div>
            <div class="kpi-value">{now}</div>
            <div class="kpi-delta">{delta}</div>
          </div>
        """.format(label=label, now=now, delta=arrow(d))

    def score_grid(title: str, now_scores: Dict[str, Any], prev_scores: Optional[Dict[str, Any]]) -> str:
        cards = []
        for c in CATEGORIES:
            now = int(now_scores[c])
            prev = int(prev_scores[c]) if (prev_scores and c in prev_scores) else None
            d = (now - prev) if prev is not None else None
            chip = status_chip(now)
            cards.append("""
              <div class="card {chip}">
                <div class="k">{name}</div>
                <div class="v">{now}</div>
                <div class="d">{delta}</div>
              </div>
            """.format(
                chip=chip,
                name=c.replace("-", " ").title(),
                now=now,
                delta=arrow(d)
            ))

        return """
          <h2>{title}</h2>
          <div class="grid">
            {cards}
          </div>
        """.format(title=title, cards="".join(cards))

    chart_svg = build_perf_svg(history)

    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>PageSpeed — Latest</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, Roboto, Arial; margin: 26px; }}
    .meta {{ color:#555; margin-top: 6px; }}
    .wrap {{ max-width: 980px; }}
    .top {{ display:flex; justify-content:space-between; align-items:flex-end; gap: 16px; flex-wrap: wrap; }}
    .pill {{ padding: 6px 10px; border-radius: 999px; background:#f2f2f2; font-size: 12px; }}
    .kpi-row {{ display:flex; gap: 12px; flex-wrap: wrap; margin: 16px 0 8px; }}
    .kpi {{ border:1px solid #e8e8e8; border-radius:16px; padding:12px 14px; min-width: 220px; flex: 1; }}
    .kpi-title {{ color:#666; font-size: 12px; }}
    .kpi-value {{ font-size: 42px; font-weight: 800; line-height: 1.0; margin-top: 6px; }}
    .kpi-delta {{ margin-top: 6px; color:#444; }}
    h2 {{ margin: 18px 0 10px; }}
    .grid {{ display:flex; gap:12px; flex-wrap:wrap; }}
    .card {{ border:1px solid #e8e8e8; border-radius:16px; padding:12px 14px; min-width: 210px; }}
    .card.good {{ border-color:#cfe9d7; }}
    .card.ok {{ border-color:#efe3bf; }}
    .card.bad {{ border-color:#f0c7c7; }}
    .k {{ color:#666; font-size: 12px; }}
    .v {{ font-size: 34px; font-weight: 800; margin-top: 4px; }}
    .d {{ margin-top: 6px; color:#444; }}
    .two {{ display:grid; grid-template-columns: 1fr; gap: 12px; }}
    @media (min-width: 900px) {{
      .two {{ grid-template-columns: 1fr 1fr; }}
    }}
    details {{ margin: 18px 0; }}
    pre {{ background:#f6f6f6; padding:12px; border-radius:12px; overflow:auto; }}
    a {{ color: inherit; }}

    /* chart */
    .chart {{ margin-top: 18px; }}
    .legend {{ display:flex; gap: 14px; flex-wrap: wrap; margin: 10px 0 8px; color:#444; }}
    .leg {{ display:flex; align-items:center; gap:8px; }}
    .sw {{ display:inline-block; width:14px; height:4px; border-radius:999px; }}
    .sw-m {{ background:#111; }}
    .sw-d {{ background:#111; opacity:.55; }}
    .delta {{ color:#555; }}
    .svg-bg {{ fill:#fafafa; stroke:#e8e8e8; }}
    .svg-grid {{ stroke:#e9e9e9; stroke-width:1; }}
    .svg-axis {{ stroke:#d7d7d7; stroke-width:1.2; }}
    .svg-line-m {{ fill:none; stroke:#111; stroke-width:2.2; }}
    .svg-line-d {{ fill:none; stroke:#111; stroke-opacity:.55; stroke-width:2.2; stroke-dasharray:6 5; }}
    .svg-pts-m circle {{ fill:#111; }}
    .svg-pts-d circle {{ fill:#111; fill-opacity:.55; }}
    .svg-x {{ font-size:11px; fill:#666; }}
    .svg-y {{ font-size:11px; fill:#666; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1 style="margin:0;">PageSpeed — Latest</h1>
        <div class="meta"><b>Date:</b> {today} · <b>URL:</b> <a href="{url}">{url}</a></div>
      </div>
      <div class="pill">KPI focus: Performance (Mobile & Desktop)</div>
    </div>

    <div class="kpi-row">
      {kpi_m}
      {kpi_d}
    </div>

    <div class="chart">
      <h2>7-day trend (Performance)</h2>
      {chart_svg}
    </div>

    <div class="two">
      <div>
        {mobile_grid}
      </div>
      <div>
        {desktop_grid}
      </div>
    </div>

    <details>
      <summary><b>Core Web Vitals (Mobile / Desktop)</b></summary>
      <h3>Mobile</h3>
      <pre>{cwv_m}</pre>
      <h3>Desktop</h3>
      <pre>{cwv_d}</pre>
    </details>

    <details>
      <summary><b>How to read</b></summary>
      <ul>
        <li><b>▲/▼</b> shows delta vs previous run.</li>
        <li>Status borders: <b>good ≥ {good}</b>, <b>ok ≥ {ok}</b>, <b>bad &lt; {ok}</b>.</li>
      </ul>
    </details>

  </div>
</body>
</html>
""".format(
        today=today,
        url=URL,
        kpi_m=perf_kpi("Mobile Performance", now_m, prev_m),
        kpi_d=perf_kpi("Desktop Performance", now_d, prev_d),
        chart_svg=chart_svg,
        mobile_grid=score_grid("Mobile scores", now_m, prev_m),
        desktop_grid=score_grid("Desktop scores", now_d, prev_d),
        cwv_m=json.dumps(snapshot["mobile"]["cwv"], indent=2, ensure_ascii=False),
        cwv_d=json.dumps(snapshot["desktop"]["cwv"], indent=2, ensure_ascii=False),
        good=GOOD,
        ok=OK,
    )


# =========================
# MAIN
# =========================

def main():
    today = dt.datetime.utcnow().strftime("%Y-%m-%d")
    os.makedirs(OUT_DIR, exist_ok=True)

    latest_json = os.path.join(OUT_DIR, "latest.json")
    prev_snapshot = safe_read_json(latest_json)

    print("Fetching PageSpeed...")

    mobile_raw = fetch("mobile")
    desktop_raw = fetch("desktop")

    snapshot = {
        "date": today,
        "url": URL,
        "mobile": {
            "scores": {c: lh_score(mobile_raw, c) for c in CATEGORIES},
            "cwv": core_web_vitals(mobile_raw),
        },
        "desktop": {
            "scores": {c: lh_score(desktop_raw, c) for c in CATEGORIES},
            "cwv": core_web_vitals(desktop_raw),
        },
    }

    # Save raw PSI JSON (optional)
    write_json(os.path.join(OUT_DIR, "psi-mobile-{}.json".format(today)), mobile_raw)
    write_json(os.path.join(OUT_DIR, "psi-desktop-{}.json".format(today)), desktop_raw)

    # Save snapshot history + latest pointer
    write_json(os.path.join(OUT_DIR, "snapshot-{}.json".format(today)), snapshot)
    write_json(latest_json, snapshot)

    # 7-day history from snapshots (including today)
    history = list_last_snapshots(OUT_DIR, CHART_DAYS)

    html = build_manager_html(today, snapshot, prev_snapshot, history)

    latest_html = os.path.join(OUT_DIR, "latest.html")
    with open(latest_html, "w", encoding="utf-8") as f:
        f.write(html)

    dated_html = os.path.join(OUT_DIR, "report-{}.html".format(today))
    with open(dated_html, "w", encoding="utf-8") as f:
        f.write(html)

    print("✅ Done.")
    print("Latest report:", latest_html)
    print("Archive report:", dated_html)


if __name__ == "__main__":
    main()