"""The agent's diagnostic tools without the phone (docs/55): which MCP tools say they are read-only,
and how ui_tap turns an AT-SPI element without actions into an Android tap."""
import ast
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rungic_agent  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# Tools that change something: operate controls, switch accessibility, record a trace (it toggles
# KWin's markers and tracefs), install symbols, write an evidence bundle.
# ui_apps and ui_find turn accessibility on when it is off: a change of the device's state (E5).
CHANGING = {'ui_press', 'ui_tap', 'ui_set_text', 'ui_accessibility', 'ui_apps', 'ui_find', 'trace', 'crash_symbolize', 'snapshot'}


def mcp_tools(monkeypatch):
    """Import the real rungic_agent_mcp with stand-ins for the MCP SDK (and perfetto, used by the
    trace report), recording each tool's annotations as the server would publish them."""
    tools = {}

    class Server:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self, annotations):
            def register(fn):
                tools[fn.__name__] = annotations
                return fn
            return register

    annotations = lambda **hints: types.SimpleNamespace(**hints)
    stand_ins = {'mcp': types.ModuleType('mcp'), 'mcp.server': types.ModuleType('mcp.server'),
                 'mcp.server.mcpserver': types.SimpleNamespace(Image=object, MCPServer=Server),
                 'mcp.types': types.SimpleNamespace(ToolAnnotations=annotations)}
    try:
        import perfetto.trace_processor  # noqa: F401
    except ImportError:
        stand_ins['perfetto'] = types.ModuleType('perfetto')
        stand_ins['perfetto.trace_processor'] = types.SimpleNamespace(TraceProcessor=object, TraceProcessorConfig=object)
    for name, module in stand_ins.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, 'rungic_agent_mcp', raising=False)
    monkeypatch.setattr(sys, 'pycache_prefix', sys.pycache_prefix)     # the server sets its own on import
    import rungic_agent_mcp  # noqa: F401
    return tools


# covers: delivery.agent-diagnostics/E5
def test_read_only_tools_are_marked_and_changes_are_apart(monkeypatch):
    tools = mcp_tools(monkeypatch)
    declared = {node.name for node in ast.parse((ROOT / 'tools/rungic_agent_mcp.py').read_text()).body
                if isinstance(node, ast.FunctionDef) and node.decorator_list}
    assert set(tools) == declared and CHANGING <= declared
    for name, hints in tools.items():
        assert hints.read_only_hint is (name not in CHANGING), name
        assert hints.destructive_hint is False and hints.open_world_hint is False
        assert hints.idempotent_hint is (name not in CHANGING), name
    # The read-only host request refuses the bridge's setters before reaching the phone.
    for op in ('display-set', 'brightness-set', 'network-set'):
        with pytest.raises(ValueError):
            rungic_agent.host_request(op)


# covers: delivery.ui-automation/E3
def test_ui_tap_maps_the_element_centre_to_physical_pixels(monkeypatch):
    """Kalk's keypad draws its own keys (no AT-SPI action): the tap goes to the window origin from KWin
    plus the element's centre in window coordinates, times physical / logical width."""
    answers = {
        ('apps',): [{'name': 'kalk', 'pid': 4242, 'windows': 1}, {'name': 'plasmashell', 'pid': 99, 'windows': 3}],
        ('tree', 'kalk', '--all'): [
            {'path': '0', 'role': 'frame', 'name': 'Kalk', 'extents': [0, 0, 360, 700]},
            {'path': '0/3/7', 'role': 'label', 'name': '7', 'extents': [10, 400, 80, 60]}],
        ('windows',): [
            {'pid': 99, 'x': 0, 'y': 0, 'w': 360, 'h': 40},                 # the panel
            {'pid': 4242, 'x': 0, 'y': 0, 'w': 200, 'h': 100},              # another window of kalk, other size
            {'pid': 4242, 'x': 0, 'y': 40, 'w': 360, 'h': 700}],
    }
    taps = []
    monkeypatch.setattr(rungic_agent, 'a11y', lambda *args, **kw: answers[args])
    monkeypatch.setattr(rungic_agent, 'host_request', lambda op: {'physicalWidth': 1080} if op == 'display-get' else None)
    monkeypatch.setattr(rungic_agent, 'run', lambda script, level='root', **kw: taps.append((script, level)))
    result = rungic_agent.ui_tap('kalk', '0/3/7')
    # centre (50, 430) in the window, window at (0, 40), scale 1080 / 360 = 3
    assert taps == [('input tap 150 1410', 'shell')]
    assert result['tap'] == [150, 1410] and result['scale'] == 3.0

    # An element outside its window is not tapped somewhere else.
    answers[('tree', 'kalk', '--all')][1]['extents'] = [10, 800, 80, 60]
    with pytest.raises(ValueError, match='not on screen'):
        rungic_agent.ui_tap('kalk', '0/3/7')
    assert len(taps) == 1


