"""Headless execution of a resolved command plan."""

import os
import subprocess
import sys


class ExecutionResult:
    """Outcome of running a plan."""

    def __init__(self):
        self.completed = 0
        self.total = 0
        self.failed_command = None
        self.returncode = 0

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_plan(
    plan,
    work_dir=None,
    *,
    stream=None,
    quiet: bool = False,
    keep_going: bool = False,
    env_by_host=None,
) -> ExecutionResult:
    """Run ``(hostname, command)`` pairs in order.

    Stops at the first failure unless ``keep_going`` is set, mirroring the TUI's
    behaviour of aborting a queue when a step fails. Output is streamed as it
    arrives so long-running builds show progress.

    ``env_by_host`` maps a hostname to the secret environment its commands were
    resolved against; the values live here rather than in the command string so
    they stay out of ``ps`` and out of the printed plan.
    """
    # Resolved at call time, not import time, so redirected output is honoured.
    stream = sys.stdout if stream is None else stream
    result = ExecutionResult()
    result.total = len(plan)

    for index, (hostname, command) in enumerate(plan, start=1):
        if not quiet:
            label = f" [{hostname}]" if hostname else ""
            print(f"\n>>> [{index}/{result.total}]{label} {command}", file=stream, flush=True)

        secret_env = (env_by_host or {}).get(hostname) or {}
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=work_dir or None,
            stdout=None if quiet else subprocess.PIPE,
            stderr=None if quiet else subprocess.STDOUT,
            text=True,
            env={**os.environ, **secret_env} if secret_env else None,
        )
        if process.stdout is not None:
            for line in process.stdout:
                stream.write(line)
                stream.flush()
        returncode = process.wait()

        if returncode == 0:
            result.completed += 1
            continue

        result.returncode = returncode
        result.failed_command = command
        if not quiet:
            print(
                f">>> command failed with exit code {returncode}",
                file=stream,
                flush=True,
            )
        if not keep_going:
            break

    return result


def run_interactive(command: str, work_dir=None) -> int:
    """Run a command with the terminal attached, for TUI-style sub-tools."""
    return subprocess.run(command, shell=True, cwd=work_dir or None).returncode
