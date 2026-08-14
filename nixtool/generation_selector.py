"""The generation picker: what exists on the host, with what to delete ticked.

Listing generations is read-only, so it belongs on a screen rather than in the
runner's log: the point of running it is to look at the result and decide, and
a parsed table is easier to decide from than the raw output of two nix-env
invocations. Only the deletion goes to the runner, where a live log, a progress
bar and a cancel button are worth having.
"""

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Label, SelectionList
from textual.widgets.selection_list import Selection

from . import generations as gen
from .host_selector import ALL_HOSTS


class GenerationSelector(Widget):
    """Lists a host's generations and collects the ones to remove."""

    DEFAULT_CSS = """
    GenerationSelector {
        width: 100%;
        height: 100%;
    }
    #gen-body {
        width: 100%;
        height: 100%;
        padding: 1 2;
    }
    #gen-title {
        width: 100%;
        height: auto;
        text-align: center;
        color: $primary;
        text-style: bold;
    }
    #gen-panes {
        width: 100%;
        height: 1fr;
    }
    .gen-pane {
        width: 1fr;
        height: 100%;
    }
    .gen-pane-heading {
        width: 100%;
        height: auto;
        color: $primary;
        text-style: bold;
        padding: 0 1;
    }
    .gen-list {
        width: 100%;
        height: 1fr;
        border: round $primary;
        background: transparent;
        padding: 0 1;
    }
    #gen-user-pane {
        margin-left: 1;
    }
    #gen-summary {
        width: 100%;
        height: auto;
        text-align: center;
        color: $text-muted;
    }
    #gen-buttons {
        width: 100%;
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    #gen-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("a", "select_removable", "Select all removable"),
        ("n", "select_none", "Select none"),
    ]

    can_focus = True

    class Selected(Message):
        """The chosen generations, per profile, as delete-generations arguments."""

        def __init__(self, values: dict) -> None:
            super().__init__()
            self.values = values

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # Per profile: the parsed listing and the error that stopped it.
        self._found = {"system": [], "user": []}
        self._errors = {"system": None, "user": None}
        self._variables = {"system": "SYSTEM_GENERATIONS", "user": "USER_GENERATIONS"}
        # "All Hosts" has no single listing to show; see _show_fleet_notice.
        self._fleet = False

    def compose(self) -> ComposeResult:
        with Vertical(id="gen-body"):
            yield Label(id="gen-title")
            with Horizontal(id="gen-panes"):
                with Vertical(classes="gen-pane", id="gen-system-pane"):
                    yield Label("System profile", classes="gen-pane-heading")
                    yield SelectionList(id="gen-system", classes="gen-list")
                with Vertical(classes="gen-pane", id="gen-user-pane"):
                    yield Label("User profile", classes="gen-pane-heading")
                    yield SelectionList(id="gen-user", classes="gen-list")
            yield Label(id="gen-summary")
            with Horizontal(id="gen-buttons"):
                yield Button("Skip deletion", id="gen-skip", variant="default")
                yield Button("Continue", id="gen-continue", variant="primary")

    def on_focus(self) -> None:
        self.query_one("#gen-system", SelectionList).focus()

    # --- loading ----------------------------------------------------------

    def load(self, config: dict, hostname: str | None, variables: dict) -> None:
        """Read both profiles from the host and show what they hold.

        ``variables`` maps a profile to the variable name its selection fills,
        so the command definition stays the one place those names are written.
        """
        self._variables = dict(variables)
        self._fleet = hostname == ALL_HOSTS
        where = f" on {hostname}" if hostname and not self._fleet else ""
        self.query_one("#gen-title", Label).update(f"Generations{where}")

        if self._fleet:
            # Numbers mean different things on different machines, so there is
            # nothing sensible to tick; only the blunt rule applies to a fleet.
            self._show_fleet_notice()
            return

        for profile in ("system", "user"):
            found, error = gen.read(profile, config, hostname)
            self._found[profile] = found
            self._errors[profile] = error
            self._fill(profile)

        self._update_summary()

    def _show_fleet_notice(self) -> None:
        """All Hosts: offer the coarse rule, since there is no one list."""
        for profile in ("system", "user"):
            listing = self.query_one(f"#gen-{profile}", SelectionList)
            listing.clear_options()
            listing.disabled = True
            listing.add_options([
                Selection("all but the current generation", -1, False)
            ])
            label = "System profile" if profile == "system" else "User profile"
            self.query_one(f"#gen-{profile}-pane", Vertical).query_one(
                ".gen-pane-heading", Label
            ).update(f"{label} — every host")
        self.query_one("#gen-title", Label).update("Generations on every host")
        self.query_one("#gen-summary", Label).update(
            "Several hosts selected, so generations cannot be picked one by one. "
            "Continue removes all but the current generation on each."
        )
        self.query_one("#gen-continue", Button).label = "Remove all old"

    def _fill(self, profile: str) -> None:
        listing = self.query_one(f"#gen-{profile}", SelectionList)
        listing.clear_options()
        heading = self.query_one(f"#gen-{profile}-pane", Vertical).query_one(
            ".gen-pane-heading", Label
        )
        label = "System profile" if profile == "system" else "User profile"

        if self._errors[profile]:
            heading.update(f"{label} — unavailable")
            listing.add_options([
                Selection(f"could not list: {self._errors[profile]}", -1, False)
            ])
            listing.disabled = True
            return

        listing.disabled = False
        heading.update(f"{label} — {gen.describe(self._found[profile])}")
        # The current generation is left out rather than shown unticked:
        # nix-env refuses to delete it, so offering it can only mislead.
        for entry in self._found[profile]:
            if entry["current"]:
                continue
            listing.add_option(
                Selection(f"{entry['id']:>5}   {entry['date']}", entry["id"], False)
            )

    def _removable(self, profile: str) -> list[int]:
        if self._errors[profile]:
            return []
        return [entry["id"] for entry in self._found[profile] if not entry["current"]]

    def _selection(self, profile: str) -> list[int]:
        if self._errors[profile]:
            return []
        listing = self.query_one(f"#gen-{profile}", SelectionList)
        return [value for value in listing.selected if value != -1]

    def _update_summary(self) -> None:
        picked = sum(len(self._selection(p)) for p in ("system", "user"))
        if picked:
            text = f"{picked} generation(s) selected for deletion"
        else:
            text = "Nothing selected — Continue will only collect garbage"
        self.query_one("#gen-summary", Label).update(
            f"{text}  ·  space toggle · a all removable · n none"
        )

    # --- events -----------------------------------------------------------

    def action_select_removable(self) -> None:
        for profile in ("system", "user"):
            if self._errors[profile]:
                continue
            listing = self.query_one(f"#gen-{profile}", SelectionList)
            for value in self._removable(profile):
                listing.select(value)
        self._update_summary()

    def action_select_none(self) -> None:
        for profile in ("system", "user"):
            if self._errors[profile]:
                continue
            self.query_one(f"#gen-{profile}", SelectionList).deselect_all()
        self._update_summary()

    @on(SelectionList.SelectedChanged)
    def _selection_changed(self, event: SelectionList.SelectedChanged) -> None:
        event.stop()
        self._update_summary()

    @on(Button.Pressed, "#gen-continue")
    def _continue(self, event: Button.Pressed) -> None:
        event.stop()
        if self._fleet:
            self._emit({name: "old" for name in self._variables.values()})
            return
        self._emit(
            {
                self._variables[profile]: gen.normalise(self._selection(profile))
                or gen.NONE
                for profile in ("system", "user")
            }
        )

    @on(Button.Pressed, "#gen-skip")
    def _skip(self, event: Button.Pressed) -> None:
        event.stop()
        self._emit({name: gen.NONE for name in self._variables.values()})

    def _emit(self, values: dict) -> None:
        self.post_message(self.Selected(values))
