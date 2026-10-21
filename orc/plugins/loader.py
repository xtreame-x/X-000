import os
import importlib.util
from typing import Dict, List, Optional
from .base import Plugin
from orc.logger import get_logger

logger = get_logger('plugins')

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


_loader_instance: Optional['PluginLoader'] = None


def get_loader() -> Optional['PluginLoader']:
    global _loader_instance
    return _loader_instance


class PluginLoader:
    """Dynamic plugin loader for Oryn."""

    def __init__(self, plugins_dir: Optional[str] = None):
        global _loader_instance
        self.plugins_dir = plugins_dir or os.path.join(ORYN_ROOT, 'plugins')
        self.plugins: Dict[str, Plugin] = {}
        self.commands: Dict[str, str] = {}  # command -> plugin_name
        _loader_instance = self

    def load_all(self):
        """Load all plugins from the plugins directory."""
        os.makedirs(self.plugins_dir, exist_ok=True)

        # Load builtin plugins
        builtin_dir = os.path.join(os.path.dirname(__file__), 'builtin')
        self._load_from_dir(builtin_dir, is_builtin=True)

        # Load user plugins
        self._load_from_dir(self.plugins_dir, is_builtin=False)

        logger.info(f"Loaded {len(self.plugins)} plugins, {len(self.commands)} commands")

    def reload_plugins(self):
        """Dynamically reload user plugins from plugins directory without restart."""
        self._load_from_dir(self.plugins_dir, is_builtin=False)
        logger.info(f"Reloaded plugins: {len(self.plugins)} plugins, {len(self.commands)} commands now active")
        return len(self.plugins)

    def _load_from_dir(self, directory: str, is_builtin: bool = False):
        """Load plugins from a directory."""
        if not os.path.isdir(directory):
            return

        for filename in os.listdir(directory):
            if not filename.endswith('.py'):
                continue
            if filename.startswith('_'):
                continue

            filepath = os.path.join(directory, filename)
            self._load_plugin(filepath, is_builtin)

    def _load_plugin(self, filepath: str, is_builtin: bool = False):
        """Load a single plugin from file."""
        try:
            plugin_name = os.path.splitext(os.path.basename(filepath))[0]

            spec = importlib.util.spec_from_file_location(plugin_name, filepath)
            if not spec or not spec.loader:
                return

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find Plugin subclass
            plugin_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                    issubclass(attr, Plugin) and
                    attr is not Plugin):
                    plugin_class = attr
                    break

            if not plugin_class:
                return

            plugin = plugin_class()
            plugin.on_load({
                'oryn_root': ORYN_ROOT,
                'is_builtin': is_builtin,
            })

            self.plugins[plugin.name] = plugin

            # Register commands
            for cmd, help_text in plugin.get_commands().items():
                self.commands[cmd] = plugin.name
                logger.debug(f"Registered command: {cmd} -> {plugin.name}")

            logger.info(f"Loaded plugin: {plugin.name} v{plugin.version}")

        except Exception as e:
            logger.error(f"Failed to load plugin {filepath}: {e}")

    def execute_command(self, command: str, args: str = "") -> str:
        """Execute a plugin command."""
        if command not in self.commands:
            return f"unknown command: {command}"

        plugin_name = self.commands[command]
        plugin = self.plugins.get(plugin_name)
        if not plugin:
            return f"plugin not found: {plugin_name}"

        try:
            return plugin.execute(command, args)
        except Exception as e:
            return f"plugin error: {e}"

    def on_message(self, message: str) -> Optional[str]:
        """Let plugins hook into messages."""
        for plugin in self.plugins.values():
            try:
                result = plugin.on_message(message)
                if result is not None:
                    return result
            except Exception:
                pass
        return None

    def unload_all(self):
        """Unload all plugins."""
        for plugin in self.plugins.values():
            try:
                plugin.on_unload()
            except Exception:
                pass
        self.plugins.clear()
        self.commands.clear()

    def list_plugins(self) -> List[dict]:
        """List all loaded plugins."""
        return [
            {
                'name': p.name,
                'description': p.description,
                'version': p.version,
                'commands': list(p.get_commands().keys()),
            }
            for p in self.plugins.values()
        ]

    def get_plugin(self, name: str) -> Optional[Plugin]:
        """Get a plugin by name."""
        return self.plugins.get(name)
