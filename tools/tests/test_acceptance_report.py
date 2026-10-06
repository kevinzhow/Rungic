# SPDX-License-Identifier: MIT
"""The actual acceptance runner preserves its plan and distinguishes proof from missing work."""
import json
import types
from pathlib import Path

import pytest
import rungic_acceptance as acc
import rungic_release as release_tool


@pytest.fixture
def runner(tmp_path, monkeypatch):
    spec = {'scenarios': [], 'manual': ['Listen to a real recording', 'Check image quality']}
    monkeypatch.setattr(acc, 'RESULTS', tmp_path)
    monkeypatch.setattr(acc, 'load', lambda: spec)
    monkeypatch.setattr(acc, 'bring_to_front', lambda: {'was_in_front': False, 'in_front': True})
    monkeypatch.setattr(acc, 'previous_report', lambda *args: (None, None))
    monkeypatch.setattr(acc.rungic_agent, 'screenshot', lambda: (_ for _ in ()).throw(RuntimeError('no screenshot')))
    observed = []
    def drift(against='origin/main'):
        observed.append(against)
        return {'release': 'installed.1', 'commit': 'installed-sha', 'in_sync': False,
                'differs': [{'part': 'apk', 'state': 'version differs'}], 'against': against,
                'installed_commit': 'installed-full-sha', 'apk': {'version_code': 1}}
    monkeypatch.setattr(release_tool, 'phone_drift', drift)
    def run(script, *args, **kwargs):
        text = {'getprop ro.serialno': 'TEST-PHONE\n', 'getprop ro.build.fingerprint': 'test/fingerprint\n',
                'dumpsys battery': '  AC powered: false\n  USB powered: true\n  Wireless powered: false\n  level: 76\n',
                'dumpsys power': 'mWakefulness=Awake\n'}.get(script, '')
        return types.SimpleNamespace(stdout=text, returncode=0)
    monkeypatch.setattr(acc, 'run', run)
    monkeypatch.setitem(acc.CHECKS, 'good', lambda ctx: acc.result(True, {'latency': 3}))
    monkeypatch.setitem(acc.CHECKS, 'bad', lambda ctx: acc.result(False, error='visible failure'))
    def scenario(id, check='good', level='smoke'):
        return {'id': id, 'title': id, 'check': check, 'level': level}
    return types.SimpleNamespace(spec=spec, scenario=scenario, path=tmp_path / 'run', observed=observed)


# covers: delivery.acceptance/E6
@pytest.mark.parametrize('check,skips,verdict,status', [
    ('good', {}, 'pass', 'pass'), ('bad', {}, 'fail', 'fail'),
    ('good', {'one': 'camera unavailable'}, 'incomplete', 'skipped'),
    ('not_implemented', {}, 'incomplete', 'unimplemented')])
def test_verdict_distinguishes_executed_results_from_missing_work(runner, check, skips, verdict, status):
    report = acc.run_scenarios([runner.scenario('one', check)], out_dir=runner.path, skips=skips)
    assert report['verdict'] == verdict
    assert report['scenarios'][0]['status'] == status
    if skips:
        assert report['passed'] is True and report['complete'] is False  # rollback compatibility
    assert report['manual'] == runner.spec['manual']


# covers: delivery.acceptance/E6
@pytest.mark.parametrize('after_one', [False, True])
def test_interrupt_keeps_the_whole_plan_and_does_not_overwrite_passed_rows(runner, monkeypatch, after_one):
    def stop(ctx):
        raise KeyboardInterrupt()
    monkeypatch.setitem(acc.CHECKS, 'stop', stop)
    scenarios = ([runner.scenario('first')] if after_one else []) + [runner.scenario('interrupted', 'stop'), runner.scenario('later')]
    with pytest.raises(KeyboardInterrupt):
        acc.run_scenarios(scenarios, out_dir=runner.path)
    saved = json.loads((runner.path / 'report.json').read_text())
    assert saved['verdict'] == 'incomplete'
    assert [r['id'] for r in saved['scenarios']] == [s['id'] for s in scenarios]
    assert saved['scenarios'][-1]['status'] == 'not-run'
    if after_one:
        assert saved['scenarios'][0]['status'] == 'pass'


# covers: delivery.acceptance/E6
def test_report_exists_before_device_setup_can_fail(runner, monkeypatch):
    monkeypatch.setattr(acc, 'bring_to_front', lambda: (_ for _ in ()).throw(RuntimeError('device disconnected')))
    with pytest.raises(RuntimeError, match='device disconnected'):
        acc.run_scenarios([runner.scenario('one')], out_dir=runner.path)
    saved = json.loads((runner.path / 'report.json').read_text())
    assert saved['verdict'] == 'incomplete' and saved['scenarios'][0]['status'] == 'not-run'
    assert 'device disconnected' in saved['run_error']


# covers: delivery.acceptance/E6
def test_second_run_cannot_destroy_an_existing_report(runner):
    runner.path.mkdir()
    original = '{"original": true}\n'
    (runner.path / 'report.json').write_text(original)
    with pytest.raises(FileExistsError):
        acc.run_scenarios([runner.scenario('one')], out_dir=runner.path)
    assert (runner.path / 'report.json').read_text() == original


# covers: delivery.acceptance/E6
def test_report_records_actual_device_and_pinned_comparison_without_fetching(runner):
    report = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path)
    assert report['system']['release'] == 'installed.1'
    assert report['system']['in_sync'] is False
    assert len(runner.observed) == 1 and len(runner.observed[0]) == 40
    assert report['system']['against'] == runner.observed[0]
    assert report['device']['serial'] == 'TEST-PHONE'
    assert report['device']['fingerprint'] == 'test/fingerprint'
    assert report['device']['battery']['charging'] is True
    assert report['device']['battery']['level'] == 76
    assert report['device']['screen'] == 'Awake'


