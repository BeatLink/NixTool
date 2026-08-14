"""The command picker: a category sidebar beside a filterable command list.

Both panes are visible at once, so the categories read as a map of the tool
rather than a menu to step through. "All" keeps the flat list the filter box
searches across every category at once.
"""

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from . import registry

ALL_CATEGORIES = ""


def matches(query: str, haystack: str) -> bool:
    """Subsequence match, so "rgen" finds "Remove Old Generations"."""
    if not query:
        return True
    it = iter(haystack.lower())
    return all(character in it for character in query.lower())


class CommandBrowser(Widget):
    """Lists every runnable command, narrowed by a category and a query."""

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
    #browser-categories {
        width: 24;
        height: 100%;
        border: round $primary;
        background: transparent;
        padding: 0 1;
    }
    #browser-list {
        width: 1fr;
        height: 100%;
        border: round $primary;
        background: transparent;
        margin-left: 1;
        padding: 0 1;
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
        # (qualified_id, node, category_id) for every runnable command, built once.
        self._entries = [
            (registry.qualified_id(path, node), node, path[0] if path else "")
            for path, node in registry.iter_commands()
        ]
        # Only top-level categories get a sidebar row; deeper ones stay reachable
        # through their parent's row and the filter box.
        self._categories = [(ALL_CATEGORIES, "All")] + [
            (path[0], category["name"])
            for path, category in registry.iter_categories()
            if len(path) == 1
        ]
        self._category = ALL_CATEGORIES
        self._visible = list(self._entries)

    def compose(self) -> ComposeResult:
        with Vertical(id="browser-body"):
            yield Input(
                placeholder="Type to filter commands…", id="browser-filter"
            )
            with Horizontal(id="browser-panes"):
                yield OptionList(
                    *[
                        Option(escape(name), id=f"category:{identifier}")
                        for identifier, name in self._categories
                    ],
                    id="browser-categories",
                )
                yield OptionList(id="browser-list")
            yield Label(
                "enter run · ↑↓ move · tab categories · esc back",
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
            if (self._category in (ALL_CATEGORIES, entry[2]))
            # Category is searchable too, so "install" narrows to that group
            # without leaving the "All" row.
            and matches(query, f"{entry[1]['name']} {entry[0]} {entry[2]}")
        ]

        for qualified, node, _category in self._visible:
            listing.add_option(Option(escape(node["name"]), id=qualified))

        if self._visible:
            listing.highlighted = 0
        else:
            listing.add_option(Option(self._empty_message(query), disabled=True))

    def _empty_message(self, query: str) -> str:
        if query:
            return f"[dim]Nothing matches {escape(query)!r}.[/dim]"
        return "[dim]No commands in this category.[/dim]"

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

    @on(OptionList.OptionHighlighted, "#browser-categories")
    def _category_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        if event.option_index < len(self._categories):
            self._category = self._categories[event.option_index][0]
            self._repopulate()

    @on(OptionList.OptionSelected, "#browser-categories")
    def _category_selected(self, event: OptionList.OptionSelected) -> None:
        """Choosing a category hands typing back to the filter box."""
        event.stop()
        self.query_one("#browser-filter", Input).focus()

    @on(OptionList.OptionSelected, "#browser-list")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.post_message(self.Selected(str(event.option.id)))

    def on_key(self, event) -> None:
        """Let ↑/↓ drive the command list while the filter box keeps focus."""
        if not self.query_one("#browser-filter", Input).has_focus:
            return
        if event.key in ("up", "down", "pageup", "pagedown"):
            listing = self.query_one("#browser-list", OptionList)
            if self._visible:
                event.stop()
                event.prevent_default()
                listing.post_message(event)
