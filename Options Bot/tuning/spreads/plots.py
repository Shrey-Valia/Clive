"""Terminal-styled matplotlib helpers (black bg, amber accents, monospace)."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

AMBER, WHITE, GREEN, RED, GREY = "#ffa028", "#ffffff", "#3ddc84", "#ff4d4d", "#555555"
CYAN = "#4fc3f7"
PALETTE = [AMBER, GREEN, CYAN, "#c792ea", WHITE]

plt.rcParams.update({
    "figure.facecolor": "black", "axes.facecolor": "black", "savefig.facecolor": "black",
    "axes.edgecolor": GREY, "axes.labelcolor": WHITE, "xtick.color": WHITE, "ytick.color": WHITE,
    "text.color": WHITE, "font.family": "monospace", "font.size": 9, "axes.grid": True,
    "grid.color": "#222222", "grid.linestyle": "-", "grid.linewidth": 0.6, "legend.frameon": False,
    "axes.titlecolor": AMBER, "axes.titleweight": "bold", "axes.titlesize": 10,
})


def shade_oos(ax, oos_start="2025-07-01"):
    import pandas as pd
    ax.axvline(pd.Timestamp(oos_start), color=AMBER, lw=0.8, ls="--")
    ax.text(pd.Timestamp(oos_start), ax.get_ylim()[1], " OOS ->", color=AMBER, va="top", fontsize=8)


def shade_shocks(ax):
    import pandas as pd
    for a, b in (("2024-07-24", "2024-08-09"), ("2025-01-24", "2025-01-31"), ("2025-03-28", "2025-04-15")):
        ax.axvspan(pd.Timestamp(a), pd.Timestamp(b), color=RED, alpha=0.12, lw=0)
