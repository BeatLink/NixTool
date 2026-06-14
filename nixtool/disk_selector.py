import subprocess
from textual.app import ComposeResult
from .options_widget import OptionsWidget

class DiskSelector(OptionsWidget):
    """A widget for selecting local block devices using lsblk."""

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self.title = "Select Disk"

    def on_mount(self) -> None:
        self.refresh_disks()

    def refresh_disks(self) -> None:
        """Queries lsblk and populates the options list."""
        try:
            # -d: skip holders, -n: no headings, -o: output columns
            result = subprocess.run(
                ["lsblk", "-dn", "-o", "NAME,SIZE,MODEL"], 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            drives = {}
            for line in result.stdout.strip().split('\n'):
                parts = line.split(None, 2)
                if len(parts) >= 2:
                    name = f"/dev/{parts[0]}"
                    size = parts[1]
                    model = parts[2] if len(parts) > 2 else "Generic"
                    description = f"{name} ({size} - {model})"
                    # Store path as ID, description as display
                    drives[name] = description
            
            self.options = drives
        except Exception:
            self.options = {}
            self.title = "Error listing disks (Is lsblk installed?)"

    def on_options_widget_selected(self, event: OptionsWidget.Selected) -> None:
        event.stop()
        # Re-emit as a specialized Selected message
        self.post_message(self.Selected(self, event.key, event.value))