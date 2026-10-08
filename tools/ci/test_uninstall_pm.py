"""Package-manager status, not its success text, gates removal completion."""
import argparse,json,subprocess,tempfile,unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock
import standalone

class PackageReadback(unittest.TestCase):
    def run_case(self, failed_step=None, unknown=False, command_rc=1, resume=False, invalid_info=False, persistent=True, kind='system', already_removed=False):
        device=mock.Mock();device.maintenance.return_value=nullcontext()
        present=not already_removed;updated=kind=='system';marker=True;calls=[]
        def shell(command, **kwargs):
            nonlocal present,updated,marker
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
                flag=('SYSTEM UPDATED_SYSTEM_APP' if updated else 'SYSTEM HAS_CODE') if kind=='system' else 'HAS_CODE'
                return f'Packages:\n  Package [{standalone.APP}] (abc):\n    versionCode=54\n    codePath=/product/app/Rungic\n    flags=[ {flag} ]\n    pkgFlags=[ {flag} ]\n        android.permission.CAMERA: granted=true, flags=[ GRANTED_BY_DEFAULT ]\nHidden system packages:\n'
            if command.startswith('pm path '):
                if not present and command.startswith('pm path --user 0 '):
                    raise subprocess.CalledProcessError(1, ['adb', '-P', '5037', 'shell', 'sh'], output='')
                return 'package:/data/app/update/base.apk' if updated or kind=='ordinary' else 'package:/product/app/Rungic/Rungic.apk'
            if command.startswith('pm list packages --user 0'):
                return 'package:'+standalone.APP if present else ''
            if 'RUNGIC_PACKAGE_PERSISTENCE' in command:
                if kind=='ordinary':
                    xml=f'<packages><package name="{standalone.APP}" /></packages>' if not persistent else '<packages/>'
                    return 'STABLE\t'+'a'*64+'\n'+xml
                value='false' if persistent else 'true'
                return 'STABLE\t'+'a'*64+f'\n<package-restrictions><pkg name="{standalone.APP}" inst="{value}" /></package-restrictions>'
            if command.startswith('rm -f '+standalone.PENDING):marker=False;return ''
            if command.startswith('if [ -e '+standalone.PENDING):return 'PRESENT' if marker else 'ABSENT'
            return ''
        device.shell.side_effect=shell
        before={'paths':{},'termux_path':'termux','user_packages':'' if already_removed else 'package:'+standalone.APP}
        if resume:before['pending']='resume-op:1:-:'+kind
        def state(d, extra_paths=()):
            if len(calls)<4:return before
            paths={path:False for path in standalone.PATHS.values()}
            paths.update({path:False for path in extra_paths})
            paths.update({standalone.PENDING:marker,standalone.COMPAT:True,standalone.UNINSTALLED:True})
            return {'paths':paths,'pending':'op:1:-' if marker else '', 'termux_path':'termux','user_packages':'package:'+standalone.APP if present else ''}
        with tempfile.TemporaryDirectory() as temp:
            args=argparse.Namespace(serial='USB',adb_port=5037,adb='adb',purge=True,yes_delete=True,report=Path(temp)/'report')
            with mock.patch.object(standalone,'Device',return_value=device),mock.patch.object(standalone,'uninstall_state',side_effect=state),mock.patch.object(standalone,'PACKAGE_PERSIST_TIMEOUT',0,create=True):
                try:standalone.uninstall(args)
                except ValueError:pass
            result=json.loads((args.report/'report.json').read_text());markdown=(args.report/'report.md').read_text()
        return result,markdown,calls
    def test_final_system_base_survives_user_zero_uninstall(self):
        # covers: install.standalone-uninstall/E7
        result, markdown, calls = self.run_case(command_rc=0)
        self.assertTrue(result['complete'], result.get('error'))
        final = result['after']['package_validation']
        self.assertTrue(final['user_absent'])
        self.assertTrue(final['system_base'])
        self.assertEqual(final['system_state']['code_path'], '/product/app/Rungic')
        self.assertFalse(any('pm path --user 0' in item['command'] for item in final['observations']))
        self.assertIn('范围内卸载完成', markdown)

    def test_nonzero_commands_with_verified_effect_complete_and_keep_evidence(self):
        # covers: install.standalone-uninstall/E7
        result,_,_=self.run_case(resume=True)
        self.assertTrue(result['complete']);self.assertEqual(result['operation_id'],'resume-op')
        steps=[x for x in result['steps'] if x['name'] in ('clear-app-data','remove-apk-update','remove-user-app')]
        self.assertEqual(len(steps),3)
        for step in steps:
            self.assertEqual(step['returncode'],1);self.assertIn('Success',step['output']);self.assertTrue(step['verified'])
    def test_in_memory_absence_with_installed_disk_state_keeps_removal_pending(self):
        # covers: install.standalone-uninstall/E7
        result,_,_=self.run_case(persistent=False)
        self.assertFalse(result['complete'])
        self.assertNotIn('finish',[step['name'] for step in result['steps']])
        self.assertEqual(result['phase'],'persist-user-app')

    def test_ordinary_apk_removed_from_packages_xml_can_complete(self):
        # covers: install.standalone-uninstall/E7
        result,_,_=self.run_case(kind='ordinary')
        self.assertTrue(result['complete'])
        self.assertEqual(result['package_kind'],'ordinary')
        self.assertNotIn('remove-apk-update',[step['name'] for step in result['steps']])

    def test_ordinary_apk_still_in_persistent_registry_keeps_marker(self):
        # covers: install.standalone-uninstall/E7
        result,_,_=self.run_case(kind='ordinary',persistent=False)
        self.assertFalse(result['complete'])
        self.assertNotIn('finish',[step['name'] for step in result['steps']])
        self.assertEqual(result['phase'],'persist-user-app')

    def test_resume_after_apk_is_gone_uses_recorded_type(self):
        # covers: install.standalone-uninstall/E5, install.standalone-uninstall/E7
        result,_,calls=self.run_case(kind='ordinary',resume=True,already_removed=True)
        self.assertTrue(result['complete']);self.assertEqual(result['package_kind'],'ordinary')
        self.assertEqual(result['package_kind_evidence']['source'],'existing-removal-record')
        self.assertFalse(any(command.startswith(('pm clear','pm uninstall','dumpsys package')) for command in calls))
        self.assertTrue(any('file=$parent/packages.xml' in command for command in calls))

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
        result,markdown,calls=self.run_case(unknown=True)
        self.assertFalse(result['complete']);self.assertEqual(result['phase'],'clear-app-data')
        self.assertNotIn('remove-apk-update',[x['name'] for x in result['steps']]);self.assertIn('未知',result['error'])
        self.assertNotIn('returned non-zero exit status', markdown)
        observation=result['steps'][-1]['observations'][-1]
        self.assertIn('Permission denied',observation['output']);self.assertEqual(observation['returncode'],1)
    def test_unknown_update_status_cannot_be_treated_as_no_update(self):
        # covers: install.standalone-uninstall/E7
        result,_,calls=self.run_case(invalid_info=True)
        self.assertFalse(result['complete']);self.assertEqual(result['phase'],'inspect-package-type')
        self.assertIn('卸载包类型',result['error'])
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
                        script=script.replace(standalone.ROOT_PROVIDER_SH,'BB=/usr/bin/busybox; RUNGIC_ROOT=magisk').replace('/data/adb/magisk/busybox','/usr/bin/busybox')
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

    def test_final_base_requires_active_product_system_package(self):
        # covers: install.standalone-uninstall/E7
        for path, flags, known in [('/product/app/Rungic', 'SYSTEM HAS_CODE', True),
                                   ('/data/app/update', 'SYSTEM UPDATED_SYSTEM_APP', False),
                                   ('/product/app/Rungic', 'HAS_CODE', False)]:
            with self.subTest(path=path, flags=flags):
                device = mock.Mock()
                device.shell.return_value = f'Packages:\n  Package [{standalone.APP}] (abc):\n    codePath={path}\n    versionCode=54\n    flags=[ {flags} ]\n'
                self.assertEqual(standalone.package_readback(device, 'verify-system-base')[1], known)
        for body in ('', '    versionCode=54\n    flags=[ SYSTEM ]',
                     '    codePath=/product/app/Rungic\n    codePath=/data/app/update\n    versionCode=54\n    flags=[ SYSTEM ]',
                     '    codePath=/product/app/Rungic\n    versionCode=54\n    flags=[ SYSTEM ]\n    pkgFlags=[ SYSTEM UPDATED_SYSTEM_APP ]'):
            with self.subTest(body=body):
                device = mock.Mock()
                device.shell.return_value = f'Packages:\n  Package [{standalone.APP}] (abc):\n{body}\nHidden system packages:\n  Package [{standalone.APP}] (old):\n    codePath=/product/app/Rungic\n    versionCode=54\n    flags=[ SYSTEM ]\n'
                with self.assertRaisesRegex(ValueError, '读回未知'):
                    standalone.package_readback(device, 'verify-system-base')

    def test_user_list_unknown_is_not_absence_and_other_package_is_not_rungic(self):
        # covers: install.standalone-uninstall/E7
        device=mock.Mock();device.shell.return_value='Failure [Binder transaction]'
        with self.assertRaises(ValueError):standalone.package_readback(device,'remove-user-app')
        device.shell.return_value='package:com.rungic.plasma.other'
        self.assertTrue(standalone.package_readback(device,'remove-user-app')[1])

