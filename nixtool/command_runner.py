import asyncio
import os
import subprocess

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Center, CenterMiddle, HorizontalGroup
from textual.widgets import Label, Button, ProgressBar, RichLog
from textual.widget import Widget
from textual.events import Focus
from textual.reactive import reactive


CSS = """
    #container {
        width: 97.5%;
        height: 95%;
    }

    HorizontalGroup {
        align: center middle;
    }

    #label {
        color: $primary;
        text-style: bold;
    }

    #progress {
        margin-bottom: 1;
    }

    #logview {
        height: 100%;
        border: round $primary;
        background: transparent;
    }

    #message {
        text-align: center;
        text-style: bold;
        align: center middle;
        content-align: center middle;
    }

    #message.success {
        color: $success;
    }

    #message.error {
        color: $error;
    }

    #start, #return, #cancel, #stage-yes, #stage-skip {
        text-style: bold;
        align: center middle;
        content-align: center middle;
    }

    .borders {
        border: round orange;
    }

    .invisible {
        display: none
    }
"""

# A long nixos-rebuild emits far more output than is useful to keep in memory,
# and an uncapped RichLog grows until the UI stutters.
MAX_LOG_LINES = 10_000


class CommandRunner(Widget):
    DEFAULT_CSS = CSS

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel run", show=True),
    ]

    can_focus = True
    work_dir = reactive(None)

    def __init__(self, work_dir=None, **kwargs):
        super().__init__(**kwargs)
        self.work_dir = work_dir
        # Secret values for the queue, carried in the child's environment rather
        # than in the command text, which is both logged and visible in ps.
        self.command_env = {}
        # Per-instance: a class-level list would be shared by every runner.
        self.command_queue = []
        # Stages the queue is split into; an optional one is offered rather than
        # run, after the output the decision depends on is already on screen.
        self.stages = []
        self.skipped_stages = []
        self._answer = None
        self._answered = None
        self.final_return_code = 0
        self._process: asyncio.subprocess.Process | None = None
        self._worker = None
        self.container = Container(id="container")
        self.label = Label(id="label")
        self.progress = ProgressBar(id="progress", show_eta=False)
        self.logview = RichLog(id="logview", max_lines=MAX_LOG_LINES, markup=False)
        self.message = Label(id="message", classes="invisible")
        self.message.loading = True
        self.return_button = Button("Return", id="return", variant="primary")
        self.return_button.compact = True
        self.return_button.flat = True
        self.start_button = Button("Start", id="start", variant="primary")
        self.start_button.compact = True
        self.start_button.flat = True
        self.cancel_button = Button("Cancel", id="cancel", variant="error")
        self.cancel_button.compact = True
        self.cancel_button.flat = True
        self.cancel_button.add_class("invisible")
        self.yes_button = Button("Yes", id="stage-yes", variant="primary")
        self.yes_button.compact = True
        self.yes_button.flat = True
        self.yes_button.add_class("invisible")
        self.skip_button = Button("Skip", id="stage-skip", variant="default")
        self.skip_button.compact = True
        self.skip_button.flat = True
        self.skip_button.add_class("invisible")

    @property
    def is_running(self) -> bool:
        return self._worker is not None and not self._worker.is_finished

    def compose(self) -> ComposeResult:
        with self.container:
            with Center():
                yield Center(self.label)
                yield Center(self.progress)
                yield CenterMiddle(self.logview)
                yield Center(self.message)
                with HorizontalGroup():
                    yield self.return_button
                    yield self.start_button
                    yield self.yes_button
                    yield self.skip_button
                    yield self.cancel_button

    def on_focus(self, _: Focus):
        self.start_button.focus()

    def load_command_queue(self, command_queue):
        """Load a flat queue: one mandatory stage, nothing to decide."""
        self.load_stages([{
            "name": "", "prompt": None, "optional": False,
            "commands": list(command_queue),
        }])

    def load_stages(self, stages):
        """Load a wizard: stages run in order, optional ones are offered first."""
        # Clear UI
        self.label.update("Ready To Start")
        self.label.refresh()
        self.logview.clear()
        self.message.update("")
        self.message.loading = True
        self.message.add_class("invisible")
        self.message.remove_class("success", "error")
        self.message.refresh()
        self.start_button.remove_class("invisible")
        self.start_button.disabled = False
        self.return_button.remove_class("invisible")
        self.cancel_button.add_class("invisible")
        self.yes_button.add_class("invisible")
        self.skip_button.add_class("invisible")
        self.stages = list(stages)
        self.skipped_stages = []
        # Kept flat as well, since everything outside the wizard -- the plan
        # screen, the tests -- reasons about the queue as one list.
        self.command_queue = [
            command for stage in self.stages for command in stage["commands"]
        ]
        self.progress.update(total=len(self.command_queue) or 1, progress=0)
        self.logview.write("The following commands will now be executed\n")
        for stage in self.stages:
            if stage["name"]:
                suffix = "  (you will be asked first)" if stage["optional"] else ""
                self.logview.write(f"[{stage['name']}]{suffix}")
            for command in stage["commands"]:
                self.logview.write(f"- {command}")
        self.logview.write("\nPress Start to begin")
        self.start_button.focus()

    @on(Button.Pressed, "#start")
    def start(self, event: Button.Pressed):
        event.stop()
        self._worker = self.run_command()

    @on(Button.Pressed, "#cancel")
    def cancel_pressed(self, event: Button.Pressed):
        event.stop()
        self.action_cancel()

    def action_cancel(self) -> None:
        """Stop the queue and terminate the command that is currently running."""
        if not self.is_running:
            return
        self._terminate_process()
        if self._worker is not None:
            self._worker.cancel()
        self.logview.write("\n>>> Cancelled by user <<<")
        self.label.update("Cancelled")
        self._finish(cancelled=True)

    def _terminate_process(self) -> None:
        """Kill the running child, escalating if it ignores SIGTERM.

        Without this, cancelling the worker would leave the subprocess running
        detached — a half-finished `sgdisk` or `zpool create` with no UI.
        """
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        self._kill_if_still_running(process)

    @work
    async def _kill_if_still_running(self, process) -> None:
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except (asyncio.TimeoutError, TimeoutError):
            try:
                process.kill()
            except ProcessLookupError:
                pass

    async def _offer(self, stage) -> bool:
        """Ask whether to run an optional stage, and wait for the answer.

        Asked here rather than up front because the answer depends on output
        this run has already produced -- the point of a wizard.
        """
        prompt = stage["prompt"] or f"Run {stage['name']}?"
        self._answer = None
        self._answered = asyncio.Event()
        self.label.update(prompt)
        self.logview.write("\n--------------------------------------------------------------------------\n")
        self.logview.write(f">>> {prompt} <<<")
        self.yes_button.remove_class("invisible")
        self.skip_button.remove_class("invisible")
        self.skip_button.focus()
        try:
            await self._answered.wait()
        finally:
            self.yes_button.add_class("invisible")
            self.skip_button.add_class("invisible")
        return bool(self._answer)

    def _answer_stage(self, answer: bool) -> None:
        self._answer = answer
        if self._answered is not None:
            self._answered.set()

    @on(Button.Pressed, "#stage-yes")
    def _stage_yes(self, event: Button.Pressed):
        event.stop()
        self._answer_stage(True)

    @on(Button.Pressed, "#stage-skip")
    def _stage_skip(self, event: Button.Pressed):
        event.stop()
        self._answer_stage(False)

    @work(exclusive=True)
    async def run_command(self):
        self.final_return_code = 0
        self.skipped_stages = []
        self.start_button.add_class("invisible")
        self.return_button.add_class("invisible")
        self.cancel_button.remove_class("invisible")
        self.message.remove_class("invisible")
        total = len(self.command_queue)
        self.progress.update(total=total or 1, progress=0)
        done = 0
        try:
            for stage in self.stages:
                if stage["optional"] and not await self._offer(stage):
                    self.skipped_stages.append(stage["name"])
                    self.logview.write(f"\n>>> Skipped: {stage['name']} <<<")
                    done += len(stage["commands"])
                    self.progress.update(progress=done)
                    continue
                if stage["name"]:
                    self.logview.write(f"\n===== {stage['name']} =====")
                for command in stage["commands"]:
                    done += 1
                    command_message = f"Running command {done} of {total}: {command}"
                    self.label.update(command_message)
                    self.logview.write("\n--------------------------------------------------------------------------\n")
                    self.logview.write(f">>> {command_message} <<<")
                    process = await asyncio.create_subprocess_shell(
                        command,
                        cwd=self.work_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        env={**os.environ, **self.command_env} if self.command_env else None,
                        # Its own process group, so terminate() reaches the whole
                        # pipeline rather than only the shell that spawned it.
                        start_new_session=True,
                    )
                    self._process = process
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        self.logview.write(line.decode(errors="replace").rstrip())
                    await process.wait()
                    self._process = None
                    self.progress.update(progress=done)
                    if int(process.returncode) == 0:
                        self.logview.write(f"\n>>> Command succeeded with return code {process.returncode} <<")
                    else:
                        self.logview.write(f"\n>>> Command failed with return code {process.returncode} <<<")
                        # Stop the queue if a command fails
                        self.final_return_code = process.returncode
                        break
                if self.final_return_code:
                    break
        except asyncio.CancelledError:
            self._terminate_process()
            raise

        self._finish()

    def _finish(self, cancelled: bool = False) -> None:
        """Show the outcome and restore the Return button."""
        skipped = (
            f" Skipped: {', '.join(self.skipped_stages)}."
            if self.skipped_stages else ""
        )
        if cancelled:
            self.message.update("Run cancelled. Some commands may have already applied changes.")
            self.message.add_class("error")
        elif int(self.final_return_code) == 0:
            self.message.update(f"All commands succeeded!{skipped}")
            self.message.add_class("success")
        else:
            self.message.update("One or more commands have failed! Please check the logs")
            self.message.add_class("error")
        self.message.loading = False
        self.message.remove_class("invisible")
        self.message.refresh()
        self.cancel_button.add_class("invisible")
        self.return_button.remove_class("invisible")
        self.return_button.focus()
        self.refresh()