# R07-R09: full includes actual human observations; smoke explicitly excludes them.
# covers: delivery.acceptance/E6
@pytest.mark.parametrize('scope,manual,verdict', [
    ('smoke', {}, 'pass'), ('full', {}, 'incomplete'),
    ('full', {'manual.1': {'status': 'pass', 'note': 'heard real speech'},
              'manual.2': {'status': 'pass', 'note': 'viewed captured image'}}, 'pass'),
    ('full', {'manual.1': {'status': 'fail', 'note': 'speech inaudible'}}, 'fail')])
def test_full_requires_human_results_but_smoke_does_not_claim_them(runner, scope, manual, verdict):
    report = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path,
                               scope=scope, manual_results=manual)
    assert report['verdict'] == verdict
    assert len(report['manual_results']) == (2 if scope == 'full' else 0)
    assert report['manual'] == runner.spec['manual']
    for id, observation in manual.items():
        assert next(r for r in report['manual_results'] if r['id'] == id)['note'] == observation['note']


# R10/R22: invalid input cannot create a report or touch a device.
# covers: delivery.acceptance/E6
@pytest.mark.parametrize('manual', [
    {'unknown': {'status': 'pass', 'note': 'observed'}},
    {'manual.1': {'status': 'pass', 'note': ''}},
    {'manual.1': {'status': 'invalid', 'note': 'observed'}}])
def test_invalid_manual_input_is_rejected_before_any_execution(runner, manual):
    with pytest.raises(ValueError):
        acc.run_scenarios([runner.scenario('one')], out_dir=runner.path, scope='full', manual_results=manual)
    assert not (runner.path / 'report.json').exists()
    assert not runner.observed


# R20: failed collection stays unknown and never means a zero battery or a matching version.
# covers: delivery.acceptance/E6
def test_metadata_failure_keeps_results_and_records_unknown_environment(runner, monkeypatch):
    def offline(*args, **kwargs):
        raise RuntimeError('phone offline')
    monkeypatch.setattr(acc, 'run', offline)
    monkeypatch.setattr(release_tool, 'phone_drift', offline)
    report = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path)
    assert report['verdict'] == 'incomplete'
    assert report['scenarios'][0]['status'] == 'pass'
    assert report['device'] == {}
    assert 'in_sync' not in report['system']
    assert 'phone offline' in report['metadata_errors']['device']


# R01-R06: actual CLI exit codes use the QA verdict, keeping passed for deploy compatibility.
# covers: delivery.acceptance/E6
@pytest.mark.parametrize('check,skip,exit_code', [('good', [], 0), ('bad', [], 1),
                                               ('good', ['one=excluded'], 2), ('missing', [], 2)])
def test_cli_exit_uses_verdict(runner, monkeypatch, check, skip, exit_code):
    runner.spec['scenarios'] = [runner.scenario('one', check)]
    monkeypatch.setattr('sys.argv', ['acceptance', 'smoke', '--release', 'cli-fixture'] +
                        [arg for entry in skip for arg in ('--skip', entry)])
    assert acc.main() == exit_code


# R10/R22
# covers: delivery.acceptance/E6
@pytest.mark.parametrize('arguments', [
    ['run', 'UNKNOWN'], ['full', '--manual', 'manual.1=pass:observed', '--manual', 'manual.1=fail:other'],
    ['full', '--manual', 'manual.1=pass'], ['full', '--manual', 'manual.1=invalid:observed']])
def test_cli_rejects_unknown_and_contradictory_input(runner, monkeypatch, arguments):
    runner.spec['scenarios'] = [runner.scenario('one')]
    monkeypatch.setattr('sys.argv', ['acceptance', *arguments])
    with pytest.raises(SystemExit) as error:
        acc.main()
    assert error.value.code == 2
    assert not runner.observed
    assert not list(runner.path.parent.rglob('report.json'))


# R20
# covers: delivery.acceptance/E6
def test_empty_device_metadata_is_unknown_with_collection_errors(runner, monkeypatch):
    monkeypatch.setattr(acc, 'run', lambda *args, **kwargs: types.SimpleNamespace(stdout='', returncode=0))
    report = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path)
    assert report['device']['battery']['level'] is None
    assert report['device']['battery']['charging'] is None
    assert report['verdict'] == 'incomplete'
    assert report['metadata_errors']['device']


# R03/R04/R06/R22
# covers: delivery.acceptance/E6
def test_failures_take_priority_over_missing_work_and_exceptions_are_failures(runner, monkeypatch):
    def crash(ctx):
        raise RuntimeError('broken check')
    monkeypatch.setitem(acc.CHECKS, 'crash', crash)
    report = acc.run_scenarios([runner.scenario('A', 'crash'), runner.scenario('B')],
                               out_dir=runner.path, scope='full', skips={'B': 'excluded'})
    assert report['verdict'] == 'fail'
    assert report['counts']['fail'] == 1 and report['counts']['skipped'] == 1
    assert 'broken check' in report['scenarios'][0]['details']['error']
    all_skipped = acc.run_scenarios([runner.scenario('C')], out_dir=runner.path.parent / 'skips', skips={'C': 'excluded'})
    assert all_skipped['verdict'] == 'incomplete'
    with pytest.raises(ValueError, match='no scenarios'):
        acc.run_scenarios([], out_dir=runner.path.parent / 'empty')
    assert not (runner.path.parent / 'empty').exists()


