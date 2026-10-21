"""Inline the chart PNGs into the brief so it publishes as one self-contained page."""
import base64
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMAGES = {
    "IMG_VRP": "charts/out/SPY_vrp.png",
    "IMG_DIP": "tuning/signals/out/S1_dip_pcs_SPY_price.png",
    "IMG_WHEEL": "tuning/wheel/out/F1_wheel_qqq_equity_dd.png",
    "IMG_LONGHIST": "longhist/out/equity_log.png",
    "IMG_SPREADS": "tuning/spreads/out/finalists.png",
}

html = open(os.path.join(HERE, "brief.template.html")).read()
for key, rel in IMAGES.items():
    path = os.path.join(ROOT, rel)
    if f"{{{{{key}}}}}" in html and os.path.exists(path):
        data = base64.b64encode(open(path, "rb").read()).decode()
        html = html.replace(f"{{{{{key}}}}}", f"data:image/png;base64,{data}")
out = os.path.join(HERE, "overnight-brief.html")
open(out, "w").write(html)
missing = [k for k in IMAGES if f"{{{{{k}}}}}" in html]
print(out, f"{len(html)/1e6:.1f} MB", "missing:", missing or "none", file=sys.stderr)
