import os
import glob
import sys

# Pfad zum site-packages deiner venv
venv_site = os.path.join(sys.prefix, "Lib", "site-packages")

# Suche nach der salsa20 Binary
pattern = os.path.join(venv_site, "_salsa20*.pyd")
found = glob.glob(pattern)

if not found:
    raise RuntimeError(f"Could not find _salsa20.pyd in: {venv_site}")

binaries = [(found[0], ".")]
hiddenimports = ["salsa20"]