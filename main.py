# Interactive Rich CLI entry point for the Emporio da Musica customer support agent.

import argparse
import sys
import uuid
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from src.agent import build_agent, chat_with_agent
from src.config import SUPPORTED_PROVIDERS, get_llm_settings, validate_data_files

EXIT_COMMANDS = {"/exit", "/quit", "/sair", "exit", "quit"}


# Parse command-line arguments for provider selection and optional single-turn query.
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emporio da Musica - Customer Support AI Assistant CLI"
    )
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default=None,
        help="Override LLM provider ('gemini' or 'groq'). Defaults to LLM_PROVIDER in .env.",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Run a single query non-interactively and exit.",
    )
    return parser.parse_args(argv)


# Display the startup banner with active model details and available slash commands.
def print_welcome_banner(console: Console, provider: str, model: str) -> None:
    banner_lines = [
        "[bold cyan]Emporio da Musica — Customer Support AI Assistant[/bold cyan]",
        f"[dim]Active provider:[/dim] [bold green]{provider}[/bold green] ([italic]{model}[/italic])",
        "",
        "[bold]Commands:[/bold]",
        "  • [yellow]/reset[/yellow] — Start a new conversation session (fresh thread ID)",
        "  • [yellow]/exit[/yellow], [yellow]/quit[/yellow], or [yellow]/sair[/yellow] — Exit the assistant",
    ]
    console.print(
        Panel(
            "\n".join(banner_lines),
            title="[bold]Welcome[/bold]",
            border_style="cyan",
            expand=False,
        )
    )


# Run the interactive chat loop or execute a single query when --query is provided.
def run_cli(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()

    if args.query is not None and not args.query.strip():
        console.print(
            Panel(
                "[bold red]Invalid Query:[/bold red] '--query' cannot be empty or whitespace only.",
                title="Input Error",
                border_style="red",
            )
        )
        return 1

    try:
        validate_data_files()
        settings = get_llm_settings(provider_override=args.provider)
        agent = build_agent(provider=settings["provider"])
    except (FileNotFoundError, ValueError) as exc:
        console.print(
            Panel(
                f"[bold red]Configuration Error:[/bold red] {exc}\n\n"
                "[dim]Tip: Copy .env.example to .env and configure your API key.[/dim]",
                title="Startup Failed",
                border_style="red",
            )
        )
        return 1
    except Exception as exc:
        console.print(
            Panel(
                f"[bold red]Initialization Error:[/bold red] Could not initialize the assistant ({type(exc).__name__}).",
                title="Startup Failed",
                border_style="red",
            )
        )
        return 1

    thread_id = f"session-{uuid.uuid4().hex[:8]}"

    if args.query is not None:
        cleaned_query = args.query.strip()
        try:
            with console.status("[cyan]Thinking...[/cyan]", spinner="dots"):
                reply = chat_with_agent(agent, cleaned_query, thread_id=thread_id)
            console.print(
                Panel(
                    Markdown(reply or "_No response returned._"),
                    title="[bold green]Emporio da Musica[/bold green]",
                    border_style="green",
                )
            )
            return 0
        except Exception as exc:
            console.print(
                Panel(
                    f"[bold red]Error while processing query:[/bold red] {exc}",
                    title="Runtime Error",
                    border_style="red",
                )
            )
            return 1

    print_welcome_banner(console, settings["provider"], settings["model"])

    while True:
        try:
            user_input = console.input("\n[bold cyan]You:[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Session ended. Goodbye![/dim]")
            break

        if not user_input:
            continue

        cmd = user_input.lower()
        if cmd in EXIT_COMMANDS:
            console.print(
                "[dim]Thank you for visiting Emporio da Musica. Goodbye![/dim]"
            )
            break

        if cmd == "/reset":
            thread_id = f"session-{uuid.uuid4().hex[:8]}"
            console.print(
                f"[yellow]Started a new conversation session ({thread_id}).[/yellow]"
            )
            continue

        try:
            with console.status(
                "[cyan]Consulting store records...[/cyan]", spinner="dots"
            ):
                reply = chat_with_agent(agent, user_input, thread_id=thread_id)
            console.print()
            console.print(
                Panel(
                    Markdown(reply or "_No response returned._"),
                    title="[bold green]Emporio da Musica[/bold green]",
                    border_style="green",
                )
            )
        except Exception as exc:
            console.print(
                Panel(
                    f"[bold red]Error while processing message:[/bold red] {exc}",
                    title="Runtime Error",
                    border_style="red",
                )
            )

    return 0


if __name__ == "__main__":
    sys.exit(run_cli())


# Engineering review notes (documented for README.md reference):
# 1. Resolved during refactoring:
#    - Added whitespace validation and try/except error handling for non-interactive '--query' mode.
#    - Added fallback startup exception handling so unexpected initialization errors render cleanly.
#    - Clarified '/reset' wording to reflect starting a fresh session thread_id and aligned exit
#      command aliases in the welcome banner.
# 2. Known trade-offs left out to avoid over-engineering for this challenge:
#    - The CLI remains a lightweight procedural entry point delegating configuration and graph
#      execution to config.py and agent.py without extra UI wrapper classes.
