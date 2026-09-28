"""Minimal .env loader (KEY=VALUE lines) so secrets need not be exported by hand."""
import os
import re

_LINE = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$')


def load_env(path):
    """Load variables from `path` without overriding ones already in the environment.

    Returns the names that were set. A missing file is not an error.
    """
    try:
        with open(path, encoding='utf-8') as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        return []
    loaded = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        else:
            value = value.split(' #', 1)[0].rstrip()  # allow trailing comments on unquoted values
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
