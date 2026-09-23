import numpy as np


def clear_axes_with_message(ax, message: str):
    ax.clear()
    if message:
        ax.text(
            0.5,
            0.5,
            message,
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=10,
            style="italic",
            color="gray",
        )
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)


def plot_histograms(ax, histograms: list, plot_type: str, colors: list, title: str, xlabel: str, ylabel: str,
                    legend_fontsize: int = 8):
    """
    Plot multiple histograms on a single axes.

    Parameters
    ----------
    histograms: list of tuples (label, counts, bins)
    plot_type: 'bar' | 'line' | 'step'
    """
    ax.clear()

    for idx, (label, counts, bins) in enumerate(histograms):
        color = colors[idx % len(colors)] if colors else None

        if plot_type == "bar":
            ax.bar(
                bins[:-1],
                counts,
                width=bins[1] - bins[0],
                alpha=0.7,
                label=label,
                color=color,
                edgecolor="black",
                linewidth=0.5,
            )
        elif plot_type == "line":
            bin_centers = bins[:-1] + (bins[1] - bins[0]) / 2
            ax.plot(bin_centers, counts, label=label, color=color, linewidth=2, marker="o", markersize=3)
        else:
            ax.step(bins[:-1], counts, label=label, color=color, where="mid", linewidth=1.5)

    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.legend(loc="best", fontsize=legend_fontsize, framealpha=0.9)
    ax.grid(True, alpha=0.3, linestyle="--", linewidth=0.5)
    ax.tick_params(axis="both", labelsize=8)


def values_to_histograms(series: list, histogram_fn) -> list:
    """
    series: list of tuples (label, values_array)
    histogram_fn: callable(values)->(counts, bins)
    Returns list of tuples (label, counts, bins) — готово для plot_histograms.

    Считается один раз; при смене типа графика пересчёт не нужен.
    """
    histograms = []
    for label, values in series:
        values = np.asarray(values, dtype=np.float64)
        values = values[np.isfinite(values)]
        counts, bins = histogram_fn(values)
        histograms.append((label, counts, bins))
    return histograms


def plot_value_arrays_as_histograms(ax, series: list, plot_type: str, colors: list, title: str,
                                   xlabel: str, ylabel: str, histogram_fn):
    """
    series: list of tuples (label, values_array)
    histogram_fn: callable(values)->(counts,bins)
    """
    histograms = values_to_histograms(series, histogram_fn)
    plot_histograms(ax, histograms, plot_type, colors, title, xlabel, ylabel, legend_fontsize=7)

