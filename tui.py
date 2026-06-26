"""Textual TUI for z.ai quota monitoring.

Live view of:
  - 5 Hours Quota  (percentage + countdown to reset)
  - Weekly Quota   (percentage + countdown to reset)
  - Web Search / Reader / Zread (used/total + countdown)
Plus sparkline of recent history and color-coded status.

Run: python3 tui.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Header, Static

import config
import store
from fetcher import Quota, Snapshot, ZaiError, fetch_account, fetch_snapshot

log = logging.getLogger("zai.tui")

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
    """A single quota panel for a given account."""

    def __init__(self, account: str, key: str) -> None:
        super().__init__()
        self.account = account
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
        hist = store.history(self.account, self.key, limit=60)
        spark = _sparkline(hist)
        if spark:
            lines.append(f"  [dim]{spark}[/]")
        return "\n".join(lines)

    def render(self) -> str:
        return self._markup()

    def set_quota(self, q: Quota | None) -> None:
        self.quota = q
        self.refresh(layout=True)


# Bindings shown in the bottom status bar (key, label).
FOOTER_BINDINGS = [("r", "Refresh"), ("t", "Theme"), ("m", "MCP"), ("p", "Palette"), ("q", "Quit")]


class StatusBar(Static):
    """Bottom bar: account/update status (left) + key hints (right)."""

    status = reactive("")

    def render(self) -> str:
        keys = "   ".join(
            f"[bold cyan] {k} [/][dim]{desc}[/]" for k, desc in FOOTER_BINDINGS
        )
        # right-align the keys: pad between status (left) and keys (right)
        from rich.text import Text

        width = self.content_size.width or 80
        status_len = len(Text.from_markup(self.status).plain)
        keys_len = len(Text.from_markup(keys).plain)
        pad = width - status_len - keys_len
        return f"{self.status}{' ' * max(1, pad)}{keys}"


class ZaiMonitorApp(App):
    """Z.ai Coding Plan quota monitor (multi-account)."""

    CSS = """
    Screen { background: $surface; }
    #main { padding: 1 2; }
    .panel { border: round $primary; padding: 0 1; margin: 0 0 1 0; height: auto; }
    .mcp-block { height: auto; }
    .mcp-block.hidden { display: none; }
    StatusBar { dock: bottom; height: 1; padding: 0 1; background: $boost; }
    """

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("t", "toggle_theme", "Theme"),
        ("m", "toggle_mcp", "MCP"),
        ("p", "palette", "Palette"),
        ("q", "quit", "Quit"),
    ]
    refresh_in = reactive(0)
    show_mcp = reactive(True)

    def __init__(self) -> None:
        super().__init__()
        self.accounts = config.load_accounts()
        # cache email per account (best-effort, fetched once)
        self.emails: dict[str, str | None] = {a.name: None for a in self.accounts}
        self.last_updated: str = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll(id="main"):
            for idx, acct in enumerate(self.accounts):
                label = acct.name or f"account {idx + 1}"
                yield Static("", id=f"acct-{idx}", classes="panel")
                for key in ORDER:
                    block = QuotaBlock(label, key)
                    if key == "mcp":
                        block.add_class("mcp-block")
                    yield block
        yield StatusBar(id="statusbar")

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
        # show_mcp: prefer saved preference, fall back to config.toml default
        saved_mcp = store.get_meta("show_mcp", "")
        if saved_mcp:
            self.show_mcp = saved_mcp == "1"
        else:
            self.show_mcp = config.show_mcp_default()
        self._apply_mcp_visibility()
        self._do_refresh()
        self.set_interval(1, self._tick)

    def watch_theme(self, theme: str) -> None:
        """Persist the chosen theme across restarts."""
        try:
            store.set_meta("theme", theme)
        except Exception:
            pass

    def watch_show_mcp(self, value: bool) -> None:
        """Persist MCP panel visibility across restarts."""
        try:
            store.set_meta("show_mcp", "1" if value else "0")
        except Exception:
            pass
        self._apply_mcp_visibility()

    def _apply_mcp_visibility(self) -> None:
        for block in self.query(".mcp-block"):
            block.set_class(not self.show_mcp, "hidden")

    def action_toggle_mcp(self) -> None:
        self.show_mcp = not self.show_mcp

    def _tick(self) -> None:
        self.refresh_in -= 1
        if self.refresh_in <= 0:
            self.refresh_in = config.tui_refresh_interval()
            self._do_refresh()
        self.update_status()

    def action_refresh(self) -> None:
        self._do_refresh()

    def action_toggle_theme(self) -> None:
        self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"

    @work(exclusive=True)
    async def _do_refresh(self) -> None:
        import asyncio

        async def fetch_one(acct: config.Account) -> Snapshot | None:
            try:
                return await asyncio.to_thread(fetch_snapshot, api_key=acct.api_key)
            except ZaiError as e:
                log.warning("[%s] fetch failed: %s", acct.name or "account", e)
                return None

        snaps = await asyncio.gather(*[fetch_one(a) for a in self.accounts])

        # fetch emails lazily (once) for accounts that don't have one cached
        for acct, snap in zip(self.accounts, snaps, strict=True):
            label = acct.name or "account"
            if snap and self.emails.get(acct.name) is None:
                try:
                    acc_info = await asyncio.to_thread(fetch_account, api_key=acct.api_key)
                    self.emails[acct.name] = acc_info.get("email")
                except Exception:
                    self.emails[acct.name] = None

        # update each account's header + its quota blocks
        for idx, (acct, snap) in enumerate(zip(self.accounts, snaps, strict=True)):
            label = acct.name or f"account {idx + 1}"
            level = "?"
            if snap:
                level = PLAN_LABEL.get((snap.level or "").lower(), (snap.level or "?").upper())
            email = self.emails.get(acct.name)
            header = f"[bold]{label}[/]"
            if email:
                header += f"   [dim]{email}[/]"
            if snap:
                header += f"   [cyan]{level}[/]"
            else:
                header += "   [red](fetch failed)[/]"
            try:
                self.query_one(f"#acct-{idx}", Static).update(header)
            except Exception:
                pass

        # update all quota blocks with their snapshot (or None on failure)
        for block in self.query(QuotaBlock):
            # find the snapshot for this block's account
            snap = None
            for acct, s in zip(self.accounts, snaps, strict=True):
                if (acct.name or "account") == block.account:
                    snap = s
                    break
            q = snap.get(block.key) if snap else None
            if q:
                store.record_history(
                    block.account,
                    block.key,
                    q.percentage,
                    q.next_reset.timestamp() if q.next_reset else None,
                )
            block.set_quota(q)

        fetched = datetime.now().astimezone().strftime("%H:%M:%S")
        self.last_updated = fetched
        self.update_status()

    def update_status(self) -> None:
        """Render the bottom status bar: accounts · updated (left), keys (right)."""
        n = len(self.accounts)
        updated = self.last_updated or "—"
        msg = f"[dim]{n} account(s) · updated {updated}[/]"
        try:
            self.query_one("#statusbar", StatusBar).status = msg
        except Exception:
            pass


if __name__ == "__main__":
    ZaiMonitorApp().run()
