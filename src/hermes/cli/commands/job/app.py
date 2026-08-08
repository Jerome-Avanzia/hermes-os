"""Typer sub-application for the `hermes job` command group."""

import typer

from hermes.cli.commands.job.run import run

job_app = typer.Typer(help="Execute and manage Workspace jobs.")
job_app.command(name="run")(run)
