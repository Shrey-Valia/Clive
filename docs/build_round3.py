"""Assemble the round-3 report: shared stylesheet plus inlined charts from the integration branch."""
import base64
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INTEG = "/private/tmp/claude-501/-Users-shreyvalia-Desktop-Clive-Trading-Bot/0599da58-f919-445c-8527-b80ba8eb6e90/scratchpad/integ"
IMAGES = {
    "IMG_SHORT_EQUITY": os.path.join(INTEG, "Stock Shorting Bot/research/round3/out/final_equity.png"),
    "IMG_OPT_BOOK": os.path.join(INTEG, "Options Bot/tuning/round3/out/book_equity_2016_2026.png"),
}

html = open(os.path.join(HERE, "round3.template.html")).read()
html = html.replace("{{STYLE}}", open(os.path.join(HERE, "_style.html")).read())
for key, path in IMAGES.items():
    if os.path.exists(path):
        html = html.replace("{{" + key + "}}", "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode())
out = os.path.join(HERE, "round3-report.html")
open(out, "w").write(html)
missing = [k for k in IMAGES if "{{" + k + "}}" in html]
print(out, f"{len(html)/1e6:.2f} MB", "missing:", missing or "none", file=sys.stderr)
