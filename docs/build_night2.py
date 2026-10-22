"""Assemble the night-two report: shared stylesheet, inlined charts, optional long-side fragment."""
import base64
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRATCH = "/private/tmp/claude-501/-Users-shreyvalia-Desktop-Clive-Trading-Bot/0599da58-f919-445c-8527-b80ba8eb6e90/scratchpad"
IMAGES = {
    "IMG_PENNY_DECOMP": os.path.join(ROOT, "Penny Research/out/decomposition_by_bucket.png"),
    "IMG_0DTE": os.path.join(ROOT, "Options Bot/tuning/zero_dte/out/decomp_1dte.png"),
    "IMG_EVENTS": os.path.join(SCRATCH, "ev-events-1440.png"),
}
LONGSIDE = os.path.join(HERE, "fragments", "night2_longside.html")
UPDATE = os.path.join(HERE, "fragments", "night2_update.html")

html = open(os.path.join(HERE, "night2.template.html")).read()
html = html.replace("{{STYLE}}", open(os.path.join(HERE, "_style.html")).read())
if os.path.exists(LONGSIDE):
    html = html.replace("<!--LONGSIDE-->", open(LONGSIDE).read())
if os.path.exists(UPDATE):
    html = html.replace("<!--UPDATE-->", open(UPDATE).read())
for key, path in IMAGES.items():
    if os.path.exists(path):
        html = html.replace("{{" + key + "}}", "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode())
out = os.path.join(HERE, "night2-report.html")
open(out, "w").write(html)
missing = [k for k in IMAGES if "{{" + k + "}}" in html]
print(out, f"{len(html)/1e6:.1f} MB", "missing:", missing or "none", file=sys.stderr)
