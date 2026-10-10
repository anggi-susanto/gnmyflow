"""The `studiowright` / `sw` command line."""

from __future__ import annotations

import click

from studiowright import __version__, chrome, paths


@click.group()
@click.version_option(__version__, "-V", "--version", prog_name="StudioWright")
def main() -> None:
    """StudioWright: Google Flow and Flow Music from the terminal. Unofficial."""


@main.group()
def auth() -> None:
    """Sign in and manage profiles."""


@auth.command("login")
@click.option("--profile", default=paths.DEFAULT_PROFILE, show_default=True)
@click.option("--url", default=chrome.FLOW_URL, show_default=True, help="Page to open.")
def auth_login(profile: str, url: str) -> None:
    """Open Chrome on PROFILE; sign in by hand, then close the window."""
    exe = chrome.find_chrome()
    if exe is None:
        raise click.ClickException("Google Chrome was not found; install it first.")
    target = paths.profile_dir(profile)
    click.echo(f"Opening Chrome on profile '{profile}' ({target}).")
    click.echo("Sign in to your Google account, open Flow once, then close the window.")
    chrome.run_sign_in(exe, target, url)
    click.echo("Chrome closed. The profile is saved.")


@auth.command("list")
def auth_list() -> None:
    """List profiles."""
    root = paths.home() / "profiles"
    names = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    if not names:
        click.echo("No profiles yet. Run `sw auth login`.")
    for name in names:
        click.echo(name)


if __name__ == "__main__":  # pragma: no cover
    main()
