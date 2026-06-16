"""Module defining the extensible ToolRegistry for the SRE agent.

This module provides decorators to dynamically register custom Python functions
as agent tools, supporting future additions like Grafana or SigNoz query tools.
"""

from typing import Callable, Any

class ToolRegistry:
    """Registry to manage and retrieve custom agent tools."""

    def __init__(self) -> None:
        """Initializes the ToolRegistry with an empty tools list."""
        self._tools: list[Callable[..., Any]] = []

    def register(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Registers a function as a custom tool.

        Args:
            func: The Python callable to register.

        Returns:
            The original function, unmodified.
        """
        if func not in self._tools:
            self._tools.append(func)
        return func

    def get_tools(self) -> list[Callable[..., Any]]:
        """Retrieves all registered tool callables.

        Returns:
            A list of registered functions.
        """
        return self._tools

# Global registry instance
registry = ToolRegistry()

def register_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to register a function in the global ToolRegistry.

    Args:
        func: The function to decorate.

    Returns:
        The registered function.
    """
    return registry.register(func)
