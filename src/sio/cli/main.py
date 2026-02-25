"""SIO CLI — Self-Improving Organism command-line interface."""

import click


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """SIO: Self-Improving Organism for AI coding CLIs."""
    pass


if __name__ == "__main__":
    cli()
