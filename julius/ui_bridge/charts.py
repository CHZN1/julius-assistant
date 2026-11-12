"""`render_chart`: data in, picture out. The model supplies DATA (kind, title, labels, series; or table columns and rows), never code: this module validates and
bounds everything, draws with matplotlib (Agg, no figure ever reaches a window or a file outside the panel store), re-encodes the PNG through the same Pillow
pipeline as every panel image, and hands the card to the panel. A table needs no drawing: it is sent as data and the UI lays it out as text.

Kinds: bar, hbar, line, pie, table. Limits (all enforced here): 30 labels, 5 series, 12 pie slices, 8 table columns, 50 table rows, 80 characters a cell."""

import io
import math
import threading
from collections.abc import Callable
from typing import Any

from julius import log
from julius.ui_bridge import panel as pn
from julius.ui_bridge import panelfiles as pf

KINDS = ("bar", "hbar", "line", "pie", "table")
MAX_LABELS, MAX_SERIES, MAX_SLICES, MAX_COLS, MAX_ROWS, MAX_CELL = 30, 5, 12, 8, 50, 80
# the orb's opal palette: blue, violet, mint, pink, amber, green
PALETTE = ["#9ec5ff", "#c4b0ff", "#96e6d7", "#ffb3d1", "#ffd58a", "#a7f3a0"]
INK, MUTED, GRID = "#d4d6da", "#8b8e96", "#2a2c32"


class ChartProblem(ValueError):
    """A spec that cannot be drawn; the message is what Julius says."""


def _text(v: Any, cap: int, what: str) -> str:
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise ChartProblem(f"{what} must be text.")
    out = pn.clean(str(v), cap)
    if not out:
        raise ChartProblem(f"{what} can't be empty.")
    return out


def _num(v: Any, what: str) -> float:
    if (
        isinstance(v, bool)
        or not isinstance(v, (int, float))
        or not math.isfinite(v)
        or abs(v) > 1e15
    ):
        raise ChartProblem(f"{what} must be a plain number.")
    return float(v)


