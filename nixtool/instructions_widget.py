from textual.app import ComposeResult
from textual.containers import Vertical, Center
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Markdown, Button

class InstructionsWidget(Widget):
    """A widget for displaying command instructions or warnings before proceeding."""

    DEFAULT_CSS = """
    InstructionsWidget {
        width: 100%;
        height: 100%;
    }
    #scroll-container {
        width: 80%;
        height: 1fr;
        margin: 2 4;
        border: tall $primary;
        padding: 1 2;
    }
    #button-container {
        height: auto;
        margin-bottom: 1;
    }
    """

    class Continued(Message):
        """Sent when the user clicks the continue button."""

    def compose(self) -> ComposeResult:
        with Vertical():
            with Vertical(id="scroll-container"):
                yield Markdown(id="instructions-text")
            with Center(id="button-container"):
                yield Button("Continue", variant="primary", id="continue-btn")

    def setup(self, markdown_text: str) -> None:
        """Set the text to display."""
        self.query_one("#instructions-text", Markdown).update(markdown_text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continue-btn":
            self.post_message(self.Continued())