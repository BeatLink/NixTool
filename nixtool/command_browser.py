"""The command picker: one flat, filterable list plus a detail pane.

Replaces the two-level category menu. With a dozen commands the hierarchy cost
two keystrokes and hid the things that actually matter at the moment of choosing
— what a command does, whether it is destructive, and what it will ask for.
Everything the CLI's ``list``/``show`` output carries is visible here.
"""

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import registry, secrets


def matches(query: str, haystack: str) -> bool:
    """Subsequence match, so "rgen" finds "Remove Old Generations"."""
    if not query:
        return True
    it = iter(haystack.lower())
    return all(character in it for character in query.lower())


class CommandBrowser(Widget):
    """Lists every runnable command, filtered by a query, with a detail pane."""

    DEFAULT_CSS = """
    CommandBrowser {
        width: 100%;
        height: 100%;
    }
    #browser-body {
        width: 100%;
        height: 100%;
        padding: 1 2;
    }
    #browser-filter {
        width: 100%;
        border: round $primary;
        background: transparent;
    }
    #browser-panes {
        width: 100%;
        height: 1fr;
    }
    #browser-list {
        width: 3fr;
        height: 100%;
        border: round $primary;
        background: transparent;
        padding: 0 1;
    }
    #browser-detail {
        width: 2fr;
        height: 100%;
        border: round $primary;
        margin-left: 1;
        padding: 0 1;
    }
    #detail-name {
        width: 100%;
        height: auto;
        color: $primary;
        text-style: bold;
    }
    #browser-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }
    """

    can_focus = True

    class Selected(Message):
        """A command was chosen; ``command_id`` is its qualified registry id."""

        def __init__(self, command_id: str) -> None:
            super().__init__()
            self.command_id = command_id

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # (qualified_id, node, category) for every runnable command, built once.
        self._entries = [
            (registry.qualified_id(path, node), node, "/".join(path))
            for path, node in registry.iter_commands()
        ]
        self._visible = list(self._entries)

    def compose(self) -> ComposeResult:
        with Vertical(id="browser-body"):
            yield Input(
                placeholder="Type to filter commands…", id="browser-filter"
            )
            with Horizontal(id="browser-panes"):
                yield OptionList(id="browser-list")
                with VerticalScroll(id="browser-detail"):
                    yield Label(id="detail-name")
                    yield Static(id="detail-text")
            yield Label(
                "enter run · ↑↓ move · esc back",
                id="browser-hint",
            )

    def on_mount(self) -> None:
        self._repopulate()

    def on_focus(self) -> None:
        self.query_one("#browser-filter", Input).focus()

    # --- filtering --------------------------------------------------------

    def _repopulate(self) -> None:
        query = self.query_one("#browser-filter", Input).value.strip()
        listing = self.query_one("#browser-list", OptionList)
        listing.clear_options()

        self._visible = [
            entry for entry in self._entries
            # Category is searchable too, so "install" narrows to that group.
            if matches(query, f"{entry[1]['name']} {entry[0]} {entry[2]}")
        ]

        for qualified, node, _category in self._visible:
            listing.add_option(Option(self._row(node), id=qualified))

        if self._visible:
            listing.highlighted = 0
            self._show_detail(self._visible[0][1], self._visible[0][2])
        else:
            self._show_empty(query)

    def _row(self, node: dict) -> str:
        """One list row: name plus compact destructive/host markers."""
        marks = []
        if registry.is_destructive(node):
            marks.append("[$error]⚠[/$error]")
        if registry.needs_host(node):
            marks.append("[$accent]⌂[/$accent]")
        if node.get("interactive"):
            marks.append("[$accent]▸[/$accent]")
        suffix = ("  " + " ".join(marks)) if marks else ""
        return f"{escape(node['name'])}{suffix}"

    # --- detail pane ------------------------------------------------------

    def _show_detail(self, node: dict, category: str) -> None:
        self.query_one("#detail-name", Label).update(escape(node["name"]))

        lines = []
        badges = []
        if registry.is_destructive(node):
            badges.append("[$error]⚠ DESTRUCTIVE[/$error]")
        if registry.needs_host(node):
            badges.append("[$accent]⌂ needs host[/$accent]")
        if node.get("interactive"):
            badges.append("[$accent]▸ interactive[/$accent]")
        if badges:
            lines.append(" · ".join(badges))
            lines.append("")

        if node.get("description"):
            lines.append(escape(node["description"]))
            lines.append("")

        variables = registry.collect_variables(node)
        steps = len(node.get("commands", []))
        facts = [f"{steps} step(s)" if steps else "external tool"]
        facts.append(f"{len(variables)} variable(s)" if variables else "no variables")
        if category:
            facts.append(escape(category))
        lines.append("[dim]" + "  ·  ".join(facts) + "[/dim]")

        if variables:
            lines.append("")
            lines.append("[bold]Will ask for[/bold]")
            for name, spec in sorted(variables.items()):
                kind = spec.get("type", "list")
                if kind == "uuid":
                    detail = "generated"
                elif secrets.is_secret(spec):
                    detail = f"{kind}, hidden"
                else:
                    detail = kind
                lines.append(f"  {escape(name)}  [dim]({detail})[/dim]")

        if node.get("instructions"):
            lines.append("")
            lines.append("[dim]Shows a warning before running.[/dim]")

        self.query_one("#detail-text", Static).update("\n".join(lines))

    def _show_empty(self, query: str) -> None:
        self.query_one("#detail-name", Label).update("No matches")
        self.query_one("#detail-text", Static).update(
            f"[dim]Nothing matches {escape(query)!r}.[/dim]"
        )

    # --- events -----------------------------------------------------------

    @on(Input.Changed, "#browser-filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._repopulate()

    @on(Input.Submitted, "#browser-filter")
    def _filter_submitted(self, event: Input.Submitted) -> None:
        """Enter from the filter runs the highlighted command."""
        event.stop()
        listing = self.query_one("#browser-list", OptionList)
        if listing.highlighted is not None and self._visible:
            self.post_message(
                self.Selected(self._visible[listing.highlighted][0])
            )

    @on(OptionList.OptionHighlighted, "#browser-list")
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        if event.option_index < len(self._visible):
            _, node, category = self._visible[event.option_index]
            self._show_detail(node, category)

    @on(OptionList.OptionSelected, "#browser-list")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.post_message(self.Selected(str(event.option.id)))

    def on_key(self, event) -> None:
        """Let ↑/↓ drive the list while the filter box keeps keyboard focus."""
        if event.key in ("up", "down", "pageup", "pagedown"):
            listing = self.query_one("#browser-list", OptionList)
            if self._visible:
                event.stop()
                event.prevent_default()
                listing.post_message(event)
