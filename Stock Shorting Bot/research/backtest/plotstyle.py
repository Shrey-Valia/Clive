"""Terminal look: black background, amber, white text, green/red, monospace."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

AMBER, WHITE, GREEN, RED, GREY = "#ffa028", "#ffffff", "#3ddc84", "#ff4d4d", "#555555"
plt.rcParams.update({
    "figure.facecolor": "black", "axes.facecolor": "black", "savefig.facecolor": "black",
    "axes.edgecolor": GREY, "axes.labelcolor": WHITE, "xtick.color": WHITE, "ytick.color": WHITE,
    "text.color": WHITE, "font.family": "monospace", "font.size": 10, "axes.grid": True,
    "grid.color": "#222222", "grid.linestyle": "-", "axes.titlecolor": AMBER, "axes.titleweight": "bold",
    "legend.facecolor": "black", "legend.edgecolor": GREY, "legend.labelcolor": WHITE,
    "axes.prop_cycle": matplotlib.cycler(color=[AMBER, GREEN, WHITE, RED, "#6fa8ff", "#c792ea"]),
})
