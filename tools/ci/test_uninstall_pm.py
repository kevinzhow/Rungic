"""Package-manager status, not its success text, gates removal completion."""
import argparse,json,subprocess,tempfile,unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock
import standalone

class PackageReadback(unittest.TestCase):
    def run_case(self, failed_step=None, unknown=False, command_rc=1, resume=False, invalid_info=False):
        device=mock.Mock();device.maintenance.return_value=nullcontext()
        present=True;updated=True;calls=[]
        def shell(command, **kwargs):
            nonlocal present,updated
            calls.append(command)
            if command=='getprop ro.serialno':return 'USB'
            if command=='pm list users':return 'UserInfo{0:Owner:13}'
            if command=='id -u':return '0'
            names={'pm clear --user 0 '+standalone.APP:'clear-app-data','pm uninstall-system-updates '+standalone.APP:'remove-apk-update','pm uninstall --user 0 '+standalone.APP:'remove-user-app'}
            if command in names:
                name=names[command]
                if name!=failed_step:
                    if name=='remove-apk-update':updated=False
                    if name=='remove-user-app':present=False
                if command_rc:raise subprocess.CalledProcessError(command_rc,'adb shell',output=b'Failed transaction (2147483646)\nUninstalling updates...\nSuccess\n')
                return 'Success'
            if 'RUNGIC_APP_DATA_READBACK' in command:
                if unknown:raise subprocess.CalledProcessError(1,'readback',output=b'Permission denied')
                return 'CE\tNOT_EMPTY\nDE\tEMPTY' if failed_step=='clear-app-data' else 'CE\tEMPTY\nDE\tEMPTY'
            if command.startswith('dumpsys package'):
                if invalid_info:return 'Failure [Binder transaction]'
                flag='SYSTEM UPDATED_SYSTEM_APP' if updated else 'SYSTEM HAS_CODE'
                return f'Packages:\n  Package [{standalone.APP}] (abc):\n    versionCode=54\n    flags=[ {flag} ]\nHidden system packages:\n'
            if command.startswith('pm path --user 0'):
                return 'package:/data/app/update/base.apk' if updated else 'package:/product/app/Rungic/Rungic.apk'
            if command.startswith('pm list packages --user 0'):
                return 'package:'+standalone.APP if present else ''
            if command.startswith('if [ -e '+standalone.PENDING):return 'ABSENT'
            return ''
        device.shell.side_effect=shell
        before={'paths':{},'termux_path':'termux','user_packages':'package:'+standalone.APP}
        if resume:before['pending']='resume-op:1:-'
        def state(d):return before if len(calls)<4 else {'paths':{},'termux_path':'termux','user_packages':'package:'+standalone.APP if present else ''}
        with tempfile.TemporaryDirectory() as temp:
            args=argparse.Namespace(serial='USB',adb_port=5037,adb='adb',purge=True,yes_delete=True,report=Path(temp)/'report')
            with mock.patch.object(standalone,'Device',return_value=device),mock.patch.object(standalone,'uninstall_state',side_effect=state):
                try:standalone.uninstall(args)
                except ValueError:pass
            result=json.loads((args.report/'report.json').read_text());markdown=(args.report/'report.md').read_text()
        return result,markdown,calls
    def test_nonzero_commands_with_verified_effect_complete_and_keep_evidence(self):
        # covers: install.standalone-uninstall/E7
        result,_,_=self.run_case(resume=True)
        self.assertTrue(result['complete']);self.assertEqual(result['operation_id'],'resume-op')
        steps=[x for x in result['steps'] if x['name'] in ('clear-app-data','remove-apk-update','remove-user-app')]
        self.assertEqual(len(steps),3)
        for step in steps:
            self.assertEqual(step['returncode'],1);self.assertIn('Success',step['output']);self.assertTrue(step['verified'])
    def test_zero_success_without_changed_state_stops_each_step(self):
        # covers: install.standalone-uninstall/E7
        for name in ('clear-app-data','remove-apk-update','remove-user-app'):
            with self.subTest(name=name):
                result,markdown,calls=self.run_case(failed_step=name,command_rc=0)
                self.assertFalse(result['complete']);self.assertEqual(result['phase'],name)
                self.assertNotIn('finish',[x['name'] for x in result['steps']]);self.assertIn('读回',result['error'])
                step=result['steps'][-1];self.assertEqual(step['returncode'],0);self.assertFalse(step['verified']);self.assertIn('Success',step['output'])
                self.assertIn('读回',markdown)
    def test_unknown_readback_keeps_marker_and_stops_after_clear(self):
        # covers: install.standalone-uninstall/E7
        result,_,calls=self.run_case(unknown=True)
        self.assertFalse(result['complete']);self.assertEqual(result['phase'],'clear-app-data')
        self.assertNotIn('remove-apk-update',[x['name'] for x in result['steps']]);self.assertIn('未知',result['error'])
        observation=result['steps'][-1]['observations'][-1]
        self.assertIn('Permission denied',observation['output']);self.assertEqual(observation['returncode'],1)
    def test_unknown_update_status_cannot_be_treated_as_no_update(self):
        # covers: install.standalone-uninstall/E7
        result,_,calls=self.run_case(invalid_info=True)
        self.assertFalse(result['complete']);self.assertEqual(result['phase'],'inspect-apk-update')
        self.assertIn('检查 APK 更新',result['error'])
        self.assertFalse(any(c.startswith('pm uninstall') for c in calls))


