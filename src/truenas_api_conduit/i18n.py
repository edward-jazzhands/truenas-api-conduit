from pathlib import Path
import gettext

# compiled translations directory
localedir = Path(__file__).parent / "locales"

# reads the OS LANG env var
t = gettext.translation("mycli", localedir=localedir, fallback=True)
_ = t.gettext

# for pluralization
ngettext = t.ngettext