# R13
# covers: delivery.acceptance/E6
def test_interrupt_during_environment_collection_still_has_a_plan(runner, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(release_tool, 'phone_drift', interrupted)
    with pytest.raises(KeyboardInterrupt):
        acc.run_scenarios([runner.scenario('one')], out_dir=runner.path)
    saved = json.loads((runner.path / 'report.json').read_text())
    assert saved['state'] == 'interrupted' and saved['verdict'] == 'incomplete'
    assert saved['counts']['not-run'] == 1


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    root = tmp_path / 'catalog'
    (root / 'quality/features').mkdir(parents=True)
    (root / 'release').mkdir()
    (root / 'quality/features/example.yaml').write_text('''area: example
title: 日常使用
scenarios:
  - {id: first-install, title: 首次安装进入桌面}
  - {id: typing, title: 输入文字}
  - {id: old-rom, title: 旧整包历史}
features:
  - id: typing.android
    title: 用安卓输入法往桌面里打字
    scenario: typing
    status: live
    interfaces: [host-input]
    experience:
      - {id: E1, text: 文字到达获得焦点的输入框}
  - {id: first.install, title: 初始化, scenario: first-install, status: live}
  - {id: old.install, title: 旧安装, scenario: old-rom, status: retired}
''')
    (root / 'quality/interfaces.yaml').write_text('- {id: host-input, title: 宿主输入}\n')
    (root / 'release/history.json').write_text(json.dumps([
        {'version': 'installed.1', 'serial': 'TEST-PHONE', 'result': 'verify-failed', 'time': '2020-01-01T00:00:00+00:00'},
        {'version': 'installed.1', 'serial': 'OTHER-PHONE', 'result': 'ok', 'time': 'other'}]))
    runner_spec = {'scenarios': [
        {'id': 'one', 'title': 'text check', 'level': 'smoke', 'check': 'good', 'covers': ['typing.android/E1']},
        {'id': 'other', 'title': 'other check', 'level': 'full', 'check': 'good', 'covers': ['typing.android/E1']}],
        'manual': ['真人音质']}
    (root / 'release/acceptance.json').write_text(json.dumps(runner_spec))
    monkeypatch.setattr(acc.rungic_device, 'WORKSPACE', root)
    monkeypatch.setattr(acc, 'load', lambda: runner_spec)
    return root


# R21: JSON and readable output stay aligned for pass/fail/missing/manual/interruption.
# covers: delivery.acceptance/E6
@pytest.mark.parametrize('check,scope,skips', [('good', 'smoke', {}), ('bad', 'full', {}),
                                             ('good', 'full', {}), ('good', 'smoke', {'one': 'no camera'})])
def test_render_explains_user_value_scope_and_saved_results_without_touching_device(runner, catalog, monkeypatch,
                                                                                  check, scope, skips):
    report = acc.run_scenarios([runner.scenario('one', check)], out_dir=runner.path, scope=scope, skips=skips)
    source = (runner.path / 'report.json').read_bytes()
    monkeypatch.setattr(acc, 'run', lambda *args, **kwargs: pytest.fail('render touched device'))
    path = acc.render_report(runner.path / 'report.json')
    rendered = path.read_text()
    assert (runner.path / 'report.json').read_bytes() == source
    assert '用安卓输入法往桌面里打字' in rendered
    assert '首次安装进入桌面' in rendered
    assert '旧整包历史' not in rendered
    assert '真人音质' in rendered
    assert 'TEST-PHONE' in rendered and 'test/fingerprint' in rendered
    assert 'verify-failed' in rendered and 'OTHER-PHONE' not in rendered
    assert report['verdict'] in rendered  # machine verdict stays traceable in the appendix
    if skips:
        assert 'no camera' in rendered and '跳过' in rendered
    if scope == 'full':
        assert '待人工' in rendered


# R15/R17/R21: render the last attempt and retain the first failure; no evidence changes.
# covers: delivery.acceptance/E6
@pytest.mark.parametrize('retry_check,retry_skips', [('good', {}), ('good', {'one': 'excluded'}), ('bad', {})])
def test_retry_renderer_reads_initial_failure_and_handles_missing_file(runner, catalog, retry_check, retry_skips):
    first = acc.run_scenarios([runner.scenario('one', 'bad')], out_dir=runner.path)
    second_dir = runner.path.parent / 'retry'
    second = acc.run_scenarios([runner.scenario('one', retry_check)], out_dir=second_dir, skips=retry_skips,
                              retry_of='../run/report.json')
    first_bytes = Path(first['path']).read_bytes()
    second_bytes = Path(second['path']).read_bytes()
    rendered = acc.render_report(second['path']).read_text()
    assert '首次' in rendered and '重试' in rendered and 'visible failure' in rendered
    assert Path(first['path']).read_bytes() == first_bytes and Path(second['path']).read_bytes() == second_bytes
    if retry_check == 'good' and not retry_skips:
        assert '不稳定' in rendered
    Path(first['path']).unlink()
    assert '首次报告缺失' in acc.render_report(second['path']).read_text()


# R21: real CLI rendering works in another process and preserves source bytes.
# covers: delivery.acceptance/E6
def test_render_cli(runner):
    import subprocess
    import sys
    report = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path)
    source = Path(report['path']).read_bytes()
    rendered = subprocess.run([sys.executable, str(Path(acc.__file__)), 'render', report['path']],
                              capture_output=True, text=True, timeout=30)
    assert rendered.returncode == 0, rendered.stderr
    assert (runner.path / 'report.md').exists()
    assert Path(report['path']).read_bytes() == source