class ActualReadbacks(unittest.TestCase):
    def test_clear_readback_executes_actual_shell_for_ce_de_and_links(self):
        # covers: install.standalone-uninstall/E7
        for shell in ('/bin/sh','/usr/bin/busybox'):
            for mode in ('empty','empty_dirs','ce_file','de_file','link','missing_parent','many_files','diagnostic_failure'):
                with self.subTest(shell=shell,mode=mode),tempfile.TemporaryDirectory() as temp:
                    root=Path(temp)
                    for parent in ('data/user/0','data/user_de/0'):(root/parent).mkdir(parents=True)
                    ce=root/'data/user/0'/standalone.APP;de=root/'data/user_de/0'/standalone.APP
                    ce.mkdir();de.mkdir()
                    if mode=='empty_dirs':(ce/'cache/nested').mkdir(parents=True)
                    if mode=='ce_file':(ce/'private').write_text('private data')
                    if mode=='de_file':(de/'private').write_text('private data')
                    if mode=='many_files':
                        (ce/'line\nbreak').write_text('private data')
                        for i in range(25):(ce/f'private-{i}').write_text('private data')
                    if mode=='diagnostic_failure':(ce/'private').write_text('private data')
                    if mode=='link':(ce/'link').symlink_to(root/'outside')
                    if mode=='missing_parent':de.rmdir();de.parent.rmdir()
                    device=mock.Mock()
                    def run(script,**kwargs):
                        self.assertTrue(kwargs['root'])
                        if mode=='diagnostic_failure' and 'RUNGIC_APP_DATA_ENTRIES' in script:
                            raise subprocess.CalledProcessError(1,'entry-readback',output='No entry listing')
                        script=script.replace('/data/adb/magisk/busybox','/usr/bin/busybox')
                        for source in ('/data/user/0','/data/user_de/0'):
                            script=script.replace(source,str(root/source.lstrip('/')))
                        command=[shell,'ash','-c'] if shell.endswith('busybox') else [shell,'-c']
                        result=subprocess.run(command+['set -eu; id() { echo 0; }; '+script],capture_output=True,text=True)
                        if result.returncode:raise subprocess.CalledProcessError(result.returncode,command,output=result.stdout+result.stderr)
                        return result.stdout.strip()
                    device.shell.side_effect=run
                    if mode=='missing_parent':
                        with self.assertRaises(subprocess.CalledProcessError):standalone.package_readback(device,'clear-app-data')
                    else:
                        observed,verified=standalone.package_readback(device,'clear-app-data')
                        self.assertEqual(verified,mode in ('empty','empty_dirs'))
                        if mode=='de_file':self.assertEqual(observed['DE'],'NOT_EMPTY')
                        if mode=='many_files':
                            entries=observed['remaining_entries']['CE'];self.assertEqual(len(entries),20)
                            self.assertTrue(all(Path(entry).exists() for entry in entries))
                        if mode=='ce_file':self.assertIn(str(ce/'private'),observed['remaining_entries']['CE'])
                        if mode=='diagnostic_failure':self.assertIn('CE',observed['entries_error'])

    def test_update_readback_rejects_untrusted_or_unrelated_dump(self):
        # covers: install.standalone-uninstall/E7
        for info in ('',f'Hidden system packages:\n  Package [{standalone.APP}] (abc):\n    versionCode=54\n    flags=[ SYSTEM ]',
                     f'Packages:\n  Package [{standalone.APP}] (abc):\n    versionCode=54\n  Package [com.other] (xyz):\n    flags=[ SYSTEM ]'):
            with self.subTest(info=info):
                device=mock.Mock();device.shell.side_effect=['package:/product/app/Rungic/Rungic.apk',info]
                with self.assertRaises(ValueError):standalone.package_readback(device,'remove-apk-update')

    def test_user_list_unknown_is_not_absence_and_other_package_is_not_rungic(self):
        # covers: install.standalone-uninstall/E7
        device=mock.Mock();device.shell.return_value='Failure [Binder transaction]'
        with self.assertRaises(ValueError):standalone.package_readback(device,'remove-user-app')
        device.shell.return_value='package:com.rungic.plasma.other'
        self.assertTrue(standalone.package_readback(device,'remove-user-app')[1])

if __name__=='__main__':unittest.main()
