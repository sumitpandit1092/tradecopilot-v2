from rich.console import Console
from rich.panel import Panel

from ai import AI

console = Console()

ai = AI()

console.print(
    Panel.fit(
        "TradeCopilot AI\nType 'exit' or 'quit' to leave. Type 'summary' for your trade journal stats.",
        title="Welcome"
    )
)

while True:

    try:
        question = input("\nYou > ")
    except (KeyboardInterrupt, EOFError):
        console.print("\n[bold]Exiting TradeCopilot.[/bold]")
        break

    if question.strip().lower() in ("exit", "quit"):
        console.print("[bold]Exiting TradeCopilot.[/bold]")
        break

    if question.strip().lower() == "summary":
        stats = ai.executor.summary()
        console.print(Panel(str(stats), title="Trade Journal Summary"))
        continue

    if not question.strip():
        continue

    try:
        answer = ai.ask(question)
    except Exception as e:
        answer = f"Something went wrong while processing that: {e}"

    console.print(
        Panel(answer, title="TradeCopilot")
    )
