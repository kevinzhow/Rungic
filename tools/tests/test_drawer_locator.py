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
        taps.append(path);field['states'].append('focused');return {'scale':3,'window':{'x':0,'y':0}}
    monkeypatch.setattr(ui.rungic_agent, 'ui_find', find)
    monkeypatch.setattr(ui.rungic_agent, 'ui_tap', tap)
    monkeypatch.setattr(ui, 'run', run)
    monkeypatch.setattr(ui.time, 'sleep', lambda _: None)
    ui.open_drawer()
    assert ui.drawer_search_fields()[0]['path'] == '0/20'
    monkeypatch.setattr(acc, '_home', lambda: None)
    monkeypatch.setattr(acc, 'run', run)
    monkeypatch.setattr(acc, 'ocr_screen', lambda: ([['rungic42', .99, [100, 180]], [name, .99, [100, 400]]], 'fixture.png'))
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


# covers: delivery.acceptance/E1
@pytest.mark.parametrize('words,passed', [
    ([['rungic42', .99, [100, 180]], ['计算器', .99, [100, 400]]], True),
    ([['RUNGIC42', .99, [100, 180]]], True),
    ([['rungic42', .99, [100, 400]]], False),
    ([['rungic42x', .99, [100, 180]]], False),
    ([['Calculator', .99, [100, 180]]], False),
])
def test_input_requires_complete_test_text_in_field_not_an_app_label(monkeypatch, words, passed):
    field = {'path':'0/1', 'extents':[26,56,301,43], 'states':['focused']}
    monkeypatch.setattr(acc, '_drawer_search', lambda: field)
    monkeypatch.setattr(acc, '_home', lambda: None)
    monkeypatch.setattr(ui, 'drawer_search_fields', lambda: [field])
    monkeypatch.setattr(acc.rungic_agent, 'a11y', lambda _: {'enabled': True})
    monkeypatch.setattr(acc.rungic_agent, 'ui_tap', lambda *a: {'scale':3, 'window':{'x':0,'y':0}})
    monkeypatch.setattr(acc, 'run', lambda *a, **k: None)
    monkeypatch.setattr(acc.time, 'sleep', lambda _: None)
    monkeypatch.setattr(acc, 'ocr_screen', lambda: (words, 'fixture.png'))
    assert acc.input_text({})['passed'] is passed


# covers: agent.dev-diagnostics/E3
@pytest.mark.parametrize('label', ['Calculator', '计算器', 'Calculatrice (Kalk)'])
def test_localized_label_is_escaped_and_does_not_define_application_identity(monkeypatch, label):
    queries=[]
    node={'path':'0/2/3', 'extents':[10,100,100,30]}
    monkeypatch.setattr(ui.rungic_agent, 'ui_find', lambda *a, **kw: queries.append(kw['name']) or [node])
    monkeypatch.setattr(ui.time, 'sleep', lambda _: None)
    assert ui.icon_for(label) == '0/2'
    import re
    assert re.fullmatch(queries[-1], label)
    assert not re.fullmatch(queries[-1], label+' extra')


# covers: agent.dev-diagnostics/E3
@pytest.mark.parametrize('change', [{}, {'pid':999}, {'resource_class':'org.kde.konsole'}, {'id':''}, {'normal':False}])
def test_app_window_requires_process_desktop_class_and_window_id(monkeypatch, change):
    entry={'id':'org.kde.kalk','classes':['org.kde.kalk','kalk']}
    window={'id':'owned-window', 'pid':123, 'normal':True, 'resource_class':'org.kde.kalk', 'caption':'计算器'}
    window.update(change)
    monkeypatch.setattr(ui, 'process_ids', lambda _: {123})
    monkeypatch.setattr(ui.rungic_agent, 'ui_windows', lambda: [window])
    assert bool(ui.application_windows(entry,'kalk')) is (not change)


# covers: agent.dev-diagnostics/E3
@pytest.mark.parametrize('reply', ['null', '{"id":"org.kde.kalk-other"}'])
def test_missing_or_partial_desktop_id_is_rejected(monkeypatch, reply):
    monkeypatch.setattr(ui, 'run', lambda *a,**kw: SimpleNamespace(stdout=reply))
    with pytest.raises(RuntimeError, match='not found exactly'):
        ui.desktop_entry('org.kde.kalk')


