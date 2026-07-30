import pathlib


def run(config_path: pathlib.Path = None):
    """Bootstraps and runs the NixOSManager application."""
    # Imported lazily: main.py imports sibling modules via the package, so
    # importing it at module scope would re-enter this partially-initialised
    # package and fail with a circular import.
    from .main import NixOSManager

    app = NixOSManager(config_path)
    app.run()
