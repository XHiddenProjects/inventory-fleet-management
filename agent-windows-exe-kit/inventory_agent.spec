# PyInstaller spec for the inventory agent.
# Build (on a Windows machine, with Python + pyinstaller installed):
#     pyinstaller inventory_agent.spec
# Output: dist\inventory-agent.exe  (single file, no Python/pip needed on target machines)

# --hidden-import entries because the agent loads collectors via
# __import__("collectors.<name>", ...) at runtime, which PyInstaller's
# static analysis can't see.
hidden = [
    "collectors",
    "collectors.system",
    "collectors.software",
    "collectors.processes",
    "collectors.network",
    "collectors.identity",
    "collectors.services",
]

a = Analysis(
    ['inventory_agent.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='inventory-agent',
    console=True,
    onefile=True,
    clean=True,
    upx=False,
)