# covers: agent.dev-diagnostics/E3
def test_close_targets_verified_id_and_requires_window_removal(monkeypatch):
    window={'id':'owned-window'}; calls=[]
    monkeypatch.setattr(ui, 'desktop_entry', lambda _: {})
    monkeypatch.setattr(ui, 'application_windows', lambda *a: [window])
    monkeypatch.setattr(ui, 'window_action', lambda *a: calls.append(a))
    monkeypatch.setattr(ui, 'running', lambda _: False)
    monkeypatch.setattr(ui.rungic_agent, 'ui_windows', lambda: [window])
    monkeypatch.setattr(ui, 'wait_for', lambda condition,*a: bool(condition()))
    assert not ui.close('kalk')['exited']
    assert calls == [('owned-window','close')]


# covers: agent.dev-diagnostics/E3
@pytest.mark.parametrize('registered_pid,passed', [(123,True),(124,False)])
def test_launch_requires_registration_of_the_actual_window_pid(monkeypatch, registered_pid, passed):
    entry={'id':'org.kde.kalk','name':'计算器','classes':['kalk']}
    monkeypatch.setattr(ui, 'desktop_entry', lambda _: entry)
    monkeypatch.setattr(ui, 'home', lambda: None)
    monkeypatch.setattr(ui, 'open_drawer', lambda: None)
    monkeypatch.setattr(ui, 'scroll_drawer_to_top', lambda _: None)
    monkeypatch.setattr(ui, 'launcher_label', lambda _: '计算器')
    monkeypatch.setattr(ui, 'icon_for', lambda _: '0/2')
    monkeypatch.setattr(ui.rungic_agent, 'ui_tap', lambda *a: {'tap':[100,200]})
    monkeypatch.setattr(ui.rungic_agent, 'screenshot', lambda: 'wrong-pid.png')
    monkeypatch.setattr(ui, 'running', lambda _: True)
    monkeypatch.setattr(ui, 'application_windows', lambda *a: [{'id':'window','pid':123}])
    monkeypatch.setattr(ui.rungic_agent, 'a11y', lambda _: [{'name':'Kalk','pid':registered_pid}])
    monkeypatch.setattr(ui, 'wait_for', lambda condition,*a: bool(condition()))
    monkeypatch.setattr(ui.time, 'sleep', lambda _: None)
    result=ui.launch('org.kde.kalk','kalk',False)
    assert result['registered'] is passed
    assert result['desktop_id']=='org.kde.kalk' and result['label']=='计算器'
    assert ('screenshot' in result) is not passed


# covers: agent.dev-diagnostics/E3
def test_duplicate_localized_launcher_labels_refuse_tap(monkeypatch):
    node={'path':'0/2','extents':[10,100,100,30]}
    monkeypatch.setattr(ui.rungic_agent,'ui_find',lambda *a,**k:[node,dict(node,path='0/3')])
    with pytest.raises(RuntimeError,match='ambiguous'):
        ui.icon_for('计算器')


# covers: agent.dev-diagnostics/E3
@pytest.mark.parametrize('env', [
    {'LANG': 'C.UTF-8', 'LANGUAGE': 'zh_CN'},
    {'LANG': 'zh_CN.UTF-8', 'LANGUAGE': 'en', 'LC_MESSAGES': 'C.UTF-8'},
])
def test_drawer_label_comes_from_all_translations_with_conflicting_language_environment(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    entry = {'id': 'org.kde.kalk', 'name': 'Calculator', 'labels': ['calculator', '计算器']}
    node = {'path': '0/2/3', 'name': '计算器', 'extents': [10, 100, 100, 30]}
    def find(app, **query):
        assert query == {'role': 'label'}  # No locale-derived name is sent to the shell.
        return [node]
    monkeypatch.setattr(ui.rungic_agent, 'ui_find', find)
    assert ui.launcher_label(entry) == '计算器'


# covers: agent.dev-diagnostics/E3
@pytest.mark.parametrize('other_name', ['计算器', 'Calculator'])
def test_two_applications_matching_translated_candidates_are_ambiguous(monkeypatch, other_name):
    entry = {'id': 'org.kde.kalk', 'name': 'Calculator', 'labels': ['calculator', '计算器']}
    first = {'path': '0/2/3', 'name': '计算器', 'extents': [10, 100, 100, 30]}
    second = dict(first, path='0/4/3', name=other_name)
    monkeypatch.setattr(ui.rungic_agent, 'ui_find', lambda *a, **k: [first, second])
    with pytest.raises(RuntimeError, match='ambiguous'):
        ui.launcher_label(entry)
