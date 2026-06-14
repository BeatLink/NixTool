from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, Input, TextArea, Button


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
    #variable-text-area {
        width: 100%;
        height: 15;
        margin: 1 0;
        border: round $primary;
    }
    #submit-btn {
        width: 100%;
        margin-bottom: 1;
    }
    .invisible {
        display: none;
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
            yield TextArea(id="variable-text-area", classes="invisible")
            yield Button("Submit", id="submit-btn", variant="primary", classes="invisible")

    def setup(self, title: str, is_password: bool = False, is_multiline: bool = False) -> None:
        """Configure the widget for a new input request."""
        self.query_one("#input-label", Label).update(title)

        input_field = self.query_one("#variable-input", Input)
        text_area = self.query_one("#variable-text-area", TextArea)
        submit_btn = self.query_one("#submit-btn", Button)

        if is_multiline:
            input_field.add_class("invisible")
            text_area.remove_class("invisible")
            submit_btn.remove_class("invisible")
            text_area.text = ""
            text_area.focus()
        else:
            input_field.remove_class("invisible")
            text_area.add_class("invisible")
            submit_btn.add_class("invisible")
            input_field.value = ""
            input_field.password = is_password
            input_field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.post_message(self.Submitted(event.value))

    @on(Button.Pressed, "#submit-btn")
    def on_button_pressed(self, event: Button.Pressed) -> None:
        text_area = self.query_one("#variable-text-area", TextArea)
        self.post_message(self.Submitted(text_area.text))