# R21: interruption must retain its cause in the human report, not just a partial count.
# covers: delivery.acceptance/E6
def test_renderer_shows_interruption_reason_and_full_manual_observations(runner, catalog, monkeypatch):
    def stop(ctx):
        raise KeyboardInterrupt('operator stopped run')
    monkeypatch.setitem(acc.CHECKS, 'stop', stop)
    with pytest.raises(KeyboardInterrupt):
        acc.run_scenarios([runner.scenario('one', 'stop')], out_dir=runner.path)
    rendered = acc.render_report(runner.path / 'report.json').read_text()
    assert 'operator stopped run' in rendered and '未执行' in rendered
    manual_dir = runner.path.parent / 'human'
    manual = {'manual.1': {'status': 'fail', 'note': '真实语音听不清'}}
    report = acc.run_scenarios([runner.scenario('one')], out_dir=manual_dir, scope='full', manual_results=manual)
    rendered = acc.render_report(report['path']).read_text()
    assert '真实语音听不清' in rendered and 'manual.1' in rendered and '人工失败' in rendered


# Honest metrics and scope: collecting timings alone does not prove absence of regression.
# covers: delivery.acceptance/E6
def test_renderer_labels_missing_performance_reference_and_plan_scope(runner):
    report = acc.run_scenarios([runner.scenario('perf.compositor')], out_dir=runner.path, scope='full',
        manual_results={f'manual.{i}': {'status': 'pass', 'note': 'observed'} for i in (1, 2)})
    rendered = acc.render_report(report['path']).read_text()
    assert '无参考，未比较' in rendered
    assert '本次完整检查计划通过' in rendered
    assert '不包含首次安装、整机重启、长时间待机' in rendered


# covers: delivery.acceptance/E6
@pytest.mark.parametrize('states,manual,state,errors', [
    (['pass'], [], 'finished', {}), (['fail', 'not-run'], [], 'interrupted', {}),
    (['pass'], [{'id': 'human', 'status': 'not-run'}], 'finished', {}),
    (['pass'], [{'id': 'human', 'status': 'fail'}], 'finished', {}),
    (['skipped'], [], 'finished', {}), (['pass'], [], 'interrupted', {}),
    (['pass'], [], 'finished', {'device': 'offline'}), ([], [], 'finished', {})])
def test_attempt_and_combined_verdict_have_one_policy(states, manual, state, errors):
    report = {'scope': 'full', 'state': state, 'metadata_errors': errors, 'manual_results': manual,
              'scenarios': [{'id': str(i), 'status': status, 'passed': {'pass': True, 'fail': False}.get(status)}
                            for i, status in enumerate(states)]}
    acc.report_summary(report)
    combined = acc.combine([(Path('report.json'), report)])
    assert combined['verdict'] == report['verdict']
    assert combined['reasons'] == report['reasons']
    assert combined['counts'] == report['counts']


# covers: delivery.acceptance/E6
def test_combination_retains_initial_conditions_and_first_plan_without_mutation():
    first = {'scope': 'smoke', 'state': 'finished', 'device': {'screen': 'Dozing', 'serial': 'TEST'},
             'system': {'release': 'fixture'}, 'metadata_errors': {'battery': 'unavailable'},
             'scenarios': [{'id': 'retry', 'status': 'fail'}, {'id': 'untouched', 'status': 'pass'},
                           {'id': 'still-fails', 'status': 'fail'}]}
    retry = {'scope': 'selected', 'state': 'finished', 'device': {'screen': 'Awake', 'serial': 'TEST'},
             'system': {'release': 'fixture'}, 'scenarios': [{'id': 'retry', 'status': 'pass'}]}
    for report in (first, retry):
        report['device']['fingerprint'] = 'test/firmware'
        report['system'].update(installed_commit='fixed-sha', apk={'version_code': 1})
    before = json.dumps([first, retry])
    combined = acc.combine([(Path('first.json'), first), (Path('retry.json'), retry)])
    assert combined['verdict'] == 'fail' and combined['counts']['fail'] == 1
    assert combined['counts']['pass'] == 2 and combined['flaky'] == ['retry']
    assert combined['observed']['untouched']['status'] == 'pass'
    assert any('首次：' in w and 'Dozing' in w for w in combined['warnings'])
    assert any('首次：' in w and 'unavailable' in w for w in combined['warnings'])
    assert not any('重试：' in w and 'Dozing' in w for w in combined['warnings'])
    assert json.dumps([first, retry]) == before
    assert '另有 1 项重试才通过' in acc.conclusion_text(combined)


# covers: delivery.acceptance/E6
def test_manual_updates_combine_by_id_and_preserve_unanswered_questions():
    first = {'scope': 'full', 'state': 'finished', 'scenarios': [{'id': 'automatic', 'status': 'pass'}],
             'manual_results': [{'id': 'manual.1', 'status': 'fail', 'note': 'first observation'},
                                {'id': 'manual.2', 'status': 'not-run', 'note': ''}]}
    update = {'state': 'finished', 'scenarios': [],
              'manual_results': [{'id': 'manual.1', 'status': 'pass', 'note': 'later observation'}]}
    for report in (first, update):
        report.update(device={'serial': 'TEST', 'fingerprint': 'test/firmware'},
                      system={'release': 'test', 'installed_commit': 'fixed-sha', 'apk': {'version_code': 1}})
    combined = acc.combine([(Path('full.json'), first), (Path('manual.json'), update)])
    assert combined['verdict'] == 'incomplete'
    assert combined['counts']['manual-pass'] == 1 and combined['counts']['manual-not-run'] == 1
    assert combined['manual'][0]['note'] == 'later observation'
    assert combined['observed']['automatic']['status'] == 'pass'


