"""Built-in tools.

Every module here registers itself with `src.tools.registry.tools` on import;
`load_builtin_tools()` imports the package so that happens exactly once.
Dropping a new module in this directory is all it takes to add a capability.
"""
