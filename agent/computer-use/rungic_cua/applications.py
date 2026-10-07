# SPDX-License-Identifier: MIT
"""Installed desktop entries and localized labels, shared by Cua and acceptance tools."""
from __future__ import annotations

import os


def localized_names(info) -> set[str]:
    """Name and GenericName in every language of the .desktop file: the request may
    be in Chinese while this process runs in another locale."""
    names: set[str] = set()
    path = info.get_filename() if hasattr(info, 'get_filename') else None
    if not path:
        return names
    try:
        section = False
        for line in open(path, encoding='utf-8', errors='replace'):
            line = line.strip()
            if line.startswith('['):
                section = line == '[Desktop Entry]'
            elif section and (line.startswith('Name') or line.startswith('GenericName')) and '=' in line:
                names.add(line.split('=', 1)[1].strip().casefold())
    except OSError:
        pass
    return names


def find_application(query: str) -> dict | None:
    """Installed .desktop entry by id or (localized) name, with the names its window may carry."""
    from gi.repository import Gio
    query_folded = query.casefold().removesuffix('.desktop')
    best = None
    for info in Gio.AppInfo.get_all():
        if not info.should_show():
            continue
        app_id = (info.get_id() or '').removesuffix('.desktop')
        names = {app_id.casefold(), (info.get_name() or '').casefold(), (info.get_display_name() or '').casefold()}
        names |= localized_names(info)
        classes = {app_id.casefold(), app_id.split('.')[-1].casefold(),
                   os.path.basename(info.get_executable() or '').casefold()}
        if isinstance(info, Gio.DesktopAppInfo) and info.get_startup_wm_class():
            classes.add(info.get_startup_wm_class().casefold())
        entry = {'id': app_id, 'name': info.get_display_name(), 'classes': sorted(c for c in classes if c),
                 'exec': info.get_commandline() or ''}
        if query_folded in names:
            return entry
        if best is None and any(query_folded in n for n in names if n):
            best = entry
    return best