@pytest.fixture
def attempts():
    def report(rows, state='finished'):
        return {'scope': 'smoke', 'state': state, 'scenarios': rows,
                'device': {'serial': 'TEST', 'fingerprint': 'test/firmware'},
                'system': {'release': 'fixture', 'installed_commit': 'fixed-sha', 'apk': {'version_code': 1}}}
    first = report([{'id': 'A', 'status': 'fail'}, {'id': 'B', 'status': 'pass'}])
    return first, report([{'id': 'A', 'status': 'pass'}])


# covers: delivery.acceptance/E6
@pytest.mark.parametrize('state', ['not-run', 'skipped', 'unimplemented'])
def test_missing_retry_observation_cannot_erase_a_known_failure(attempts, state):
    first, retry = attempts
    retry['scenarios'][0]['status'] = state
    retry['state'] = 'interrupted' if state == 'not-run' else 'finished'
    combined = acc.combine([(Path('first.json'), first), (Path('retry.json'), retry)])
    assert combined['verdict'] == 'fail'
    assert combined['observed']['A']['status'] == 'fail'
    assert combined['counts']['fail'] == 1 and combined['counts']['pass'] == 1
    assert not combined['flaky']
    if state == 'not-run':
        assert any('重试中断' in w for w in combined['warnings'])


# covers: delivery.acceptance/E6
@pytest.mark.parametrize('field', ['serial', 'fingerprint', 'release', 'installed_commit', 'version_code'])
@pytest.mark.parametrize('missing', [False, True])
def test_different_or_unknown_installations_cannot_produce_a_combined_pass(attempts, field, missing):
    first, retry = attempts
    target = retry['device'] if field in ('serial', 'fingerprint') else retry['system']['apk'] if field == 'version_code' else retry['system']
    if missing:
        del target[field]
    else:
        target[field] = 'different'
    combined = acc.combine([(Path('first.json'), first), (Path('retry.json'), retry)])
    assert combined['verdict'] == 'fail'
    assert 'identity-errors' in combined['reasons'] and 'automatic-failure' in combined['reasons']
    assert not combined['mergeable'] and not combined['flaky']
    assert combined['observed']['A']['status'] == 'fail'
    assert combined['identity_errors']


# covers: delivery.acceptance/E6
def test_render_shows_each_phone_instead_of_claiming_the_second_inherits_the_first_result(runner, catalog):
    first = acc.run_scenarios([runner.scenario('one', 'bad'), runner.scenario('other')], out_dir=runner.path)
    second = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path.parent / 'retry', retry_of='../run/report.json')
    path = Path(second['path'])
    second['device']['serial'] = 'OTHER-PHONE'
    path.write_text(json.dumps(second))
    rendered = acc.render_report(path).read_text()
    assert '本页合并结论：未通过' in rendered
    assert '手机序列号不同（TEST-PHONE → OTHER-PHONE）' in rendered
    assert '首次（TEST-PHONE）' in rendered and '重试（OTHER-PHONE）' in rendered
    assert '不能合成一台手机的结论' in rendered


# covers: delivery.acceptance/E6
def test_human_can_complete_a_full_report_without_rerunning_automatic_checks(runner, monkeypatch):
    full = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path, scope='full')
    before = Path(full['path']).read_bytes()
    monkeypatch.setattr(acc, 'run', lambda *args, **kwargs: pytest.fail('manual update touched phone'))
    results = {f'manual.{i}': {'status': 'pass', 'note': f'real observation {i}'} for i in (1, 2)}
    report = acc.manual_report(full['path'], results, out_dir=runner.path.parent / 'human')
    assert not report['scenarios'] and report['kind'] == 'manual'
    assert (Path(report['path']).parent / report['retry_of']).resolve() == Path(full['path']).resolve()
    attempts, warnings = acc.read_attempts(Path(report['path']))
    assert acc.combine(attempts, warnings)['verdict'] == 'pass'
    assert Path(full['path']).read_bytes() == before
    rendered = acc.render_report(report['path']).read_text()
    assert '本次完整检查计划通过' in rendered
    assert '没有重新采集手机状态' in rendered
    assert 'real observation 1' in rendered


# covers: delivery.acceptance/E6
@pytest.mark.parametrize('scope,results', [('smoke', {'manual.1': {'status': 'pass', 'note': 'heard'}}),
                                          ('full', {}), ('full', {'unknown': {'status': 'pass', 'note': 'heard'}})])
def test_manual_update_rejects_unknown_plan_or_empty_input_without_creating_evidence(runner, scope, results):
    full = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path, scope=scope)
    directory = runner.path.parent / 'human'
    with pytest.raises(ValueError):
        acc.manual_report(full['path'], results, out_dir=directory)
    assert not directory.exists()


# covers: delivery.acceptance/E6
def test_manual_updates_keep_all_observations_and_do_not_overwrite_report_files(runner):
    first = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path, scope='full')
    human1 = acc.manual_report(first['path'], {'manual.1': {'status': 'fail', 'note': 'inaudible speech'}}, runner.path.parent / 'human1')
    human2 = acc.manual_report(human1['path'], {'manual.1': {'status': 'pass', 'note': 'heard clearly'}}, runner.path.parent / 'human2')
    attempts, warnings = acc.read_attempts(Path(human2['path']))
    combined = acc.combine(attempts, warnings)
    assert combined['counts']['manual-pass'] == 1 and combined['counts']['manual-not-run'] == 1
    assert combined['verdict'] == 'incomplete'
    before = Path(human2['path']).read_bytes()
    with pytest.raises(FileExistsError):
        acc.manual_report(human1['path'], {'manual.2': {'status': 'pass', 'note': 'viewed image'}}, runner.path.parent / 'human2')
    assert Path(human2['path']).read_bytes() == before
    rendered = acc.render_report(human2['path']).read_text()
    assert 'inaudible speech' in rendered and 'heard clearly' in rendered


