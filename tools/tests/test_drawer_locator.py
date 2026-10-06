# SPDX-License-Identifier: MIT
"""Replay actual AT-SPI field semantics without device commands."""
from types import SimpleNamespace
import pytest
import ui_launch_check as ui
import rungic_acceptance as acc

# covers: agent.dev-diagnostics/E3, delivery.acceptance/E1
@pytest.mark.parametrize('name', ['Search', '搜索'])
def test_drawer_open_focus_and_input_use_semantics_in_both_languages(monkeypatch, name):
    field = {'role': 'text', 'name': name, 'path': '0/20', 'extents': [26, 56, 301, 43],
             'states': ['showing', 'visible', 'enabled', 'editable']}
    opened, taps, commands = [], [], []
    def find(app, role=None, **kwargs):
        assert 'name' not in kwargs
        return [dict(field)] if opened else []
    def run(command, *args, **kwargs):
        commands.append(command)
        if command.startswith('input swipe'): opened.append(True)
        return SimpleNamespace(stdout='Physical size: 1080x2400')
    def tap(app, path):
        taps.append(path);field['states'].append('focused');return {}
    monkeypatch.setattr(ui.rungic_agent, 'ui_find', find)
    monkeypatch.setattr(ui.rungic_agent, 'ui_tap', tap)
    monkeypatch.setattr(ui, 'run', run)
    monkeypatch.setattr(ui.time, 'sleep', lambda _: None)
    ui.open_drawer()
    assert ui.drawer_search_fields()[0]['path'] == '0/20'
    monkeypatch.setattr(acc, '_home', lambda: None)
    monkeypatch.setattr(acc, 'run', run)
    monkeypatch.setattr(acc, 'ocr_screen', lambda: ([['Calcul', .99, [100, 200]], ['Calculator', .99, [100, 400]]], 'fixture.png'))
    monkeypatch.setattr(acc.rungic_agent, 'a11y', lambda _: {'enabled': True})
    monkeypatch.setattr(acc.rungic_agent, 'ui_windows', lambda: [])
    result = acc.input_text({})
    assert result['passed'], result
    assert taps == ['0/20']
    assert any(cmd.startswith('input text ') for cmd in commands)
    assert len([cmd for cmd in commands if cmd.startswith('input swipe')]) == 1

# covers: agent.dev-diagnostics/E3
@pytest.mark.parametrize('changes', [{'states': ['visible', 'editable']}, {'extents': [0, -100, 301, 43]}, {'extents': [0, 0, 0, 43]}])
def test_hidden_disabled_or_offscreen_fields_are_not_drawer_candidates(monkeypatch, changes):
    field = {'role': 'text', 'path': '0/20', 'states': ['showing', 'visible', 'enabled', 'editable'], 'extents': [26,56,301,43]}
    field.update(changes)
    monkeypatch.setattr(ui.rungic_agent, 'ui_find', lambda *args, **kwargs: [field])
    assert ui.drawer_search_fields() == []

# covers: agent.dev-diagnostics/E3
def test_multiple_editable_fields_are_ambiguous_and_never_tapped(monkeypatch):
    field = {'role': 'text', 'states': ['showing', 'visible', 'enabled', 'editable'], 'extents': [26,56,301,43]}
    monkeypatch.setattr(ui.rungic_agent, 'ui_find', lambda *args, **kwargs: [dict(field,path='0/20'),dict(field,path='0/21')])
    with pytest.raises(RuntimeError, match='ambiguous'):
        ui.drawer_search_fields()
