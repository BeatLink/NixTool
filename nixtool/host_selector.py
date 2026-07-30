from .options_widget import OptionsWidget

ALL_HOSTS = "all"


class HostSelector(OptionsWidget):
    """Selects one configured NixOS host, or all of them.

    Hosts are supplied by the app from the already-loaded config rather than
    re-read here, so the selector cannot disagree with the config the rest of
    the run resolves against.
    """

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self.title = "Select Hosts"

    def load_hosts(self, hosts: dict) -> None:
        """Populate the list from a ``{hostname: url}`` mapping."""
        # Keyed by hostname, so two hosts sharing a URL stay distinct; the
        # option label carries the URL for context.
        options = {ALL_HOSTS: "All Hosts"}
        for name, url in hosts.items():
            if name == ALL_HOSTS:
                continue
            options[name] = f"{name}  ({url})" if url else name
        self.options = options
        self.title = "Select Hosts" if hosts else "No hosts configured"

    def on_options_widget_selected(self, event: OptionsWidget.Selected) -> None:
        event.stop()
        # Re-emit as HostSelector.Selected so the app can distinguish this
        # widget's choice from any other option list.
        self.post_message(self.Selected(self, event.key, event.value))

    class Selected(OptionsWidget.Selected):
        @property
        def hostname(self) -> str:
            """The chosen hostname, or ``ALL_HOSTS`` for every configured host."""
            return self.value

        @property
        def is_all_hosts(self) -> bool:
            return self.value == ALL_HOSTS
