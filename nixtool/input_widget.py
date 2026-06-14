from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, Input


class InputWidget(Widget):
    """A reusable widget for collecting text or password input."""

    DEFAULT_CSS = """
    InputWidget {
        align: center middle;
        width: 100%;
        height: 100%;
    }
    #input-container {
        width: 60%;
        height: auto;
    }
    #input-label {
        width: 100%;
        height: auto;
        text-align: center;
        color: $primary;
        text-style: bold;
    }
    #variable-input {
        width: 100%;
        margin: 1 0;
        border: round $primary;
        background: transparent;
    }
    """

    class Submitted(Message):
        """Sent when the user submits the input field."""
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="input-container"):
            yield Label(id="input-label")
            yield Input(id="variable-input")

    def setup(self, title: str, is_password: bool = False) -> None:
        """Configure the widget for a new input request."""
        self.query_one("#input-label", Label).update(title)
        field = self.query_one("#variable-input", Input)
        field.value = ""
        field.password = is_password
        field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.post_message(self.Submitted(event.value))