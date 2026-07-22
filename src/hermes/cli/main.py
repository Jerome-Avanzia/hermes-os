import typer

from hermes.cli.commands import context, inspect, knowledge, plan, skills, workspace

app = typer.Typer(help="Hermes OS command-line interface.")

app.command(name="inspect")(inspect.inspect)
app.command(name="workspace")(workspace.workspace)
app.command(name="knowledge")(knowledge.knowledge)
app.command(name="context")(context.context)
app.command(name="plan")(plan.plan)
app.command(name="skills")(skills.skills)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