# covers: delivery.acceptance/E6
def test_manual_cli_reports_the_combined_decision_and_preserves_source_json(runner):
    import subprocess
    import sys
    full = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path, scope='full')
    before = Path(full['path']).read_bytes()
    command = [sys.executable, str(Path(acc.__file__)), 'manual', full['path'], '--out-dir', str(runner.path.parent / 'human'),
               '--manual', 'manual.1=pass:heard real speech', '--manual', 'manual.2=pass:viewed real image']
    executed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert executed.returncode == 0, executed.stderr
    output = json.loads(executed.stdout)
    assert output['verdict'] == 'pass' and output['attempt_verdict'] == 'incomplete'
    assert Path(full['path']).read_bytes() == before


# covers: delivery.acceptance/E6
def test_manual_update_does_not_mark_an_interrupted_automatic_run_finished(runner, monkeypatch):
    calls = []
    def interrupted(*args):
        calls.append(True)
        if len(calls) == 1:
            return None, None
        raise KeyboardInterrupt('interrupted after automatic observation')
    monkeypatch.setattr(acc, 'previous_report', interrupted)
    with pytest.raises(KeyboardInterrupt):
        acc.run_scenarios([runner.scenario('one')], out_dir=runner.path, scope='full')
    results = {f'manual.{i}': {'status': 'pass', 'note': 'observed'} for i in (1, 2)}
    report = acc.manual_report(runner.path / 'report.json', results, out_dir=runner.path.parent / 'human')
    assert report['state'] == 'finished'
    attempts, warnings = acc.read_attempts(Path(report['path']))
    combined = acc.combine(attempts, warnings)
    assert combined['verdict'] == 'incomplete' and combined['state'] == 'interrupted'
    assert combined['counts']['pass'] == 1 and combined['counts']['manual-pass'] == 2


# covers: delivery.acceptance/E6
def test_known_retry_failures_remain_failures_when_the_original_file_is_missing(attempts):
    _, retry = attempts
    retry['scenarios'][0]['status'] = 'fail'
    combined = acc.combine([(Path('missing.json'), {'missing': True}), (Path('retry.json'), retry)], ['first file missing'])
    assert combined['verdict'] == 'fail'
    assert combined['counts']['fail'] == 1
    assert combined['failures'][0]['serial'] == 'TEST'
    assert 'initial-missing' in combined['reasons']
    text = acc.conclusion_text(combined)
    assert '未通过' in text and '首次报告缺失' in text


# covers: delivery.acceptance/E6
def test_a_failure_on_the_second_phone_is_not_hidden_by_the_first_phones_pass(attempts):
    first, retry = attempts
    first['scenarios'][0]['status'] = 'pass'
    retry['device']['serial'] = 'SECOND-PHONE'
    retry['scenarios'][0]['status'] = 'fail'
    combined = acc.combine([(Path('first.json'), first), (Path('retry.json'), retry)])
    assert combined['verdict'] == 'fail' and combined['counts']['fail'] == 1
    assert combined['failures'][0]['serial'] == 'SECOND-PHONE'
    assert combined['failures'][0]['source'] == 'retry.json'
    assert '手机 SECOND-PHONE' in acc.conclusion_text(combined)


# covers: delivery.acceptance/E6
def test_identity_mismatch_with_only_passes_is_incomplete_not_failed(attempts):
    first, retry = attempts
    first['scenarios'][0]['status'] = 'pass'
    retry['device']['serial'] = 'SECOND-PHONE'
    combined = acc.combine([(Path('first.json'), first), (Path('retry.json'), retry)])
    assert combined['verdict'] == 'incomplete'
    assert combined['counts']['fail'] == 0
    assert not combined['failures']


# covers: delivery.acceptance/E6
@pytest.mark.parametrize('interrupt', [False, True])
def test_failed_observation_is_saved_before_screenshot_can_fail_or_be_interrupted(runner, monkeypatch, interrupt):
    def capture():
        saved = json.loads((runner.path / 'report.json').read_text())
        assert saved['scenarios'][0]['status'] == 'fail' and saved['verdict'] == 'fail'
        assert saved['scenarios'][0]['details']['screenshot_error'] == '截图未完成'
        if interrupt:
            raise KeyboardInterrupt('stopped screenshot')
        raise RuntimeError('screenshot unavailable')
    monkeypatch.setattr(acc.rungic_agent, 'screenshot', capture)
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            acc.run_scenarios([runner.scenario('one', 'bad'), runner.scenario('later')], out_dir=runner.path)
    else:
        acc.run_scenarios([runner.scenario('one', 'bad'), runner.scenario('later')], out_dir=runner.path)
    saved = json.loads((runner.path / 'report.json').read_text())
    assert saved['verdict'] == 'fail' and saved['scenarios'][0]['status'] == 'fail'
    assert '截图未完成' in saved['scenarios'][0]['details']['screenshot_error']
    assert saved['scenarios'][1]['status'] == ('not-run' if interrupt else 'pass')
    assert saved['state'] == ('interrupted' if interrupt else 'finished')


# covers: delivery.acceptance/E6
def test_successful_screenshot_adds_evidence_after_failure_is_already_saved(runner, monkeypatch):
    screenshot = runner.path.parent / 'fixture.png'
    screenshot.write_bytes(b'offline screenshot fixture')
    def capture():
        assert json.loads((runner.path / 'report.json').read_text())['scenarios'][0]['status'] == 'fail'
        return screenshot
    monkeypatch.setattr(acc.rungic_agent, 'screenshot', capture)
    report = acc.run_scenarios([runner.scenario('one', 'bad')], out_dir=runner.path)
    details = report['scenarios'][0]['details']
    assert Path(details['screenshot']).exists() and 'screenshot_error' not in details
    assert json.loads(Path(report['path']).read_text())['scenarios'][0]['details'] == details

