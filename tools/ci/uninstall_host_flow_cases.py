#!/usr/bin/env python3
"""Actual Python uninstall entry + generated shell against path-mapped storage.
Android package manager/identity, maintenance lease and SELinux are explicit stand-ins.
"""
import argparse, contextlib, hashlib, importlib, io, json, os, re, subprocess, sys
from pathlib import Path
from unittest import mock
p=argparse.ArgumentParser();p.add_argument('repository',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
sys.path.insert(0,str(a.repository.resolve()/'tools/ci'));product=importlib.import_module('standalone')
a.output.mkdir(parents=True,exist_ok=True);results=[]
for mode in ['normal','normal_no_termux','termux_readback_failure','package_failure','readback_failure','finish_readback_marker','final_snapshot_failure','final_snapshot_marker','final_snapshot_unknown','final_snapshot_missing','final_snapshot_duplicate','final_snapshot_runtime_reappears','final_snapshot_package_reappears','final_snapshot_retained_gone','preview_healthy','preview_mount']:
 case=a.output.resolve()/mode;root=case/'root';adb=root/'data/adb'
 home=adb/'rungic-lxc/runtime/var/lib/lxc/plasma/state/home';home.mkdir(parents=True);(home/'private').write_text('old home')
 appdata=root/'data/user/0'/product.APP;appdata.mkdir(parents=True);(appdata/'private').write_text('app private')
 for d in ['proc/self','proc/1','sys/block','product/etc/rungic','data/local/tmp','data/user_de/0','data/system/users/0']:(root/d).mkdir(parents=True,exist_ok=True)
 (root/'proc/mounts').write_text(f'none {home}/Shared none rw 0 0\n' if mode=='preview_mount' else '')
 (root/'proc/1/cmdline').write_bytes(b'init\x00');(root/'proc/1/mountinfo').write_text('1 0 0:1 / / rw - rootfs rootfs rw\n');(root/'proc/self/mountinfo').write_text('1 0 0:1 / / rw - rootfs rootfs rw\n');(root/'product/etc/rungic/firstboot.sh').write_text('old seed')
 def snapshot():
  return {str(x.relative_to(root)):('link:'+os.readlink(x) if x.is_symlink() else 'dir' if x.is_dir() else hashlib.sha256(x.read_bytes()).hexdigest()) for x in root.rglob('*')}
 restrictions=root/'data/system/users/0/package-restrictions.xml'
 restrictions.write_text(f'<package-restrictions><pkg name="{product.APP}" inst="true" /></package-restrictions>')
 original=snapshot()
 class Device:
  def __init__(self,args):self.installed=True;self.updated=True;self.commands=[];self.root_removed=False;self.marker_cleared=False
  def maintenance(self,**kwargs):return contextlib.nullcontext()
  def push(self,*args):raise AssertionError('Uninstall must not upload a payload')
  def shell(self,script,root=False,timeout=120):
   self.commands.append({'script':script,'root':root})
   if script=='getprop ro.serialno':return 'USB'
   if script=='pm list users':return 'UserInfo{0:Owner:13}'
   if script=='id -u':return '0'
   if script.startswith('pm list packages'):return 'package:'+product.APP if self.installed else ''
   if script.startswith('pm path com.termux'):
    if mode=='termux_readback_failure' and self.marker_cleared:raise subprocess.CalledProcessError(255,'pm-path',output='Remote exception')
    return '' if mode=='normal_no_termux' else 'package:/data/app/termux/base.apk'
   if script.startswith('pm path --user 0 ') and not self.installed:
    raise subprocess.CalledProcessError(1,['adb','-P','5037','shell','sh'],output='')
   if script.startswith('pm path '):return 'package:/product/app/Rungic/Rungic.apk'
   if script.startswith('dumpsys '):
    if 'grep' in script:return 'versionCode=26 versionName=2.6'
    return f'Packages:\n  Package [{product.APP}] (abc):\n    versionCode=54\n    codePath=/product/app/Rungic\n    flags=[ SYSTEM ' + ('UPDATED_SYSTEM_APP' if self.updated else '') + ' ]\n    pkgFlags=[ SYSTEM ' + ('UPDATED_SYSTEM_APP' if self.updated else '') + ' ]\n        android.permission.CAMERA: granted=true, flags=[ GRANTED_BY_DEFAULT ]\n'
   if script.startswith('am force-stop'):return ''
   if script.startswith('pm clear'):
    subprocess.run(['rm','-rf','--',str(appdata)],check=True);return 'Success'
   if script.startswith('pm uninstall-system-updates'):self.updated=False;return 'Success'
   if script.startswith('pm uninstall --user'):
    if mode=='package_failure':raise subprocess.CalledProcessError(1,'package-manager',output='Failure [busy]\n')
    self.installed=False;restrictions.write_text(f'<package-restrictions><pkg name="{product.APP}" inst="false" /></package-restrictions>');return 'Success'
   if root and self.root_removed and mode=='readback_failure' and 'RUNGIC_APP_DATA_READBACK' not in script and 'printf' in script and 'if [ -e' in script:
    raise subprocess.CalledProcessError(1,'path-readback',output='Injected final readback failure\n')
   if script.startswith('rm -f '+product.PENDING) and mode=='finish_readback_marker':return '' # Command says success, marker actually remains.
   if root and self.marker_cleared and mode=='final_snapshot_failure' and 'printf' in script and 'if [ -e' in script:
    raise subprocess.CalledProcessError(1,['adb','-P','5037','shell','sh'],output='Injected snapshot failure after marker clear\n')
   if root and self.marker_cleared and mode=='final_snapshot_marker' and 'printf' in script and 'if [ -e' in script:
    (adb/'rungic-uninstalling').write_text('unexpected-marker:1:-')
   if root and self.marker_cleared and 'printf' in script and 'if [ -e' in script:
    if mode=='final_snapshot_runtime_reappears':
     (adb/'rungic-plasma').mkdir(exist_ok=True);(adb/'rungic-plasma/unexpected').write_text('reappeared')
    if mode=='final_snapshot_package_reappears':self.installed=True
    if mode=='final_snapshot_retained_gone':(adb/'rungic-uninstalled').unlink(missing_ok=True)
   if script.startswith('rm -f '+product.PENDING):self.marker_cleared=True
   text=script
   for prefix in ('/data/system','/data/adb','/data/data','/data/user_de','/data/user','/data/local/tmp','/proc','/sys/block','/product'):
    text=re.sub(r'(?<![\w/])'+re.escape(prefix)+r'\b',str(case/'root')+prefix,text)
   text=text.replace('BB='+str(adb/'magisk/busybox'),'BB=/usr/bin/busybox')
   run=subprocess.run(['/usr/bin/busybox','ash','-c','set -eu\nid() { echo 0; }\nchcon() { :; }\ngetprop() { echo fixture-base; }\nuname() { echo fixture-kernel; }\ngetenforce() { echo Enforcing; }\n'+text],capture_output=True,text=True,timeout=30)
   number=len(self.commands);(case/f'command-{number}.sh').write_text(text);(case/f'command-{number}.stdout').write_text(run.stdout);(case/f'command-{number}.stderr').write_text(run.stderr)
   if 'phase\truntime-removed' in run.stdout:self.root_removed=True
   if run.returncode:raise subprocess.CalledProcessError(run.returncode,'local-shell',output=(run.stdout+run.stderr).replace(str(case/'root'),''))
   output=run.stdout.replace(str(case/'root'),'').strip()
   if root and self.marker_cleared and 'printf' in script and 'if [ -e' in script:
    if mode=='final_snapshot_unknown':output=output.replace(product.PENDING+'\t0',product.PENDING+'\tUNKNOWN')
    if mode=='final_snapshot_missing':output='\n'.join(line for line in output.splitlines() if not line.startswith(product.PENDING+'\t'))
    if mode=='final_snapshot_duplicate':output+='\n'+product.PENDING+'\t1'
    if mode in ('final_snapshot_unknown','final_snapshot_missing','final_snapshot_duplicate'):(case/f'command-{number}.stdout-injected').write_text(output)
   return output
 device=Device(None);args=argparse.Namespace(serial='USB',adb_port=5037,adb='unused',purge=True,yes_delete=not mode.startswith('preview'),report=case/'report')
 error=None
 try:
  with mock.patch.object(product,'Device',return_value=device),contextlib.redirect_stdout(io.StringIO()):value=product.uninstall(args)
 except Exception as e:error=str(e)
 report=json.loads((args.report/'report.json').read_text());markdown=(args.report/'report.md').read_text()
 checks={'report_survives':(args.report/'report.md').is_file()}
 pending=(adb/'rungic-uninstalling').exists()
 if mode in ('normal','normal_no_termux'):checks.update(completed=report['complete'],marker_removed=not pending,runtime_removed=not (adb/'rungic-lxc').exists(),package_removed=not device.installed,final_snapshot_marker_absent=report.get('after',{}).get('paths',{}).get(product.PENDING) is False,final_snapshot_record_empty=report.get('after',{}).get('pending')=='',previous_snapshot_preserved=report.get('before_finish',{}).get('paths',{}).get(product.PENDING) is True)
 elif mode=='termux_readback_failure':checks.update(rejected=error is not None,incomplete=not report['complete'],readback_unknown=bool(report.get('readback_error')))
 elif mode=='package_failure':checks.update(rejected=error is not None,incomplete=not report['complete'],marker_kept=pending,raw_failure_retained='Failure [busy]' in json.dumps(report))
 elif mode=='readback_failure':checks.update(rejected=error is not None,incomplete=not report['complete'],marker_kept=pending,readback_failure_recorded='readback_error' in report,no_false_verified_rows=not any(token in line for line in markdown.splitlines() if line.startswith('| `') for token in ('已删除并读回','已删除并确认','确认不存在')))
 elif mode=='final_snapshot_failure':checks.update(rejected=error is not None,incomplete=not report['complete'],failure_recorded=bool(report.get('readback_error')),prior_snapshot_kept=bool(report.get('before_finish')),marker_clear_evidence=report.get('marker_cleared') is True,readable_failure='读取最终现场的命令失败' in markdown,no_python_exception_text='returned non-zero exit status' not in markdown)
 elif mode in ('final_snapshot_unknown','final_snapshot_missing','final_snapshot_duplicate','final_snapshot_runtime_reappears','final_snapshot_package_reappears','final_snapshot_retained_gone'):
  checks.update(rejected=error is not None,incomplete=not report['complete'],no_false_completed_heading='范围内卸载完成' not in markdown)
 elif mode=='final_snapshot_marker':checks.update(rejected=error is not None,incomplete=not report['complete'],marker_present=pending,final_snapshot_preserved=report.get('after',{}).get('paths',{}).get(product.PENDING) is True,retained_marker=product.PENDING in report['expected_retained'])
 elif mode=='finish_readback_marker':checks.update(incomplete_if_marker_present=not pending or not report['complete'])
 else:
  checks.update(readonly=snapshot()==original,incomplete=not report['complete'],no_package_mutation=device.installed)
  if mode=='preview_mount':checks.update(mount_refusal_reported='挂载' in json.dumps(report,ensure_ascii=False),actual_matching_mount=str(home).replace(str(root),'')+'/Shared' in markdown,no_manual_unmount_advice='请先解除挂载并重试' not in markdown,plan_and_presence_columns='执行时计划删除 | 现在是否存在' in markdown)
 results.append({'mode':mode,'error':error,'complete':report['complete'],'pending_present':pending,'checks':checks,'passed':all(checks.values()),'gating':mode!='finish_readback_marker'})
 (case/'commands.json').write_text(json.dumps(device.commands,ensure_ascii=False,indent=2)+'\n')
(a.output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n');print(json.dumps(results,ensure_ascii=False,indent=2));sys.exit(0 if all(r['passed'] for r in results if r['gating']) else 1)
