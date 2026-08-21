# Registers this tool's own menu item directly into ukore_menu's central
# "Ukore Tools" registry (merged into UkoreShot 2026-08-20 — previously
# wired through MayaToolkit's UkoreMaya/__init__.py/menu_utils.py instead of
# registering itself, the way most other Maya tool plugins in this codebase
# already do). Must run at import time, triggered by plugin.py's
# pre_open_mel launch hook — see UkoreMenu's own README's "ต้อง auto-import
# ตอน Maya เปิดไฟล์" requirement: register_item() only takes effect once
# this package is actually imported, not merely once its code exists on
# PYTHONPATH.
#
# The "Playblast Options..." (Anim) item and its dialog/options_store.py
# were removed 2026-08-21, per the user's own request — every playblast
# setting is hardcoded in function.py now, nothing left to configure.
try:
    from UkoreMenu import registry, MenuItemSpec, ReloadHandlerSpec, reload_package

    registry.register_item(
        MenuItemSpec(
            id="playblast",
            label="Ukore Shot Playblast",
            category="General",
            command="import UkorePlayblast; from UkorePlayblast import function; function.publish_playblast()",
            order=40,
        )
    )

    registry.register_reload_handler(
        ReloadHandlerSpec(
            id="ukore_playblast",
            label="UkorePlayblast",
            callback=lambda: reload_package("UkorePlayblast"),
            order=40,
        )
    )
except ImportError:
    pass