# covers: delivery.acceptance/E6
def test_renderer_keeps_plan_execution_and_pass_counts_separate(runner, catalog):
    plan = acc.load()
    # The selected check only touches an interface; the full-only check has the user scenario.
    plan['scenarios'][0]['covers'] = ['iface:host-input']
    report = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path, scope='smoke', skips={'one': 'excluded'})
    rendered = acc.render_report(report['path']).read_text()
    assert '本次计划直接关联 0 个用户场景（共 2 个）' in rendered
    assert '完整验收另关联 1 个，本次没有运行' in rendered
    assert '其余 1 个没有本验收计划的自动检查直接关联' in rendered
    # With no planned scenario there is nothing executed to report, and the smoke note
    # only explains a passing smoke run, not this incomplete one.
    assert '其中实际执行了' not in rendered
    assert '冒烟只说明' not in rendered
    assert '首次安装进入桌面' in rendered

# covers: delivery.acceptance/E6
def test_passing_smoke_without_user_scenarios_says_it_proves_none(runner, catalog):
    plan = acc.load()
    plan['scenarios'][0]['covers'] = ['iface:host-input']
    report = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path, scope='smoke')
    rendered = acc.render_report(report['path']).read_text()
    assert '冒烟只说明系统起来了、接口通了，不说明任何用户场景可用。' in rendered

# covers: delivery.acceptance/E6
@pytest.mark.parametrize('ref', ['unknown/E1', 'typing.android/E999', 'iface:unknown'])
def test_renderer_refuses_unknown_coverage_before_writing(runner, catalog, ref):
    report = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path)
    acc.load()['scenarios'][1]['covers'] = [ref]
    with pytest.raises(ValueError, match='coverage'):
        acc.render_report(report['path'])
    assert not (runner.path / 'report.md').exists()

# covers: delivery.acceptance/E6
def test_skipped_user_check_is_planned_but_never_executed_or_passed(runner, catalog):
    report = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path, skips={'one': 'excluded'})
    rendered = acc.render_report(report['path']).read_text()
    assert '本次计划直接关联 1 个用户场景（共 2 个）' in rendered
    assert '其中实际执行了 0 个，关联检查全部通过的 0 个' in rendered

# covers: delivery.acceptance/E6
def test_mixed_checks_for_one_user_scenario_do_not_claim_all_passed(runner, catalog):
    report = acc.run_scenarios([runner.scenario('one'), runner.scenario('other', 'bad')], out_dir=runner.path)
    rendered = acc.render_report(report['path']).read_text()
    assert '其中实际执行了 1 个，关联检查全部通过的 0 个' in rendered

# covers: delivery.acceptance/E6
def test_duplicate_plan_id_is_rejected_before_rendering(runner, catalog):
    report = acc.run_scenarios([runner.scenario('one')], out_dir=runner.path)
    acc.load()['scenarios'][1]['id'] = 'one'
    with pytest.raises(ValueError, match='duplicate.*coverage'):
        acc.render_report(report['path'])


# The readable page states program errors as program reports, keeps raw output in JSON,
# and uses only the four result words for a single check.
# covers: delivery.acceptance/E6
def test_readable_page_labels_program_output_and_keeps_raw_details_in_json(runner, catalog, monkeypatch):
    def crash(ctx):
        raise RuntimeError('app drawer did not open')
    def stop(ctx):
        raise RuntimeError('Stop: first automatic criterion failed; remaining scenarios are not-run.')
    monkeypatch.setitem(acc.CHECKS, 'crash', crash)
    monkeypatch.setitem(acc.CHECKS, 'stop', stop)
    report = acc.run_scenarios([runner.scenario('one', 'crash'), runner.scenario('other', 'not_implemented')],
                               out_dir=runner.path)
    rendered = acc.render_report(report['path']).read_text()
    assert '程序报告：RuntimeError: app drawer did not open' in rendered
    assert 'Traceback' not in rendered and 'screenshot_error' not in rendered and '原始观察' not in rendered
    assert '截图：没有取得。' in rendered and '完整的程序输出在原始文件里。' in rendered
    assert 'Traceback' in json.dumps(json.loads(Path(report['path']).read_text()))
    assert '未执行（检查未实现）' in rendered and '| 检查未实现' not in rendered
    assert '| 原始文件 | 检查 ID' not in rendered and '| 检查 ID | 状态 |' in rendered  # one source: stated once above the table
    assert '本次没有人工检查结果。' in rendered and '原始报告（' in rendered
    stopped_dir = runner.path.parent / 'stopped'
    monkeypatch.setattr(acc, 'bring_to_front', lambda: (_ for _ in ()).throw(RuntimeError('Stop: device went away; retry later')))
    with pytest.raises(RuntimeError):
        acc.run_scenarios([runner.scenario('one')], out_dir=stopped_dir)
    rendered = acc.render_report(stopped_dir / 'report.json').read_text()
    warning = next(line for line in rendered.splitlines() if line.startswith('> ⚠') and '运行没有跑完' in line)
    assert '停止时还有 1 项未执行' in warning and 'Stop:' not in warning
    assert '运行停止原因（程序报告）：RuntimeError: Stop: device went away; retry later' in rendered


