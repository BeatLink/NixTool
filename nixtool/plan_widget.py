"""The confirmation step shown between resolving a plan and running it.

The TUI used to jump straight from the last variable prompt into execution, so
a stray Enter could start a disk wipe. This screen is the gate: it shows the
resolved commands, masks secret values, and for destructive commands requires
the word "yes" to be typed rather than a button press.
"""

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static

from . import registry, secrets


class PlanWidget(Widget):
    """Shows a resolved plan and asks for confirmation."""

    DEFAULT_CSS = """
    PlanWidget {
        width: 100%;
        height: 100%;
    }
    #plan-body {
        width: 100%;
        height: 100%;
        padding: 1 2;
    }
    #plan-title {
        width: 100%;
        height: auto;
        text-align: center;
        color: $primary;
        text-style: bold;
    }
    #plan-warning {
        width: 100%;
        height: auto;
        text-align: center;
        color: $error;
        text-style: bold;
    }
    #plan-scroll {
        width: 100%;
        height: 1fr;
        border: round $primary;
        padding: 0 1;
    }
    #plan-confirm {
        width: 100%;
        height: auto;
        margin-top: 1;
        border: round $error;
        background: transparent;
    }
    #plan-buttons {
        width: 100%;
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    #plan-buttons Button {
        margin: 0 1;
    }
    .hidden {
        display: none;
    }
    """

    class Confirmed(Message):
        """The user approved the plan."""

    class Cancelled(Message):
        """The user backed out of the plan."""

    def compose(self) -> ComposeResult:
        with Vertical(id="plan-body"):
            yield Label(id="plan-title")
            yield Label(id="plan-warning", classes="hidden")
            with VerticalScroll(id="plan-scroll"):
                yield Static(id="plan-text")
            yield Input(
                placeholder="type 'yes' to confirm",
                id="plan-confirm",
                classes="hidden",
            )
            with Horizontal(id="plan-buttons"):
                yield Button("Back", id="plan-back", variant="default")
                yield Button("Run", id="plan-run", variant="primary")

    def setup(self, node: dict, plan: list, values: dict) -> None:
        """Render a plan for confirmation.

        ``plan`` is the ``(hostname, command)`` list from ``resolver.build_plan``.
        """
        self._destructive = registry.is_destructive(node)
        self.query_one("#plan-title", Label).update(
            f"Review plan: {node.get('name', '')}"
        )

        # Resolved commands contain [ ] (test expressions, globs) which Rich
        # would read as markup, so every interpolated value is escaped and only
        # the literal tags written here stay live.
        variables = registry.collect_variables(node)
        lines = []
        if values:
            lines.append("[bold]Variables[/bold]")
            for name in sorted(values):
                spec = variables.get(name, {})
                shown = secrets.redact(name, spec, values[name])
                lines.append(f"  {escape(name)} = {escape(shown)}")
            lines.append("")

        lines.append(f"[bold]{len(plan)} command(s) will run[/bold]")
        current_host = object()
        for index, (hostname, command) in enumerate(plan, start=1):
            if hostname != current_host:
                current_host = hostname
                if hostname:
                    lines.append(f"\n  [italic]on {escape(hostname)}[/italic]")
            lines.append(f"  {index:>3}. {escape(command)}")

        self.query_one("#plan-text", Static).update("\n".join(lines))

        warning = self.query_one("#plan-warning", Label)
        confirm = self.query_one("#plan-confirm", Input)
        run_button = self.query_one("#plan-run", Button)

        if self._destructive:
            warning.update("This command is destructive and cannot be undone.")
            warning.remove_class("hidden")
            confirm.value = ""
            confirm.remove_class("hidden")
            run_button.disabled = True
            confirm.focus()
        else:
            warning.add_class("hidden")
            confirm.add_class("hidden")
            run_button.disabled = False
            run_button.focus()

    @on(Input.Changed, "#plan-confirm")
    def _gate_run_button(self, event: Input.Changed) -> None:
        self.query_one("#plan-run", Button).disabled = (
            event.value.strip().lower() != "yes"
        )

    @on(Input.Submitted, "#plan-confirm")
    def _submit_confirmation(self, event: Input.Submitted) -> None:
        event.stop()
        if event.value.strip().lower() == "yes":
            self.post_message(self.Confirmed())

    @on(Button.Pressed, "#plan-run")
    def _run(self, event: Button.Pressed) -> None:
        event.stop()
        if self._destructive:
            typed = self.query_one("#plan-confirm", Input).value.strip().lower()
            if typed != "yes":
                return
        self.post_message(self.Confirmed())

    @on(Button.Pressed, "#plan-back")
    def _back(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.Cancelled())
