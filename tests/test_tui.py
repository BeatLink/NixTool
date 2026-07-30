"""Tests for the Textual interface.

These drive the real app through Pilot rather than calling handlers directly,
so the wiring between widgets (which is where the TUI's bugs lived) is covered.
"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from nixtool.command_browser import CommandBrowser
from nixtool.command_runner import CommandRunner
from nixtool.host_selector import HostSelector
from nixtool.main import NixOSManager
from nixtool.options_widget import OptionsWidget
from nixtool.plan_widget import PlanWidget


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "nixtool-config.json"
    path.write_text(json.dumps({
        "flake_path": str(tmp_path),
        "user": "admin",
        "hosts": {"alpha": "10.0.0.1", "beta": "10.0.0.2"},
    }))
    return path


async def select(pilot, widget_id: str, option_id: str):
    """Choose an option by id from one of the app's OptionsWidgets."""
    widget = pilot.app.query_one(f"#{widget_id}", OptionsWidget)
    widget.post_message(OptionsWidget.Selected(widget, "", option_id))
    await pilot.pause()


async def run_command(pilot, command_id: str):
    """Pick a command from the browser by its qualified registry id."""
    browser = pilot.app.query_one("#command-menu", CommandBrowser)
    browser.post_message(CommandBrowser.Selected(command_id))
    await pilot.pause()
    await pilot.pause()


# --- state isolation ------------------------------------------------------

def test_mutable_state_is_per_instance(config_path):
    """Regression: these were class attributes shared by every app instance."""
    first = NixOSManager(config_path)
    second = NixOSManager(config_path)
    first.selected_vars["ACTION"] = "switch"
    first.command_queue.append("echo leaked")
    first.hostname = "alpha"
    assert second.selected_vars == {}
    assert second.command_queue == []
    assert second.hostname == ""


def test_runner_queue_is_per_instance():
    first, second = CommandRunner(), CommandRunner()
    first.command_queue.append("echo leaked")
    assert second.command_queue == []


# --- host selector --------------------------------------------------------

def test_hosts_sharing_a_url_stay_distinct():
    """Regression: the options dict used to be keyed by URL, collapsing hosts."""
    selector = HostSelector()
    selector.load_hosts({"alpha": "10.0.0.1", "beta": "10.0.0.1"})
    assert {"alpha", "beta", "all"} <= set(selector.options)


# --- the wizard -----------------------------------------------------------

async def test_plan_is_shown_before_running(config_path):
    app = NixOSManager(config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await run_command(pilot, "maintenance/rebuild")
        # rebuild needs ACTION then a host, then must land on the plan screen.
        assert app.content_switcher.current == "variable-menu"
        await select(pilot, "variable-menu", "switch")
        assert app.content_switcher.current == "host-selector"
        await select(pilot, "host-selector", "alpha")
        assert app.content_switcher.current == "plan-view"


async def test_destructive_command_requires_typed_confirmation(config_path):
    app = NixOSManager(config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await run_command(pilot, "maintenance/garbage-collect")
        await select(pilot, "host-selector", "alpha")
        await pilot.pause()
        assert app.content_switcher.current == "plan-view"

        plan = app.query_one("#plan-view", PlanWidget)
        run_button = plan.query_one("#plan-run")
        assert run_button.disabled, "destructive run must start disabled"

        confirm = plan.query_one("#plan-confirm")
        confirm.value = "no"
        await pilot.pause()
        assert run_button.disabled
        confirm.value = "yes"
        await pilot.pause()
        assert not run_button.disabled


async def test_non_destructive_command_needs_no_typing(config_path):
    app = NixOSManager(config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await run_command(pilot, "maintenance/flake-update")
        assert app.content_switcher.current == "plan-view"
        assert not app.query_one("#plan-run").disabled


async def test_secrets_are_masked_in_the_plan(config_path):
    from textual.widgets import Static
    app = NixOSManager(config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.current_cmd = __import__(
            "nixtool.registry", fromlist=["registry"]
        ).find_command("format-data-drive")
        app.selected_vars = {
            "DATA_DRIVE": "/dev/sdb", "MIRROR_DRIVE": "none",
            "PASSPHRASE": "swordfish", "POOL_UUID": "ab12cd34",
        }
        app.hostname = "alpha"
        app.prepare_command_queue()
        await pilot.pause()
        text = str(app.query_one("#plan-text", Static).content)
        assert "PASSPHRASE = ********" in text
        assert "swordfish" not in text.split("command(s) will run")[0]


async def test_escape_steps_back_and_reasks(config_path):
    app = NixOSManager(config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await run_command(pilot, "maintenance/rebuild")
        await select(pilot, "variable-menu", "switch")
        assert app.content_switcher.current == "host-selector"
        await pilot.press("escape")
        assert app.content_switcher.current == "variable-menu"
        # The discarded ACTION must be asked for again, not silently reused.
        assert "ACTION" not in app.selected_vars


# --- runner ---------------------------------------------------------------

async def test_runner_reports_success(config_path):
    app = NixOSManager(config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        runner = app.query_one("#command-runner", CommandRunner)
        app.content_switcher.current = "command-runner"
        runner.load_command_queue(["true", "true"])
        await pilot.pause()
        runner._worker = runner.run_command()
        await runner._worker.wait()
        await pilot.pause()
        assert runner.final_return_code == 0
        assert "succeeded" in str(runner.message.content)


async def test_runner_stops_at_the_first_failure(config_path):
    app = NixOSManager(config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        runner = app.query_one("#command-runner", CommandRunner)
        app.content_switcher.current = "command-runner"
        runner.load_command_queue(["false", "echo should-not-run"])
        await pilot.pause()
        runner._worker = runner.run_command()
        await runner._worker.wait()
        await pilot.pause()
        assert runner.final_return_code != 0
        log = "\n".join(str(s) for s in runner.logview.lines)
        assert "should-not-run" not in log.split("will now be executed")[-1].split("Press Start")[-1]


async def test_cancel_terminates_the_running_command(config_path):
    """A cancelled run must not leave the child process alive."""
    import os
    import signal
    app = NixOSManager(config_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        runner = app.query_one("#command-runner", CommandRunner)
        app.content_switcher.current = "command-runner"
        # Long enough that the run is certainly still in flight when cancelled.
        runner.load_command_queue(["sleep 60"])
        await pilot.pause()
        runner._worker = runner.run_command()
        for _ in range(50):
            await pilot.pause()
            if runner._process is not None:
                break
        assert runner._process is not None, "process never started"
        pid = runner._process.pid
        assert runner.is_running

        runner.action_cancel()
        for _ in range(50):
            await pilot.pause()
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                break
        else:
            # Still alive after cancellation.
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise AssertionError(f"child {pid} survived cancellation")

        assert "ancel" in str(runner.message.content)
        assert not runner.return_button.has_class("invisible")