def clean_spec(args: dict) -> dict:
    """Validate a render_chart call. -> {"kind", "title", "unit", ...} with every string cleaned and every list bounded; raises ChartProblem."""
    if not isinstance(args, dict):
        raise ChartProblem("The chart needs a kind, a title and data.")
    kind = str(args.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise ChartProblem(f"Chart kind must be one of {', '.join(KINDS)}.")
    title = _text(args.get("title") or "Chart", pn.CAPS["title"], "The title")
    unit = pn.clean(str(args.get("unit") or ""), 20)
    if kind == "table":
        cols, rows = args.get("columns"), args.get("rows")
        if not isinstance(cols, list) or not 1 <= len(cols) <= MAX_COLS:
            raise ChartProblem(f"A table needs 1 to {MAX_COLS} columns.")
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
            raise ChartProblem(f"A table needs 1 to {MAX_ROWS} rows.")
        columns = [_text(c, 60, "A column name") for c in cols]
        body = []
        for r in rows:
            if not isinstance(r, list) or len(r) != len(columns):
                raise ChartProblem("Every table row needs one cell per column.")
            body.append([pn.clean("" if c is None else str(c), MAX_CELL) for c in r])
        return {"kind": kind, "title": title, "unit": unit, "columns": columns, "rows": body}
    labels, series = args.get("labels"), args.get("series")
    if not isinstance(labels, list) or not 1 <= len(labels) <= MAX_LABELS:
        raise ChartProblem(f"A chart needs 1 to {MAX_LABELS} labels.")
    if not isinstance(series, list) or not 1 <= len(series) <= MAX_SERIES:
        raise ChartProblem(f"A chart needs 1 to {MAX_SERIES} series.")
    labs = [_text(x, 40, "A label") for x in labels]
    out = []
    for i, s in enumerate(series, 1):
        if not isinstance(s, dict) or not isinstance(s.get("values"), list):
            raise ChartProblem("Each series needs a name and a list of values.")
        vals = [_num(v, "A value") for v in s["values"]]
        if len(vals) != len(labs):
            raise ChartProblem("Every series needs one value per label.")
        out.append(
            {"name": _text(s.get("name") or f"Series {i}", 40, "A series name"), "values": vals}
        )
    if kind == "pie":
        if len(out) != 1 or not 2 <= len(labs) <= MAX_SLICES:
            raise ChartProblem(f"A pie chart takes one series and 2 to {MAX_SLICES} slices.")
        if any(v < 0 for v in out[0]["values"]) or sum(out[0]["values"]) <= 0:
            raise ChartProblem(
                "A pie chart needs values that are zero or more and add up to more than zero."
            )
    return {"kind": kind, "title": title, "unit": unit, "labels": labs, "series": out}


def draw_png(spec: dict) -> bytes:
    """The chart as PNG bytes (transparent background, the app's colours). Needs matplotlib; raises ChartProblem when it is not installed."""
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        from matplotlib.figure import Figure
    except ImportError as e:
        raise ChartProblem("Charts need the matplotlib package, which isn't installed.") from e
    fig = Figure(figsize=(5.6, 3.5), dpi=200)
    fig.patch.set_alpha(0)
    ax = fig.add_subplot(111)
    ax.set_facecolor("none")
    kind, labels, series, unit = spec["kind"], spec["labels"], spec["series"], spec.get("unit", "")
    n = len(labels)
    if kind == "pie":
        vals = series[0]["values"]
        wedges, _ = ax.pie(
            vals,
            colors=[PALETTE[i % len(PALETTE)] for i in range(n)],
            startangle=90,
            counterclock=False,
            wedgeprops={"linewidth": 1.5, "edgecolor": "#0e0f12"},
        )
        total = sum(vals)
        ax.legend(
            wedges,
            [f"{lb}  {v / total:.0%}" for lb, v in zip(labels, vals, strict=True)],
            loc="center left",
            bbox_to_anchor=(1.0, 0.5),
            frameon=False,
            labelcolor=INK,
            fontsize=8,
        )
        ax.set_aspect("equal")
    else:
        horizontal = kind == "hbar"
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(GRID)
        ax.tick_params(colors=MUTED, labelsize=8, length=0)
        k = len(series)
        for i, s in enumerate(series):
            color = PALETTE[i % len(PALETTE)]
            if kind == "line":
                ax.plot(
                    range(n),
                    s["values"],
                    color=color,
                    linewidth=2.2,
                    marker="o",
                    markersize=4,
                    label=s["name"],
                )
            elif horizontal:
                w = 0.8 / k
                ax.barh(
                    [j + i * w - 0.4 + w / 2 for j in range(n)],
                    s["values"],
                    height=w * 0.92,
                    color=color,
                    label=s["name"],
                )
            else:
                w = 0.8 / k
                ax.bar(
                    [j + i * w - 0.4 + w / 2 for j in range(n)],
                    s["values"],
                    width=w * 0.92,
                    color=color,
                    label=s["name"],
                )
        ticks = list(range(n))
        shown = [lb if len(lb) <= 14 else lb[:13] + "…" for lb in labels]
        if horizontal:
            ax.set_yticks(ticks, shown)
            ax.invert_yaxis()
            ax.grid(axis="x", color=GRID, linewidth=0.6)
            if unit:
                ax.set_xlabel(unit, color=MUTED, fontsize=8)
        else:
            ax.set_xticks(
                ticks, shown, rotation=30 if n > 6 else 0, ha="right" if n > 6 else "center"
            )
            ax.grid(axis="y", color=GRID, linewidth=0.6)
            if unit:
                ax.set_ylabel(unit, color=MUTED, fontsize=8)
        ax.set_axisbelow(True)
        if k > 1:
            ax.legend(frameon=False, labelcolor=INK, fontsize=8, loc="best")
    fig.tight_layout(pad=0.6)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    return buf.getvalue()


def chart_alt(spec: dict) -> str:
    kind = {"hbar": "bar"}.get(spec["kind"], spec["kind"])
    return pn.clean(f"{kind.capitalize()} chart: {spec['title']}", pn.CAPS["alt"])


# ---- wiring ---------------------------------------------------------------------------------------------
_panel: tuple[Any, Any, Callable[[], int]] | None = None


def set_panel(panel: Any, files: Any, turn_fn: Callable[[], int] = lambda: 0) -> None:
    """The service registers the panel hub and its image store here (None, None to switch it off)."""
    global _panel
    _panel = (panel, files, turn_fn) if panel is not None and files is not None else None


def render_chart(args: dict) -> str:
    """The `render_chart` tool body. Returns what Julius is told (the model then speaks); the card goes to the panel."""
    if _panel is None:
        return "The side panel isn't available, so I can't show a chart."
    panel, files, turn_fn = _panel
    try:
        spec = clean_spec(args)
        if spec["kind"] == "table":
            pid = panel.table(spec, turn=turn_fn())
            what = f"table with {len(spec['rows'])} rows"
        else:
            data, ext = pf.ingest_image(draw_png(spec))
            card = {**spec, "image": files.put(data, ext), "alt": chart_alt(spec)}
            pid = panel.chart(card, turn=turn_fn())
            what = f"{spec['kind']} chart"
    except ChartProblem as e:
        return f"I couldn't make that chart: {e}"
    except Exception as e:  # drawing or storing failed: say so, never raise into the turn
        log.event("panel_chart_failed", error=type(e).__name__)
        return "I couldn't draw that chart."
    if pid is None:
        return "I couldn't show that chart."
    return f"Showing a {what} titled '{spec['title']}' in the side panel. Describe what it shows in a sentence; don't read the numbers out."


def render_in_thread(
    args: dict,
) -> str:  # pragma: no cover - drawing is quick; kept for callers that must not block
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(render_chart(args)))
    t.start()
    t.join(15)
    return out[0] if out else "Drawing the chart took too long."
