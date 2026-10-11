import os
import sys

# Repo root = directory containing this file (portable, no hardcoded path).
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv() -> None:
    """Load ``.env`` (repo root, gitignored) into ``os.environ`` (no override).

    Connectors stored with ``${env:...}`` placeholders (e.g. the sorooshan1040
    MSSQL ``${env:SQL_SERVER_PASSWORD}``) resolve at request time, so the
    variables must exist in the server process environment — not just the
    launcher shell.
    """
    dotenv = os.path.join(_REPO_ROOT, ".env")
    try:
        with open(dotenv, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


_load_dotenv()
# Fallback: make workspace packages importable even when the .venv editable
# .pth files still point to the old checkout path (DB_GPT/DB-GPT).
for _pkg in ("dbgpt-core", "dbgpt-client", "dbgpt-ext", "dbgpt-serve", "dbgpt-app", "dbgpt-sandbox"):
    _src = os.path.join(_REPO_ROOT, "packages", _pkg, "src")
    if os.path.isdir(_src) and _src not in sys.path:
        sys.path.insert(0, _src)

sys.argv = [
    "dbgpt",
    "start",
    "webserver",
    "--config",
    os.path.join(_REPO_ROOT, "configs", "dbgpt-proxy-liara.toml"),
]
from dbgpt.cli.cli_scripts import cli
cli()
