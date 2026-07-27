"""The multi-panel image, unchanged.

Only reached when an explicit ``--export`` (or the legacy ``--output``) names an
image file: the terminal report is what a plain run prints. Kept as it was so an
exported PNG still looks like the ones already circulating.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from .fetch import normalize_title


def build_figure(
    df: pd.DataFrame, start_dt: datetime, end_dt: datetime, who: str, window_label: str
) -> Figure:
    """Three-panel figure: stacked daily bars + top tickets + summary."""
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "axes.titlesize": 12,
        }
    )

    start_date = start_dt.date()
    end_date = end_dt.date()
    span_days = (end_date - start_date).days + 1
    all_days = [start_date + timedelta(days=i) for i in range(span_days)]

    if df.empty:
        pivot = pd.DataFrame(0.0, index=all_days, columns=[])
    else:
        pivot = df.pivot_table(
            index="date", columns="ticket_id", values="hours", aggfunc="sum", fill_value=0
        ).reindex(all_days, fill_value=0)
        # Largest tickets at the bottom of the stack
        pivot = pivot[pivot.sum().sort_values(ascending=False).index]

    fig = plt.figure(figsize=(14, 8.5), constrained_layout=True)
    gs = fig.add_gridspec(2, 3, height_ratios=[1.6, 1])

    # --- Panel 1: daily stacked bars ---------------------------------------
    ax1 = fig.add_subplot(gs[0, :])
    cmap = plt.get_cmap("tab20")
    colors = [cmap(i % 20) for i in range(max(1, len(pivot.columns)))]

    x = np.arange(len(all_days))
    bottom = np.zeros(len(all_days))
    for ticket, color in zip(pivot.columns, colors, strict=False):
        vals = pivot[ticket].to_numpy()
        ax1.bar(x, vals, bottom=bottom, color=color, edgecolor="white", linewidth=0.6, label=ticket)
        bottom += vals

    # Shade weekends
    for i, d in enumerate(all_days):
        if d.weekday() >= 5:
            ax1.axvspan(i - 0.5, i + 0.5, color="#f4f4f4", zorder=0)

    # "Today" marker (end of window)
    if end_date in all_days:
        idx = all_days.index(end_date)
        ax1.axvline(idx, color="#d62728", linestyle="--", linewidth=1, alpha=0.7)

    # Reference line: average on active days only (zero days excluded)
    daily_totals = pivot.sum(axis=1).to_numpy() if not pivot.empty else np.array([0])
    active = daily_totals[daily_totals > 0]
    if len(active):
        avg = active.mean()
        ax1.axhline(avg, color="#555", linestyle=":", linewidth=1, alpha=0.7)
        ax1.text(
            len(all_days) - 0.5,
            avg,
            f"  avg active day: {avg:.1f}h",
            fontsize=8,
            color="#555",
            va="bottom",
            ha="right",
        )

    # X-axis: aim for ~25 visible labels max so long windows stay readable.
    # Always label the first tick, the last tick, and the 1st of any month.
    target_labels = 25
    step = max(1, span_days // target_labels)
    tick_labels = []
    for i, d in enumerate(all_days):
        if i == 0 or i == len(all_days) - 1 or d.day == 1:
            tick_labels.append(d.strftime("%-d %b"))
        elif i % step == 0:
            tick_labels.append(str(d.day))
        else:
            tick_labels.append("")
    ax1.set_xticks(x)
    ax1.set_xticklabels(
        tick_labels,
        fontsize=8,
        rotation=45 if span_days > 60 else 0,
        ha="right" if span_days > 60 else "center",
    )
    ax1.set_xlim(-0.6, len(all_days) - 0.4)
    ax1.set_ylabel("hours logged")
    title_range = f"{start_dt.strftime('%-d %b %Y %H:%M')} – {end_dt.strftime('%-d %b %Y %H:%M')}"
    ax1.set_title(
        f"Trackspace worklogs — {window_label}   ·   {title_range}   ·   {who}",
        loc="left",
    )
    ax1.grid(axis="y", linestyle="-", linewidth=0.5, alpha=0.3)
    ax1.set_axisbelow(True)

    # Compact legend (top N tickets)
    top_n = 8
    if len(pivot.columns):
        handles = [
            Patch(facecolor=colors[i], label=pivot.columns[i])
            for i in range(min(top_n, len(pivot.columns)))
        ]
        extra = len(pivot.columns) - top_n
        if extra > 0:
            handles.append(Patch(facecolor="#cccccc", label=f"+{extra} more"))
        ax1.legend(
            handles=handles,
            loc="upper left",
            bbox_to_anchor=(1.005, 1.0),
            frameon=False,
            fontsize=8,
            title="tickets",
            title_fontsize=8,
        )

    # --- Panel 2: top tickets bar (grouped by normalized title) -----------
    ax2 = fig.add_subplot(gs[1, :2])
    if df.empty:
        ax2.text(
            0.5,
            0.5,
            f"no worklogs found in the {window_label}",
            ha="center",
            va="center",
            color="#888",
        )
        ax2.set_axis_off()
    else:
        # Group by title with IPs collapsed, so the same alert type across
        # different source/dest IPs lands in a single bar.
        df_grouped = df.copy()
        df_grouped["title_key"] = df_grouped["summary"].apply(normalize_title)
        agg = (
            df_grouped.groupby("title_key")
            .agg(
                hours=("hours", "sum"),
                n_tickets=("ticket_id", "nunique"),
                sample_key=("ticket_id", "first"),
            )
            .sort_values("hours", ascending=True)
            .tail(10)
        )

        labels = []
        for title_key, row in agg.iterrows():
            display = str(title_key) if title_key else "(no title)"
            display = display[:50] + ("…" if len(display) > 50 else "")
            n = int(row["n_tickets"])
            display = f"{display}  ({n} tickets)" if n > 1 else f"{row['sample_key']}  ·  {display}"
            labels.append(display)

        ax2.barh(labels, agg["hours"].to_numpy(), color="#4c78a8", edgecolor="white")
        for i, v in enumerate(agg["hours"].to_numpy()):
            ax2.text(v, i, f"  {v:.1f}h", va="center", fontsize=8, color="#333")
        ax2.set_xlabel("hours")
        ax2.set_title(
            f"Top tickets — {window_label}   ·   grouped by title (IPs ignored)",
            loc="left",
        )
        ax2.grid(axis="x", linestyle="-", linewidth=0.5, alpha=0.3)
        ax2.set_axisbelow(True)

    # --- Panel 3: summary stats --------------------------------------------
    ax3 = fig.add_subplot(gs[1, 2])
    ax3.set_axis_off()

    total = float(df["hours"].sum()) if not df.empty else 0.0
    days_logged = int((pivot.sum(axis=1) > 0).sum()) if not pivot.empty else 0
    weekdays_in_window = sum(1 for d in all_days if d.weekday() < 5)
    avg_per_active = active.mean() if len(active) else 0
    busiest_idx = int(np.argmax(daily_totals)) if daily_totals.any() else None
    busiest = (
        f"{all_days[busiest_idx].strftime('%a %-d %b')} ({daily_totals[busiest_idx]:.1f}h)"
        if busiest_idx is not None and daily_totals[busiest_idx] > 0
        else "—"
    )

    lines = [
        ("Total logged", f"{total:.1f} h"),
        ("Days logged", f"{days_logged} / {weekdays_in_window} weekdays"),
        ("Avg active day", f"{avg_per_active:.1f} h"),
        ("Unique tickets", f"{df['ticket_id'].nunique() if not df.empty else 0}"),
        ("Busiest day", busiest),
    ]
    y = 0.95
    ax3.text(0.0, y, "Summary", fontsize=12, fontweight="bold", transform=ax3.transAxes)
    y -= 0.13
    for label, value in lines:
        ax3.text(0.0, y, label, fontsize=9, color="#666", transform=ax3.transAxes)
        ax3.text(1.0, y, value, fontsize=10, fontweight="bold", ha="right", transform=ax3.transAxes)
        y -= 0.13

    return fig
