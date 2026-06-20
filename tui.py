"""Textual TUI for z.ai quota monitoring.

Live view of:
  - 5 Hours Quota  (percentage + countdown to reset)
  - Weekly Quota   (percentage + countdown to reset)
  - Web Search / Reader / Zread (used/total + countdown)
Plus sparkline of recent history and color-coded status.

Run: python tui.py
"""

from __future__ import annotations

from datetime import datetime, timezone

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, Header, Static

import config
import store
from fetcher import Quota, Snapshot, ZaiError, fetch_account, fetch_snapshot

ORDER = ["five_hour", "weekly", "mcp"]
PLAN_LABEL = {"max": "MAX", "pro": "PRO", "lite": "LITE"}

LEVEL_THRESHOLDS = [(50, "yellow"), (75, "darkorange"), (90, "red")]


def _color_for(pct: int) -> str:
    for lvl, color in LEVEL_THRESHOLDS:
        if pct >= lvl:
            return color
    return "green"


def _bar(pct: int, width: int = 30) -> str:
    pct = max(0, min(100, pct))
    filled = round(width * pct / 100)
    color = _color_for(pct)
    return f"[{color}]{'█' * filled}[/]{'░' * (width - filled)}"


def _countdown(next_reset: datetime | None) -> str:
    if not next_reset:
        return "—"
    now = datetime.now(tz=timezone.utc)
    delta = next_reset - now
    if delta.total_seconds() <= 0:
        return "resetting…"
    secs = int(delta.total_seconds())
    d, secs = divmod(secs, 86400)
    h, secs = divmod(secs, 3600)
    m, s = divmod(secs, 60)
    if d > 0:
        return f"resets in {d}d {h}h {m}m"
    return f"resets in {h}h {m}m {s}s"


def _reset_local(next_reset: datetime | None) -> str:
    if not next_reset:
        return "—"
    return next_reset.astimezone().strftime("%a %b %d %H:%M")


def _sparkline(samples: list[tuple[float, int]], width: int = 40) -> str:
    if len(samples) < 2:
        return ""
    vals = [v for _, v in samples]
    span = max(vals) - min(vals) or 1
    blocks = "▁▂▃▄▅▆▇█"
    out = []
    step = max(1, len(vals) // width)
    sampled = vals[::step][:width]
    for v in sampled:
        norm = (v - min(vals)) / span
        out.append(blocks[max(0, min(len(blocks) - 1, int(norm * (len(blocks) - 1))))])
    return "".join(out)


class QuotaBlock(Static):
    """A single quota panel."""

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key
        self.quota: Quota | None = None

    def _markup(self) -> str:
        q = self.quota
        if not q:
            return f"[bold]{self.key}[/]\n  loading…"
        pct = q.percentage
        lines = [f"[bold]{q.title}[/]"]
        if q.has_counts and q.used is not None and q.usage is not None:
            lines.append(f"  {_bar(pct)}  {pct}%   [{q.used}/{q.usage} {q.unit_text}]")
        else:
            lines.append(f"  {_bar(pct)}  {pct}%")
        lines.append(f"  [dim]{_countdown(q.next_reset)}[/]")
        lines.append(f"  [dim]at {_reset_local(q.next_reset)}[/]")
        hist = store.history(self.key, limit=60)
        spark = _sparkline(hist)
        if spark:
            lines.append(f"  [dim]{spark}[/]")
        return "\n".join(lines)

    def render(self) -> str:
        return self._markup()

    def set_quota(self, q: Quota | None) -> None:
        self.quota = q
        self.refresh(layout=True)


class ZaiMonitorApp(App):
    """Z.ai Coding Plan quota monitor."""

    CSS = """
    Screen { background: $surface; }
    #main { padding: 1 2; }
    .panel { border: round $primary; padding: 0 1; margin: 0 0 1 0; height: auto; }
    Label { margin: 0 0 1 0; }
    #status { color: $text-muted; }
    """

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("t", "toggle_theme", "Theme"),
        ("q", "quit", "Quit"),
    ]
    refresh_in = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self.snapshot: Snapshot | None = None
        self.email: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll(id="main"):
            yield Static("", id="title", classes="panel")
            for key in ORDER:
                yield QuotaBlock(key)
            yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        store.init()
        self.title = "z.ai monitor"
        self.sub_title = "GLM Coding Plan"
        saved_theme = store.get_meta("theme", "")
        if saved_theme:
            try:
                self.theme = saved_theme
            except Exception:
                pass
        self._do_refresh()
        self.set_interval(1, self._tick)

    def watch_theme(self, theme: str) -> None:
        """Persist the chosen theme across restarts."""
        try:
            store.set_meta("theme", theme)
        except Exception:
            pass

    def _tick(self) -> None:
        self.refresh_in -= 1
        if self.refresh_in <= 0:
            self.refresh_in = config.tui_refresh_interval()
            self._do_refresh()
        self._set_status(f"next refresh in {self.refresh_in}s")

    def action_refresh(self) -> None:
        self._do_refresh()

    def action_toggle_theme(self) -> None:
        self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"

    @work(exclusive=True)
    async def _do_refresh(self) -> None:
        import asyncio

        try:
            snap = await asyncio.to_thread(fetch_snapshot)
        except ZaiError as e:
            self._set_status(f"[red]error: {e}[/]")
            return
        if not self.email:
            try:
                acc = await asyncio.to_thread(fetch_account)
                self.email = acc.get("email")
            except Exception:
                self.email = None

        self.snapshot = snap
        for block in self.query(QuotaBlock):
            q = snap.get(block.key)
            if q:
                store.record_history(
                    block.key,
                    q.percentage,
                    q.next_reset.timestamp() if q.next_reset else None,
                )
            block.set_quota(q)

        level = PLAN_LABEL.get((snap.level or "").lower(), (snap.level or "?").upper())
        fetched = snap.fetched_at.astimezone().strftime("%H:%M:%S")
        header = f"[bold]z.ai GLM Coding Plan[/] — [cyan]{level}[/]"
        if self.email:
            header += f"   {self.email}"
        acct = config.account_name()
        if acct:
            header += f"\n[magenta]↳ {acct}[/]"
        header += f"\n[dim]last updated {fetched}[/]"
        try:
            self.query_one("#title", Static).update(header)
        except Exception:
            pass
        self._set_status(f"updated {fetched} — next refresh in {self.refresh_in}s")

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#status", Static).update(msg)
        except Exception:
            pass


if __name__ == "__main__":
    ZaiMonitorApp().run()
