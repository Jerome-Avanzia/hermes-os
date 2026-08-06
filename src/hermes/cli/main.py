import typer

from hermes.cli.commands import (
    context,
    execute,
    generate,
    implement,
    inspect,
    knowledge,
    plan,
    read,
    skills,
    workspace,
)
from hermes.logging_config import configure_logging

app = typer.Typer(help="Hermes OS command-line interface.")

app.command(name="inspect")(inspect.inspect)
app.command(name="workspace")(workspace.workspace)
app.command(name="knowledge")(knowledge.knowledge)
app.command(name="context")(context.context)
app.command(name="plan")(plan.plan)
app.command(name="skills")(skills.skills)
app.command(name="execute")(execute.execute)
app.command(name="read")(read.read)
app.command(name="generate")(generate.generate)
app.command(name="implement")(implement.implement)


def main() -> None:
    configure_logging()
    app()


if __name__ == "__main__":
    main()
