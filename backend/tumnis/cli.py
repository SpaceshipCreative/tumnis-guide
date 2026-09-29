"""Command-line entry point: `tumnis api|worker|migrate|seed|...` (commands land in later WPs)."""

import typer

app = typer.Typer(name="tumnis", help="Tumnis Guide backend.", no_args_is_help=True)
