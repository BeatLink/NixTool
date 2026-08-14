import pathlib
import subprocess

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Footer, ContentSwitcher, Button
from textual.reactive import reactive

from . import config as config_mod
from . import registry, resolver
from .theme import white_blue_theme
from .command_browser import CommandBrowser
from .command_runner import CommandRunner
from .host_selector import ALL_HOSTS, HostSelector
from .input_widget import InputWidget
from .disk_selector import DiskSelector
from .instructions_widget import InstructionsWidget
from .options_widget import OptionsWidget
from .plan_widget import PlanWidget


class NixOSManager(App):
    CSS = """
    Screen {
        align: center middle;
        content-align: center middle;
    }

    Header {
        align: center middle;
        content-align: center middle;
    }

    Header, HeaderClock {
        background: $background;
        color: $primary;
        text-style: bold;
    }

    ContentSwitcher  {
        width: 100%;
        height: 100%;
        align: center middle;
        content-align: center middle;
        border: round $primary;
    }

    ContentSwitcher > * {
        width: 100%;
        height: 100%;
        align: center middle;
        content-align: center middle;
    }
    """

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    config = reactive({})

    def __init__(self, config_path: pathlib.Path = None):
        super().__init__()
        # Fall back to the same search path the CLI uses, so both front ends
        # find the same config regardless of how nixtool was launched.
        self.config_path = (
            config_path
            or config_mod.resolve_config_path()
            or pathlib.Path.cwd() / config_mod.CONFIG_FILENAME
        )
        # Per-instance, not class-level: these are mutated in place during a
        # run, and sharing them across instances leaked state between runs.
        self.current_cmd = {}
        self.selected_vars = {}
        self.current_var = ""
        self.instructions_shown = False
        self.hostname = ""
        self.command_queue = []
        # The screens visited so far, so Escape can walk back out of a wizard.
        self.history = []
        # Which variable each prompting screen is currently collecting.
        self.step_var = {}

    def compose(self) -> ComposeResult:
        self.header = Header(show_clock=True)
        self.header.tall = True
        self.content_switcher = ContentSwitcher(initial="command-menu")
        self.content_switcher.loading = True
        self.command_browser = CommandBrowser(id="command-menu")
        self.variable_menu = OptionsWidget(id="variable-menu")
        self.input_menu = InputWidget(id="input-menu")
        self.instructions_menu = InstructionsWidget(id="instructions-menu")
        self.disk_selector = DiskSelector(id="disk-selector")
        self.host_selector = HostSelector(id="host-selector")
        self.plan_view = PlanWidget(id="plan-view")
        self.command_runner = CommandRunner(id="command-runner")
        yield self.header
        with self.content_switcher:
            yield self.command_browser
            yield self.variable_menu
            yield self.input_menu
            yield self.instructions_menu
            yield self.disk_selector
            yield self.host_selector
            yield self.plan_view
            yield self.command_runner
        yield Footer()

    def on_mount(self) -> None:
        self.title = "NixTool"
        self.sub_title = "CLI tool for managing flake based NixOS installations"
        self.register_theme(white_blue_theme)
        self.theme = "white_blue"

    def on_ready(self) -> None:
        self.load_config()

    @work(exclusive=True)
    async def load_config(self):
        try:
            self.config, path = config_mod.load_config(self.config_path)
        except config_mod.ConfigError as exc:
            self.config = {}
            self.notify(str(exc), title="Config error", severity="error", timeout=15)
        else:
            if path is None:
                self.notify(
                    f"Config file not found: {self.config_path}",
                    title="Warning",
                    severity="warning",
                    timeout=10,
                )
            else:
                self.host_selector.load_hosts(config_mod.hosts(self.config))
        self.content_switcher.loading = False

    def show(self, screen_id: str, *, remember: bool = True) -> None:
        """Switch to a screen, recording where we came from for Escape.

        Each history entry pairs the screen with the variable it collected, so
        stepping back can discard that value and ask for it again.
        """
        current = self.content_switcher.current
        if remember and current and current != screen_id:
            self.history.append((current, self.step_var.get(current, "")))
        self.content_switcher.current = screen_id

    def action_go_back(self) -> None:
        """Step back one screen, or out of a category, without re-running work."""
        if self.content_switcher.current == "command-runner":
            # Leaving a finished run resets; a running one must be cancelled first.
            if self.command_runner.is_running:
                self.notify(
                    "A command is still running. Press ctrl+c to cancel it.",
                    severity="warning",
                )
                return
            self.reset()
            return

        if not self.history:
            # Already at the command browser; nothing above it.
            return

        previous, collected_var = self.history.pop()
        # The value the target screen collected is discarded, so stepping back
        # and forward re-asks rather than silently reusing the old answer.
        if collected_var:
            self.selected_vars.pop(collected_var, None)
        if previous == "host-selector":
            self.hostname = ""
        self.current_var = ""
        self.content_switcher.current = previous
        self.query_one(f"#{previous}").focus()

    @on(CommandBrowser.Selected)
    def process_command(self, selected: CommandBrowser.Selected):
        if self.content_switcher.current != "command-menu":
            return
        selected.stop()
        try:
            chosen = registry.find_command(selected.command_id)
        except LookupError as exc:
            self.notify(str(exc), title="Unknown command", severity="error")
            return

        if chosen.get("interactive"):
            self.launch_interactive(chosen)
            return

        self.current_cmd = chosen
        self.selected_vars = {}
        self.instructions_shown = False
        self.current_var = ""
        self.hostname = ""
        self.check_next_step()

    def launch_interactive(self, cmd_dict):
        command = resolver.interactive_command(cmd_dict, self.config)
        with self.suspend():
            subprocess.run(
                command,
                shell=True,
                cwd=self.config.get("flake_path"),
            )

    @on(OptionsWidget.Selected, "#variable-menu")
    def process_variable(self, selected: OptionsWidget.Selected):
        if self.content_switcher.current != "variable-menu":
            return
        selected.stop()
        self.selected_vars[self.current_var] = str(selected.value)
        self.current_var = ""
        self.check_next_step()

    @on(DiskSelector.Selected)
    def process_disk(self, selected: DiskSelector.Selected):
        if self.content_switcher.current != "disk-selector":
            return
        selected.stop()
        self.selected_vars[self.current_var] = str(selected.value)
        self.current_var = ""
        self.check_next_step()

    @on(InstructionsWidget.Continued)
    def on_instructions_continued(self, event: InstructionsWidget.Continued):
        event.stop()
        self.instructions_shown = True
        self.check_next_step()

    @on(HostSelector.Selected)
    def process_host(self, message: HostSelector.Selected):
        message.stop()
        self.hostname = message.hostname
        self.check_next_step()

    def check_next_step(self):
        if "instructions" in self.current_cmd and not self.instructions_shown:
            self.instructions_menu.setup(self.current_cmd["instructions"])
            self.show("instructions-menu")
            return

        # registry.collect_variables is the same traversal the CLI uses, so both
        # front ends prompt for exactly the same variable set.
        all_vars = registry.collect_variables(self.current_cmd)
        for var_name, var_cfg in all_vars.items():
            if var_name not in self.selected_vars:
                self.current_var = var_name
                var_type = var_cfg.get("type", "list")
                screen = {
                    "list": "variable-menu",
                    "disk": "disk-selector",
                }.get(var_type, "input-menu")
                self.step_var[screen] = var_name

                if var_type == "list":
                    self.variable_menu.title = var_cfg.get("title", f"Select {var_name}")
                    self.variable_menu.options = var_cfg.get("options", {})
                    self.show("variable-menu")
                    self.variable_menu.focus()
                elif var_type == "disk":
                    self.disk_selector.title = var_cfg.get("title", "Select Disk")
                    self.disk_selector.refresh_disks(allow_none=var_cfg.get("allow_none", False))
                    self.show("disk-selector")
                    self.disk_selector.focus()
                elif var_type == "uuid":
                    # Generated, never prompted; fill it and keep walking.
                    self.selected_vars.update(
                        resolver.generate_auto_values({var_name: var_cfg}, self.selected_vars)
                    )
                    self.current_var = ""
                    self.check_next_step()
                    return
                else:
                    self.input_menu.setup(
                        var_cfg.get("title", f"Enter {var_name}"),
                        is_password=(var_type == "password"),
                        is_multiline=(var_type == "textarea")
                    )
                    self.show("input-menu")
                return

        if registry.needs_host(self.current_cmd) and not self.hostname:
            self.show("host-selector")
            self.host_selector.focus()
            return

        self.prepare_command_queue()

    @on(InputWidget.Submitted)
    def on_input_submitted(self, event: InputWidget.Submitted):
        event.stop()
        self.selected_vars[self.current_var] = event.value
        self.current_var = ""
        self.check_next_step()

    def prepare_command_queue(self):
        """Resolve the plan and hand it to the confirmation screen."""
        try:
            hostnames = self.target_hostnames()
            plan = resolver.build_plan(
                self.current_cmd, self.config, hostnames, self.selected_vars
            )
        except resolver.ResolutionError as exc:
            self.notify(str(exc), title="Cannot run command", severity="error", timeout=12)
            self.reset()
            return

        if not plan:
            self.notify(
                "This command resolved to an empty plan.",
                title="Nothing to run",
                severity="warning",
            )
            self.reset()
            return

        # (hostname, command) pairs; the runner only needs the command strings,
        # but the plan view shows which host each one targets.
        self.command_queue = [command for _, command in plan]
        # Secrets travel out of band; the resolved commands only reference them.
        self.command_runner.command_env = resolver.secret_environment(
            registry.collect_variables(self.current_cmd), self.selected_vars
        )
        self.plan_view.setup(self.current_cmd, plan, self.selected_vars)
        self.show("plan-view")

    def target_hostnames(self):
        """Which hosts the queue should be resolved for.

        Mirrors ``resolver.target_hosts``, but sources the selection from the
        host selector rather than from CLI flags. "All Hosts" maps onto the
        CLI's ``--all-hosts``.
        """
        if not registry.needs_host(self.current_cmd):
            return [None]
        if self.hostname == ALL_HOSTS:
            return resolver.target_hosts(
                self.current_cmd, self.config, None, all_hosts=True
            )
        return resolver.target_hosts(
            self.current_cmd, self.config, [self.hostname], all_hosts=False
        )

    @on(PlanWidget.Confirmed)
    def on_plan_confirmed(self, event: PlanWidget.Confirmed):
        event.stop()
        self.run_commands()

    @on(PlanWidget.Cancelled)
    def on_plan_cancelled(self, event: PlanWidget.Cancelled):
        event.stop()
        self.action_go_back()

    def run_commands(self):
        self.show("command-runner")
        self.command_runner.focus()
        if self.config.get("flake_path"):
            self.command_runner.work_dir = self.config["flake_path"]
        self.command_runner.load_command_queue(self.command_queue)

    @on(Button.Pressed, "#return")
    def on_return_pressed(self, event: Button.Pressed):
        event.stop()
        self.reset()

    def reset(self):
        self.current_cmd = {}
        self.selected_vars = {}
        self.current_var = ""
        self.instructions_shown = False
        self.hostname = ""
        self.command_queue = []
        self.history = []
        self.step_var = {}
        # Return to the command browser.
        self.show("command-menu", remember=False)
        self.command_browser.focus()