# Scenario titles can contain 、 themselves, so lists quote each title.
# covers: delivery.acceptance/E6
def test_interface_dependents_quote_each_scenario_title(runner, catalog):
    plan = acc.load()
    plan['scenarios'][0].update(check='interface_contract', covers=['iface:host-input'])
    acc.CHECKS.setdefault('interface_contract', lambda ctx, **kw: acc.result(True))
    report = acc.run_scenarios([runner.scenario('one', 'good')], out_dir=runner.path)
    rendered = acc.render_report(report['path']).read_text()
    assert '依赖这个接口的场景：1 个（只表示依赖，不表示已测）：「输入文字」。' in rendered

# covers: install.desktop-entry/E6 delivery.acceptance/E6
@pytest.mark.parametrize('splash,rc,expected', [('clear', 0, True), ('running 12', 0, False), ('unknown', 0, False), ('clear', 1, False)])
def test_session_requires_splash_exit_and_keeps_visible_desktop_unconfirmed(tmp_path, monkeypatch, splash, rc, expected):
    clock = [0]
    monkeypatch.setattr(acc.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(acc.time, 'sleep', lambda value: clock.__setitem__(0, clock[0] + value))
    monkeypatch.setattr(acc, 'run', lambda *a, **k: types.SimpleNamespace(
        stdout=f'env\nkwin 10\nshell 11 20\nsplash {splash}\n', returncode=rc))
    def capture(path):
        Path(path).write_bytes(b'\x89PNG\r\n\x1a\n')
        return str(path)
    monkeypatch.setattr(acc.rungic_agent, 'screenshot', capture)
    row = acc.session_ready({'out_dir': tmp_path}, timeout=6)
    assert row['passed'] is expected
    if expected:
        assert row['details']['visible_desktop_confirmed'] is False
        assert row['details']['manual_confirmation_required'] is True
        assert Path(row['details']['screenshot']).exists()
    else:
        assert not (tmp_path / 'session.ready.png').exists()

# covers: install.desktop-entry/E6
@pytest.mark.parametrize('failure', ['capture-failed', 'not-png'])
def test_session_does_not_pass_without_picture(tmp_path, monkeypatch, failure):
    clock = [0]
    monkeypatch.setattr(acc.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(acc.time, 'sleep', lambda value: clock.__setitem__(0, clock[0] + value))
    monkeypatch.setattr(acc, 'run', lambda *a, **k: types.SimpleNamespace(
        stdout='env\nkwin 10\nshell 11 20\nsplash clear\n', returncode=0))
    def capture(path):
        if failure == 'capture-failed': raise OSError('screen unavailable')
        Path(path).write_text('no picture')
        return str(path)
    monkeypatch.setattr(acc.rungic_agent, 'screenshot', capture)
    row = acc.session_ready({'out_dir': tmp_path}, timeout=6)
    assert row['passed'] is False and row['details']['screenshot_error']

# covers: install.desktop-entry/E6 delivery.acceptance/E6
def test_selected_desktop_check_requires_linked_human_observation(runner):
    case = runner.scenario('session.ready')
    case['manual'] = ['Confirm the actual complete home screen']
    case['manual_review'] = True
    report = acc.run_scenarios([case], out_dir=runner.path, scope='smoke')
    assert report['scenarios'][0]['status'] == 'pass'
    assert report['verdict'] == 'incomplete'
    assert report['manual_results'][0]['id'] == 'manual.session.ready.1'
    human = acc.manual_report(report['path'], {'manual.session.ready.1': {'status': 'pass', 'note': 'Saw complete home, boot TEST, screenshot session.ready.png', 'reviewer': 'James', 'observed_at': '2026-10-07T07:10:00+09:00'}}, runner.path.parent / 'human')
    combined = acc.combine(acc.read_attempts(Path(human['path']))[0])
    assert combined['verdict'] == 'pass'
    assert report['manual_results'][0]['status'] == 'not-run'


# covers: install.desktop-entry/E6 delivery.acceptance/E6
@pytest.mark.parametrize('identity', [{}, {'reviewer': 'James'}, {'reviewer': 'James', 'observed_at': '2026-10-07T07:10:00'}])
def test_desktop_manual_result_rejects_missing_reviewer_or_timezone(runner, identity):
    case = runner.scenario('session.ready'); case.update(manual=['Observe the complete home screen'], manual_review=True)
    report = acc.run_scenarios([case], out_dir=runner.path, scope='smoke')
    directory = runner.path.parent / 'invalid-observation'
    with pytest.raises(ValueError, match='reviewer|observation time'):
        acc.manual_report(report['path'], {'manual.session.ready.1': {'status': 'pass', 'note': 'Saw desktop', **identity}}, directory)
    assert not directory.exists()


# covers: delivery.acceptance/E6
@pytest.mark.parametrize('check', ['good', 'bad', 'interface_contract'])
def test_renderer_preserves_recorded_check_description_when_current_plan_changes(runner, catalog, monkeypatch, check):
    if check == 'interface_contract':
        monkeypatch.setitem(acc.CHECKS, check, lambda ctx: acc.result(True))
    scenario = {**runner.scenario('one', check), 'title': 'Original recorded check'}
    report = acc.run_scenarios([scenario], out_dir=runner.path)
    original = Path(report['path']).read_bytes()
    definition = acc.load()['scenarios'][0]
    definition['title'] = 'New stronger check never performed'
    definition['check'] = check
    if check == 'interface_contract':
        definition['covers'] = ['iface:host-input']
    rendered = acc.render_report(report['path']).read_text()
    assert 'Original recorded check' in rendered
    assert 'New stronger check never performed' not in rendered
    assert '检查内容已在之后修改，本结果按当时的标准判定。' in rendered
    assert Path(report['path']).read_bytes() == original
