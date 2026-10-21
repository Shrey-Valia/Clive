"""Assemble the research report: shared stylesheet, inlined charts, optional section files."""
import base64
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OB = os.path.join(ROOT, "Options Bot")
IMAGES = {
    "IMG_VRP": "charts/out/SPY_vrp.png",
    "IMG_LONGHIST": "longhist/out/equity_log.png",
    "IMG_DIP": "tuning/signals/out/S1_dip_pcs_SPY_price.png",
    "IMG_SPREADS": "tuning/spreads/out/finalists.png",
    "IMG_WHEEL": "tuning/wheel/out/F1_wheel_qqq_equity_dd.png",
    "IMG_EVENTS": "tuning/events/out/e1_implied_vs_realized.png",
    "IMG_P10K": "tuning/portfolio/out/equity_10k.png",
    "IMG_STRESS": "tuning/portfolio/out/stress_bars.png",
}
# Optional HTML fragments dropped into <!--NAME--> markers when the file exists.
FRAGMENTS = {
    "SHORT_BACKTEST": os.path.join(HERE, "fragments", "short_backtest.html"),
    "STRATEGY_REVIEW": os.path.join(HERE, "fragments", "strategy_review.html"),
}
PENDING = {
    "SHORT_BACKTEST": '<div class="pending"><b>STILL RUNNING</b><p>A research agent is rebuilding 2016–2026 "top gainers at 15:45" from Alpaca minute and daily bars, and backtesting the checklist with realistic costs, borrow and stops. It will judge on 2022–2026 after choosing on 2016–2021. This section will update in place with the verdict.</p></div>',
}


def img(path):
    return "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()


html = open(os.path.join(HERE, "research-report.template.html")).read()
html = html.replace("{{STYLE}}", open(os.path.join(HERE, "_style.html")).read())
html = html.replace("{{ASOF}}", dt.datetime.now().strftime("%b %d, %Y %H:%M ET").upper())
for key, rel in IMAGES.items():
    p = os.path.join(OB, rel)
    if os.path.exists(p):
        html = html.replace("{{" + key + "}}", img(p))
for key, path in FRAGMENTS.items():
    if os.path.exists(path):
        html = html.replace(f"<!--{key}-->", open(path).read())
    elif key in PENDING:
        html = html.replace(f"<!--{key}-->", PENDING[key])
out = os.path.join(HERE, "research-report.html")
open(out, "w").write(html)
left = [k for k in IMAGES if "{{" + k + "}}" in html]
print(out, f"{len(html)/1e6:.1f} MB", "missing images:", left or "none", file=sys.stderr)
