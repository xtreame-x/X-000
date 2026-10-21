class Plugin:
    """Base class for all Oryn plugins."""

    name: str = "unnamed"
    description: str = ""
    version: str = "0.1"
    author: str = ""

    def on_load(self, context):
        """Called when plugin is loaded.

        Args:
            context: Dict with oryn_context, config, etc.
        """
        pass

    def get_commands(self) -> dict:
        """Return dict of {command_name: help_text}."""
        return {}

    def execute(self, command: str, args: str) -> str:
        """Execute a plugin command.

        Args:
            command: The command name
            args: Arguments string

        Returns:
            Response string
        """
        return f"command '{command}' not implemented"

    def on_message(self, message: str) -> str | None:
        """Hook into every user message.

        Args:
            message: User's input

        Returns:
            Response string to intercept, or None to pass through
        """
        return None

    def on_unload(self):
        """Called when plugin is unloaded."""
        pass