# covers: delivery.ui-automation/E4
def test_touches_use_screenshot_pixels_and_refuse_what_input_would_misread(monkeypatch):
    """An agent reading a screenshot touches at its pixels: tap, swipe and key become Android input
    commands as they are; text is ASCII with spaces as %s; exec reports a failing command's status."""
    calls = []
    monkeypatch.setattr(rungic_agent, 'run', lambda script, level='root', timeout=60, check=True:
                        calls.append((script, level)) or types.SimpleNamespace(returncode=3, stdout='o', stderr='e'))
    rungic_agent.tap(540, '1200')
    rungic_agent.swipe(100, 2000, 100, 2000, ms=800)      # a long press
    rungic_agent.key('back')
    rungic_agent.text('http://10.0.0.1/a b')
    assert calls == [('input tap 540 1200', 'shell'), ('input swipe 100 2000 100 2000 800', 'shell'),
                     ('input keyevent BACK', 'shell'), ('input text http://10.0.0.1/a%sb', 'shell')]
    for bad in (lambda: rungic_agent.tap(-1, 5), lambda: rungic_agent.key('BACK; reboot'),
                lambda: rungic_agent.text('你好'), lambda: rungic_agent.text(''),
                lambda: rungic_agent.execute('true', level='kernel')):
        with pytest.raises(ValueError):
            bad()
    assert len(calls) == 4
    assert rungic_agent.execute('sha256sum f') == {'exit': 3, 'stdout': 'o', 'stderr': 'e'}
    assert calls[-1] == ('sha256sum f', 'user')


def keyboard_phone(monkeypatch, pages):
    """A phone whose plasma-keyboard shows pages[state['page']]; a touch on a page key switches page.
    The panel ends 36 logical px above the output's bottom (the navigation bar), as on the G100."""
    state = {'page': 'lower', 'touches': []}

    def at(x, y):     # screenshot pixels back to the page's key, through the same placement
        for ident, name, (kx, ky) in pages[state['page']]:
            if abs((kx + 10) * 3 - x) <= 1 and abs((ky + 10 - 36) * 3 - y) <= 1:
                return ident, name
        raise AssertionError(f'touch at {x},{y} hits no key')

    def a11y(*args, **kw):
        if args == ('apps',):
            return [{'name': 'plasma-keyboard', 'pid': 7}]
        keys = [{'path': f'0/{i}', 'id': ident, 'name': name, 'states': ['showing', 'enabled'],
                 'extents': [x, y, 20, 20]} for i, (ident, name, (x, y)) in enumerate(pages[state['page']])]
        return [{'path': '0', 'id': 'QGuiApplication.InputPanelWindow', 'extents': [0, 0, 360, 785]}] + keys

    def run(script, level='root', **kw):
        for line in script.splitlines():
            x, y = map(int, line.split()[2:])
            ident, name = at(x, y)
            state['touches'].append(name)
            state['page'] = {'shift': 'upper' if state['page'] == 'lower' else 'lower', 'symbol': 'symbols'
                             if state['page'] != 'symbols' else 'lower'}.get(ident, 'lower' if state['page'] == 'upper' else state['page'])
    monkeypatch.setattr(rungic_agent, 'a11y', a11y)
    monkeypatch.setattr(rungic_agent, 'ui_windows', lambda: [
        {'pid': 1, 'resource_class': 'plasmashell', 'normal': False, 'x': 0, 'y': 749, 'w': 360, 'h': 36},
        {'pid': 2, 'resource_class': 'kwin_wayland', 'normal': False, 'x': 0, 'y': 513, 'w': 360, 'h': 236}])
    monkeypatch.setattr(rungic_agent, 'host_request', lambda op: {'physicalWidth': 1080})
    monkeypatch.setattr(rungic_agent, 'run', run)
    monkeypatch.setattr(rungic_agent.time, 'sleep', lambda s: None)
    rungic_agent._placement.clear()
    return state


# covers: delivery.ui-automation/E5
def test_keyboard_type_touches_the_keys_a_user_would(monkeypatch):
    """Shift for a capital (one shot), the symbol page for digits, keys found by identifier; the
    panel above the navigation bar moves every key up by its 36 px."""
    letters = [(f'key:{c}', c, (20 * i, 600)) for i, c in enumerate('abg')]
    common = [('shift', 'Shift', (0, 700)), ('symbol', '&123', (40, 700)), ('space', 'Space', (80, 700)),
              ('enter', 'Enter', (120, 700))]
    pages = {'lower': letters + common,
             'upper': [(i, n.upper(), p) for i, n, p in letters] + common,
             'symbols': [('key:1', '1', (0, 600)), ('key:2', '2', (20, 600))] + common}
    state = keyboard_phone(monkeypatch, pages)
    assert rungic_agent.keyboard_type('Ab 12\n') == {'typed': 6}
    assert state['touches'] == ['Shift', 'A', 'b', 'Space', '&123', '1', '2', 'Enter']
    with pytest.raises(ValueError, match='no key types'):
        rungic_agent.keyboard_type('é')


# covers: delivery.ui-automation/E5
def test_keyboard_pinyin_picks_the_shown_candidate_by_its_text(monkeypatch):
    pinyin = [(f'key:{c}', c, (20 * i, 600)) for i, c in enumerate('nihao')]
    pages = {'lower': pinyin + [('candidate', '你好', (0, 560)), ('candidate', '妳好', (40, 560))]}
    state = keyboard_phone(monkeypatch, pages)
    rungic_agent.keyboard_pinyin('nihao', '你好')
    assert state['touches'] == list('nihao') + ['你好']
    with pytest.raises(ValueError, match='no candidate'):
        rungic_agent.keyboard_pinyin('ni', '泥')
