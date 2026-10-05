import os
import sys

_config = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "configs",
    "dbgpt-proxy-ollama-qwen.toml",
)


def _load_env_file(path):
    """Export KEY=VALUE lines from .env (without overriding real environment variables)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# The config references secrets as ${env:...}; load them from .env before it is parsed.
# Fall back to env.txt (legacy name in this repo) so direct `python launch_dbgpt.py`
# works even without the .bat / .env.
_load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
_load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.txt"))

sys.argv = ["dbgpt", "start", "webserver", "--config", _config]
from dbgpt.cli.cli_scripts import cli
cli()
