# SPDX-License-Identifier: MIT
"""desktop_find_name (plan two, docs/60): a name that speech recognition wrote with the wrong characters
is found by how it sounds, with the system's pypinyin (the rootfs's python3-pypinyin, which the
development venv lacks; hence here). Same sound 1.0, accent-type confusions 0.9; each match carries
the heading of its list (contacts or search suggestions); two different people at 0.9 or more are
both returned, for the agent to ask the user (the tool's description says so). The window's
accessibility snapshot is a stand-in of what WeChat's search popup shows; no display is needed.
arc_cua (plan two's executor, not shipped to the container) is an empty module."""
import sys
from pathlib import Path
from types import SimpleNamespace

import harness


def stubs():
    root = Path('/tmp/cua-find-name-stubs')
    (root / 'arc_cua').mkdir(parents=True, exist_ok=True)
    (root / 'arc_cua/__init__.py').write_text('class DesktopExecutor: pass\nclass RuntimeConfig: pass\n'
                                              'def result_to_dict(r): return r\ndef subtask_from_dict(d): return d\n')
    (root / 'arc_cua/policies.py').write_text('class TypeSafeJevPolicy: pass\n')
    (root / 'arc_cua/errors.py').write_text('class StaleDesktopState(Exception): pass\n'
                                            'class UnsupportedDesktopAction(Exception): pass\n')
    (root / 'arc_cua/keyboard.py').write_text('def parse_hotkey(t): return [], t\n')
    (root / 'arc_cua/models.py').write_text('class ActionKind: pass\nclass DesktopElement: pass\n'
                                            'class DesktopSnapshot: pass\nclass ExecutableAction: pass\n')
    sys.path.insert(0, str(root))


def element(n, name, parent, role='list item'):
    return SimpleNamespace(id=f'e{n}', name=name, parent_id=parent, role=role)


# covers[system]: agent.plan-two/E4
def test():
    stubs()
    from rungic_cua import names, server
    from rungic_cua import mode
    mode.PLAN_FILE = Path('/tmp/cua-find-name-plan')
    mode.save('atspi')          # rungic-cua plan atspi
    steps = []

    def check(condition, what):
        steps.append(what)
        if not condition:
            raise harness.Failed(what)

    check(names.score('周凯文', '周楷雯') == 1.0 and names.score('周凯文', '周恺文') == 1.0,
          'the same sound with other characters scores 1.0')
    check(names.score('周凯文', '邹凯文') == 0.9 and names.score('刘宁', '牛玲') == 0.9,
          'accent-type confusions (zh/z, l/n, in/ing) score 0.9')
    check(names.score('周凯文', '王小明') < 0.5, 'another name scores low')
    check(names.search_text('周凯文') == 'zhoukaiwen', 'what to type into the search field is the pinyin')
    check(names.rank('周凯文', ['王小明', '周楷雯', '邹凯文'])[:2] == [('周楷雯', 1.0), ('邹凯文', 0.9)],
          'candidates are ranked by sound')

    snapshot = SimpleNamespace(window='微信', elements=(
        element(1, '联系人', 'contacts', 'label'),
        element(2, '周楷雯', 'contacts'),
        element(3, '邹凯文\n上次聊天：昨天', 'contacts'),
        element(4, '王小明', 'contacts'),
        element(5, '搜索网络结果', 'web', 'label'),
        element(6, '周凯文 是谁', 'web')))
    cua = server.Cua()
    cua._backend = SimpleNamespace(observe=lambda: snapshot, set_accessibility=lambda on: None)
    found = cua.call('desktop_find_name', {'name': '周凯文'})
    by_name = {m['name']: m for m in found['matches']}
    check(found['search_text'] == 'zhoukaiwen' and found['window'] == '微信', 'the tool gives the pinyin to search for')
    check(by_name['周楷雯']['score'] == 1.0 and by_name['周楷雯']['elements'][0]['section'] == '联系人',
          'the contact who sounds the same is found, under its list heading "联系人"')
    check(by_name['邹凯文']['score'] == 0.9 and by_name['邹凯文']['elements'][0]['section'] == '联系人',
          'a near sound is found too, on the first line of a two-line entry')
    check(by_name['周凯文 是谁']['elements'][0]['section'] == '搜索网络结果',
          'a search suggestion carries its own heading, telling it apart from people')
    sure = [m['name'] for m in found['matches'] if m['score'] >= 0.9 and m['elements'][0]['section'] == '联系人']
    check(len(sure) == 2, 'two different people at 0.9 or more are both returned')
    tool = next(t for t in server.tools_for('atspi') if t['name'] == 'desktop_find_name')
    check('ask the user which one' in tool['description'], 'and the tool tells the agent to ask the user which one')
    return steps


if __name__ == '__main__':
    harness.run('cua_find_name', test)
