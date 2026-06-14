import json
import pathlib
from textual import on
from textual.app import ComposeResult
from .options_widget import OptionsWidget

class HostSelector(OptionsWidget):
    """A portable widget for selecting NixOS hosts from a configuration file."""

    def __init__(self, config_path: pathlib.Path, id: str | None = None) -> None:
        super().__init__(id=id)
        self.config_path = config_path
        self.title = "Select Hosts"

    def refresh_hosts(self) -> None:
        """Loads host data from config and populates the list."""
        try:
            config = json.loads(self.config_path.read_text())
            # We set the reactive 'options' property, which triggers a recompose
            self.options = {"All Hosts": "all"} | config.get("hosts", {})
        except Exception:
            self.title = "Error loading hosts"

    # We override the handler to translate OptionsWidget.Selected into HostSelector.Selected
    # This keeps the main NixOSManager logic unchanged.
    def on_options_widget_selected(self, event: OptionsWidget.Selected) -> None:
        event.stop()
        self.post_message(self.Selected(self, event.key, event.value))

    class Selected(OptionsWidget.Selected):
        @property
        def hostname(self) -> str:
            return self.key
        @property
        def host_url(self) -> str:
            return self.value