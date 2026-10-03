"""Desktop decision mode. Codex owns screenshot reasoning by default; API is opt-in."""
from pathlib import Path
import os

PLAN_FILE = Path.home() / '.config/rungic-cua/plan'
PLANS = ('codex', 'luna', 'atspi')


def plan() -> str:
    try:
        value = PLAN_FILE.read_text().strip()
    except OSError:
        value = ''
    return value if value in PLANS else 'codex'


def save(value: str) -> str:
    if value == 'api':
        value = 'luna'                 # preserve the existing explicit API choice
    if value not in PLANS:
        raise ValueError('desktop mode: codex, api (luna), or atspi')
    PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PLAN_FILE.with_suffix('.tmp')
    temporary.write_text(value + '\n')
    os.replace(temporary, PLAN_FILE)
    return value
