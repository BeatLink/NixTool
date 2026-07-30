import subprocess

from .options_widget import OptionsWidget

class DiskSelector(OptionsWidget):
    """A widget for selecting local block devices using lsblk."""

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self.title = "Select Disk"

    # No on_mount refresh: the app calls refresh_disks() when a disk variable is
    # actually reached, so starting the TUI does not shell out to lsblk.

    def refresh_disks(self, allow_none: bool = False) -> None:
        """Queries lsblk and populates the options list."""
        drives = {}
        if allow_none:
            drives["none"] = "-- None / Skip --"

        try:
            # -d: skip holders, -n: no headings, -o: output columns
            result = subprocess.run(
                ["lsblk", "-dn", "-o", "NAME,SIZE,MODEL"],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            self.options = drives
            self.title = "lsblk not found — cannot list disks"
            return
        except subprocess.CalledProcessError as exc:
            # The specific failure matters when debugging a machine where the
            # disk list is empty but lsblk is installed.
            self.options = drives
            detail = (exc.stderr or "").strip().splitlines()
            self.title = f"lsblk failed: {detail[-1] if detail else exc.returncode}"
            return

        for line in result.stdout.strip().splitlines():
            parts = line.split(None, 2)
            if len(parts) >= 2:
                name = f"/dev/{parts[0]}"
                size = parts[1]
                model = parts[2] if len(parts) > 2 else "Generic"
                # Store path as ID, description as display
                drives[name] = f"{name} ({size} - {model})"

        self.options = drives
        if not any(key != "none" for key in drives):
            self.title = "No disks found"

    def on_options_widget_selected(self, event: OptionsWidget.Selected) -> None:
        event.stop()
        # Re-emit as a specialized Selected message
        self.post_message(self.Selected(self, event.key, event.value))