class PersistentPackageState(unittest.TestCase):
    def stable(self,inst='false',body=None):
        return 'STABLE\t'+'a'*64+'\n'+(body if body is not None else f'<package-restrictions><pkg name="{standalone.APP}" inst="{inst}" /></package-restrictions>')

    def wait(self,responses,timeout=3,kind='system'):
        device=mock.Mock();device.shell.side_effect=responses
        evidence=[];clock=[0];saved=[]
        def sleep(seconds):clock[0]+=seconds
        with mock.patch.object(standalone,'PACKAGE_PERSIST_TIMEOUT',timeout),mock.patch.object(standalone.time,'monotonic',side_effect=lambda:clock[0]),mock.patch.object(standalone.time,'sleep',side_effect=sleep):
            result=standalone.wait_user_package_persistence(device,evidence,lambda:saved.append(len(evidence)),kind=kind)
        return result,evidence,saved

    def test_waits_for_actual_uninstalled_state_and_retains_every_attempt(self):
        # covers: install.standalone-uninstall/E7
        result,evidence,saved=self.wait([self.stable('true'),'PENDING\tbackup','PENDING\tchanged',self.stable()],timeout=5)
        self.assertEqual(result['state'],'uninstalled');self.assertEqual(result['elapsed_seconds'],3)
        self.assertEqual(len(evidence),4);self.assertEqual(saved,[1,2,3,4])
        self.assertEqual(evidence[0]['readback']['state'],'installed')
        self.assertEqual(evidence[-1]['readback']['inst'],'false')
        self.assertTrue(all(entry['root'] and entry['returncode']==0 for entry in evidence))

    def test_unknown_missing_duplicate_and_malformed_values_never_complete(self):
        # covers: install.standalone-uninstall/E7
        bodies=['','<other/>','<package-restrictions/>',f'<package-restrictions><pkg name="{standalone.APP}" inst="unknown" /></package-restrictions>',f'<package-restrictions><pkg name="{standalone.APP}" inst="false" /><pkg name="{standalone.APP}" inst="false" /></package-restrictions>']
        for body in bodies:
            with self.subTest(body=body),self.assertRaises(ValueError):self.wait([self.stable(body=body)])
        for output in ('','PENDING\tbackup\nignored','Success','STABLE\tinvalid\n<package-restrictions/>'):
            with self.subTest(output=output),self.assertRaises(ValueError):self.wait([output])

    def test_pending_or_default_installed_state_times_out(self):
        # covers: install.standalone-uninstall/E7
        for output in ('PENDING\tbackup','PENDING\tchanged',self.stable('true'),self.stable(body=f'<package-restrictions><pkg name="{standalone.APP}" /></package-restrictions>')):
            with self.subTest(output=output),self.assertRaisesRegex(ValueError,'超时'):self.wait([output],timeout=0)

    def test_failed_read_retains_raw_output_and_never_completes(self):
        # covers: install.standalone-uninstall/E7
        device=mock.Mock();device.shell.side_effect=subprocess.CalledProcessError(1,'read',output=b'Permission denied')
        evidence=[];saved=[]
        with self.assertRaisesRegex(ValueError,'读回失败'):
            standalone.wait_user_package_persistence(device,evidence,lambda:saved.append(True))
        self.assertEqual(evidence[0]['output'],'Permission denied');self.assertEqual(evidence[0]['returncode'],1)
        self.assertEqual(saved,[True])

    def test_global_package_registry_requires_stable_absence(self):
        # covers: install.standalone-uninstall/E7
        installed=self.stable(body=f'<packages><package name="{standalone.APP}" /></packages>')
        absent=self.stable(body='<packages><package name="com.other" /></packages>')
        result,evidence,_=self.wait([installed,'PENDING\tbackup',absent],kind='ordinary')
        self.assertEqual(result['kind'],'ordinary');self.assertFalse(result['registered'])
        self.assertTrue(evidence[0]['readback']['registered'])
        self.assertIn('backup=$parent/packages-backup.xml',evidence[0]['command'])
        with self.assertRaisesRegex(ValueError,'超时'):self.wait([installed],timeout=0,kind='ordinary')
        with self.assertRaises(ValueError):self.wait([self.stable()],kind='ordinary')
        with self.assertRaises(ValueError):self.wait([self.stable(body=f'<packages><package name="{standalone.APP}" /><package name="{standalone.APP}" /></packages>')],kind='ordinary')

    def test_real_shell_handles_text_binary_backup_and_concurrent_write(self):
        # covers: install.standalone-uninstall/E7
        import os
        import shutil
        for shell in (['/bin/sh'],['/usr/bin/busybox','ash']):
            for variant in ('text','binary','backup','changed','missing','symlink','conversion_error','backup_during_sync','ordinary_text','ordinary_binary','ordinary_backup','ordinary_large'):
                mode=variant.removeprefix('ordinary_');kind='ordinary' if variant.startswith('ordinary_') else 'system'
                with self.subTest(shell=shell,mode=variant),tempfile.TemporaryDirectory() as temp:
                    root=Path(temp);parent=root/'users/0';parent.mkdir(parents=True)
                    xml=f'<package-restrictions><pkg name="{standalone.APP}" inst="false" /></package-restrictions>'
                    # An ordinary app's packages.xml is longer than one argument may be (X70, 2026-10-08).
                    if mode=='large':xml=f'<packages>{"<package name=\"x\" />"*20000}</packages>'
                    target=parent/('packages.xml' if kind=='ordinary' else 'package-restrictions.xml')
                    backup=parent/('packages-backup.xml' if kind=='ordinary' else 'package-restrictions-backup.xml')
                    target.write_bytes((b'ABX\0' if mode in ('binary','conversion_error') else b'')+xml.encode())
                    before=target.read_bytes()
                    if mode=='backup':backup.write_text('old')
                    if mode=='missing':target.unlink()
                    if mode=='symlink':target.unlink();target.symlink_to(root/'outside');(root/'outside').write_text(xml)
                    converter=root/'abx2xml';converter.write_text("#!/usr/bin/python3\nimport sys\nfrom pathlib import Path\ndata=Path(sys.argv[1]).read_bytes()\nassert data[:4]==b'ABX\\0'\n"+("sys.exit(1)\n" if mode=='conversion_error' else "Path(sys.argv[2]).write_text(data[4:].decode())\n"));converter.chmod(0o755)
                    script=standalone.package_persistence_script(kind).replace(standalone.ROOT_PROVIDER_SH,'BB=/usr/bin/busybox; RUNGIC_ROOT=magisk').replace('/data/adb/magisk/busybox','/usr/bin/busybox').replace('/data/system/users/0' if kind=='system' else '/data/system',str(parent)).replace('/system/bin/abx2xml',str(converter)).replace('/data/local/tmp',str(root))
                    sync=':'
                    if mode=='changed':sync=f"printf x >> {target}"
                    if mode=='backup_during_sync':sync=f"printf old > {backup}"
                    run=subprocess.run([*shell,'-c','set -eu\nid() { echo 0; }\nprintf() { /usr/bin/printf "$@"; }\nsync() { '+sync+'; }\n'+script],capture_output=True,text=True,timeout=5)
                    if mode in ('missing','symlink','conversion_error'):self.assertNotEqual(run.returncode,0)
                    elif mode in ('backup','changed','backup_during_sync'):
                        self.assertEqual(run.returncode,0,run.stderr);self.assertTrue(run.stdout.startswith('PENDING\t'))
                    else:
                        self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(run.stdout.split('\n',1)[1].strip(),xml)
                        self.assertEqual(target.read_bytes(),before)
                    if mode not in ('missing','changed','symlink'):self.assertEqual(target.read_bytes(),before)

if __name__=='__main__':unittest.main()
