# -*- mode: python ; coding: utf-8 -*-
import sys

from PyInstaller.utils.hooks import collect_all

datas = [('assets', 'assets')]  # logo and bridge icon, read via app_info.asset_path (issue #115)
binaries = []
hiddenimports = []
tmp_ret = collect_all('rapidocr_onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('av')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('aiortc')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('cv2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['ui_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AtlasKVM',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows exe icon (issue #115). macOS takes its icon from the .app bundle.
    icon='assets/bridge.ico' if sys.platform.startswith('win') else None,
)
core = Analysis(
    ['core_daemon.py'], pathex=[], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False, optimize=0,
)
core_pyz = PYZ(core.pure)
core_exe = EXE(core_pyz, core.scripts, [], exclude_binaries=True, name='Core',
               debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
               console=False, disable_windowed_traceback=True,
               icon='assets/bridge.ico' if sys.platform.startswith('win') else None)
coll = COLLECT(
    exe, core_exe, core.binaries, core.datas,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AtlasKVM',
)

# macOS: pin the app to light mode so Tk colours stay readable under Dark Mode (issue #105).
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='AtlasKVM.app',
        info_plist={'NSRequiresAquaSystemAppearance': True},
    )
