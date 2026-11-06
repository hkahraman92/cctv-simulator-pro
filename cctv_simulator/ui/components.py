"""Design-system widgets for Tk: command bar pieces, KPI tile, alarm list.

Plain ``tk`` widgets (not ttk) so each surface takes its exact token colour;
every colour comes from :mod:`design_tokens`. Status is always symbol + word
+ colour (``status_summary.SYMBOL`` / ``WORD``), never colour alone.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, List, Optional, Sequence

from ..design_tokens import tok
from ..status_summary import SYMBOL, WORD, Alarm, Kpi
from ..theme import FONT_DISPLAY, FONT_MONO, FONT_UI

_STATE_FG = {"ok": "ok", "warn": "warn", "danger": "danger", "info": "info", "neutral": "ink"}
_STATE_EDGE = {"ok": "ok", "warn": "warn", "danger": "danger", "info": "info", "neutral": "line-strong"}


def panel_label(parent, text: str, bg: str = "bg-1") -> tk.Label:
    """Upper-case panel caption (``panel-label``: display face, ink-3)."""
    return tk.Label(parent, text=text.upper(), bg=tok(bg), fg=tok("ink-3"),
                    font=(FONT_DISPLAY, 9), anchor="w")


def _fit_wrap(label: tk.Label, pad: int = 2) -> None:
    def _fit(e):
        wl = max(40, e.width - pad)
        if int(str(label.cget("wraplength")) or 0) != wl:
            label.configure(wraplength=wl)
    label.configure(wraplength=120)
    label.bind("<Configure>", _fit, add="+")


class StatusChip(tk.Frame):
    """``● Kamera kütüphanesi · 36 model``: the symbol carries the state
    colour, the text stays ink-2 so a column of chips does not shout."""

    def __init__(self, parent, bg: str = "bg-2", **_kw):
        super().__init__(parent, bg=tok(bg))
        self.sym = tk.Label(self, bg=tok(bg), font=(FONT_UI, 9), width=2, anchor="w")
        self.sym.pack(side=tk.LEFT)
        self.text = tk.Label(self, bg=tok(bg), fg=tok("ink-2"), font=(FONT_UI, 9),
                             anchor="w", justify="left")
        self.text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _fit_wrap(self.text)

    def set(self, state: str, text: str) -> None:
        self.sym.configure(text=SYMBOL.get(state, ""), fg=tok(_STATE_FG.get(state, "ink")))
        self.text.configure(text=text)


class KpiTile(tk.Frame):
    """Caption, big mono value + unit, note. Edge and value carry the state."""

    def __init__(self, parent, label: str):
        super().__init__(parent, bg=tok("bg-2"), highlightthickness=0)
        self.edge = tk.Frame(self, bg=tok("line-strong"), width=3)
        self.edge.pack(side=tk.LEFT, fill=tk.Y)
        body = tk.Frame(self, bg=tok("bg-2"))
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(9, 8), pady=(7, 8))
        self.caption = tk.Label(body, text=label, bg=tok("bg-2"), fg=tok("ink-3"),
                                font=(FONT_DISPLAY, 8), anchor="w")
        self.caption.pack(fill=tk.X)
        row = tk.Frame(body, bg=tok("bg-2"))
        row.pack(fill=tk.X, pady=(1, 0))
        self.value = tk.Label(row, text="—", bg=tok("bg-2"), fg=tok("ink"),
                              font=(FONT_MONO, 17), anchor="w")
        self.value.pack(side=tk.LEFT)
        self.unit = tk.Label(row, text="", bg=tok("bg-2"), fg=tok("ink-3"),
                             font=(FONT_UI, 9), anchor="sw")
        self.unit.pack(side=tk.LEFT, padx=(4, 0), pady=(0, 3), anchor="s")
        self.note = tk.Label(body, text="", bg=tok("bg-2"), fg=tok("ink-3"),
                             font=(FONT_UI, 8), anchor="w", justify="left")
        self.note.pack(fill=tk.X)
        _fit_wrap(self.note)

    def set(self, kpi: Kpi) -> None:
        self.caption.configure(text=kpi.label)
        sym = SYMBOL.get(kpi.state, "") if kpi.state in ("warn", "danger") else ""
        self.value.configure(text=f"{sym} {kpi.value}".strip() if sym else kpi.value,
                             fg=tok(_STATE_FG.get(kpi.state, "ink")))
        self.unit.configure(text=kpi.unit)
        self.note.configure(text=kpi.note)
        self.edge.configure(bg=tok(_STATE_EDGE.get(kpi.state, "line-strong")))


class AlarmList(tk.Frame):
    """Rows of symbol + title + detail, most severe first. Click selects."""

    MAX_ROWS = 40

    def __init__(self, parent, on_select: Optional[Callable[[Alarm], None]] = None):
        super().__init__(parent, bg=tok("bg-1"))
        self.on_select = on_select
        self._rows: List[tk.Frame] = []

    def set(self, alarms: Sequence[Alarm], empty_text: str = "Açık alarm yok") -> None:
        for row in self._rows:
            row.destroy()
        self._rows = []
        if not alarms:
            self._add_row(None, empty_text)
            return
        for alarm in list(alarms)[: self.MAX_ROWS]:
            self._add_row(alarm)
        if len(alarms) > self.MAX_ROWS:
            self._add_row(None, f"… ve {len(alarms) - self.MAX_ROWS} alarm daha")

    def _add_row(self, alarm: Optional[Alarm], text: str = "") -> None:
        bg = tok("bg-1")
        row = tk.Frame(self, bg=bg, cursor="hand2" if alarm and self.on_select else "")
        row.pack(fill=tk.X, pady=(0, 1))
        self._rows.append(row)
        tk.Frame(row, bg=tok("line"), height=1).pack(side=tk.BOTTOM, fill=tk.X)
        state = alarm.severity if alarm else "ok"
        sym = tk.Label(row, text=SYMBOL[state], bg=bg, fg=tok(_STATE_FG[state]),
                       font=(FONT_UI, 10), width=2, anchor="n")
        sym.pack(side=tk.LEFT, anchor="n", padx=(2, 4), pady=(5, 0))
        body = tk.Frame(row, bg=bg)
        body.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=(4, 5))
        title = tk.Label(body, text=alarm.title if alarm else text, bg=bg, fg=tok("ink"),
                         font=(FONT_UI, 9, "bold"), anchor="w", justify="left")
        title.pack(fill=tk.X)
        _fit_wrap(title)
        parts = [row, sym, body, title]
        if alarm is not None:
            detail = tk.Label(body, text=f"{WORD[state]} · {alarm.detail}" if alarm.detail else WORD[state],
                              bg=bg, fg=tok("ink-3"), font=(FONT_UI, 8), anchor="w", justify="left")
            detail.pack(fill=tk.X)
            _fit_wrap(detail)
            parts.append(detail)
            if self.on_select:
                def _enter(_e, ws=parts):
                    for w in ws:
                        w.configure(bg=tok("bg-3"))

                def _leave(_e, ws=parts):
                    for w in ws:
                        w.configure(bg=tok("bg-1"))
                for w in parts:
                    w.bind("<Enter>", _enter)
                    w.bind("<Leave>", _leave)
                    w.bind("<Button-1>", lambda _e, a=alarm: self.on_select(a))


class BarButton(tk.Label):
    """Flat command-bar button on the brand strip (hover = lighter strip)."""

    def __init__(self, parent, text: str, command: Callable[[], None], primary: bool = False):
        self._normal = tok("accent") if primary else tok("brand")
        self._hover = tok("accent-hi") if primary else tok("brand-hi")
        fg = tok("on-accent") if primary else tok("on-brand")
        super().__init__(parent, text=text, bg=self._normal, fg=fg, cursor="hand2",
                         font=(FONT_UI, 10, "bold" if primary else "normal"), padx=12, pady=6)
        self.bind("<Enter>", lambda _e: self.configure(bg=self._hover))
        self.bind("<Leave>", lambda _e: self.configure(bg=self._normal))
        self.bind("<Button-1>", lambda _e: command())
