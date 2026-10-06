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
for mode in ['normal','package_failure','readback_failure','finish_readback_marker','preview_healthy','preview_mount']:
 case=a.output.resolve()/mode;root=case/'root';adb=root/'data/adb'
 home=adb/'rungic-lxc/runtime/var/lib/lxc/plasma/state/home';home.mkdir(parents=True);(home/'private').write_text('old home')
 appdata=root/'data/user/0'/product.APP;appdata.mkdir(parents=True);(appdata/'private').write_text('app private')
 for d in ['proc/self','proc/1','sys/block','product/etc/rungic','data/local/tmp']:(root/d).mkdir(parents=True,exist_ok=True)
 (root/'proc/mounts').write_text(f'none {home}/Shared none rw 0 0\n' if mode=='preview_mount' else '')
 (root/'proc/1/cmdline').write_bytes(b'init\x00');(root/'proc/1/mountinfo').write_text('1 0 0:1 / / rw - rootfs rootfs rw\n');(root/'proc/self/mountinfo').write_text('1 0 0:1 / / rw - rootfs rootfs rw\n');(root/'product/etc/rungic/firstboot.sh').write_text('old seed')
 def snapshot():
  return {str(x.relative_to(root)):('link:'+os.readlink(x) if x.is_symlink() else 'dir' if x.is_dir() else hashlib.sha256(x.read_bytes()).hexdigest()) for x in root.rglob('*')}
 original=snapshot()
 class Device:
  def __init__(self,args):self.installed=True;self.commands=[];self.root_removed=False
  def maintenance(self,**kwargs):return contextlib.nullcontext()
  def push(self,*args):raise AssertionError('Uninstall must not upload a payload')
  def shell(self,script,root=False,timeout=120):
   self.commands.append({'script':script,'root':root})
   if script=='getprop ro.serialno':return 'USB'
   if script=='pm list users':return 'UserInfo{0:Owner:13}'
   if script=='id -u':return '0'
   if script.startswith('pm list packages'):return 'package:'+product.APP if self.installed else ''
   if script.startswith('pm path com.termux'):return 'package:/data/app/termux/base.apk'
   if script.startswith('pm path '):return 'package:/product/app/Rungic/Rungic.apk'
   if script.startswith('dumpsys '):return 'versionCode=26 versionName=2.6' if 'grep' in script else 'UPDATED_SYSTEM_APP'
   if script.startswith('am force-stop'):return ''
   if script.startswith('pm clear'):
    subprocess.run(['rm','-rf','--',str(appdata)],check=True);return 'Success'
   if script.startswith('pm uninstall-system-updates'):return 'Success'
   if script.startswith('pm uninstall --user'):
    if mode=='package_failure':raise subprocess.CalledProcessError(1,'package-manager',output='Failure [busy]\n')
    self.installed=False;return 'Success'
   if root and self.root_removed and mode=='readback_failure' and 'printf' in script and 'if [ -e' in script:
    raise subprocess.CalledProcessError(1,'path-readback',output='Injected final readback failure\n')
   if script.startswith('rm -f '+product.PENDING) and mode=='finish_readback_marker':return '' # Command says success, marker actually remains.
   text=script
   for prefix in ('/data/adb','/data/data','/data/user','/data/local/tmp','/proc','/sys/block','/product'):
    text=re.sub(r'(?<![\w/])'+re.escape(prefix)+r'\b',str(case/'root')+prefix,text)
   text=text.replace(str(adb/'magisk/busybox'),'/usr/bin/busybox')
   run=subprocess.run(['/usr/bin/busybox','ash','-c','set -eu\nchcon() { :; }\ngetprop() { echo fixture-base; }\nuname() { echo fixture-kernel; }\ngetenforce() { echo Enforcing; }\n'+text],capture_output=True,text=True,timeout=30)
   number=len(self.commands);(case/f'command-{number}.sh').write_text(text);(case/f'command-{number}.stdout').write_text(run.stdout);(case/f'command-{number}.stderr').write_text(run.stderr)
   if 'phase\truntime-removed' in run.stdout:self.root_removed=True
   if run.returncode:raise subprocess.CalledProcessError(run.returncode,'local-shell',output=(run.stdout+run.stderr).replace(str(case/'root'),''))
   return run.stdout.replace(str(case/'root'),'').strip()
 device=Device(None);args=argparse.Namespace(serial='USB',adb_port=5037,adb='unused',purge=True,yes_delete=not mode.startswith('preview'),report=case/'report')
 error=None
 try:
  with mock.patch.object(product,'Device',return_value=device),contextlib.redirect_stdout(io.StringIO()):value=product.uninstall(args)
 except Exception as e:error=str(e)
 report=json.loads((args.report/'report.json').read_text());markdown=(args.report/'report.md').read_text()
 checks={'report_survives':(args.report/'report.md').is_file()}
 pending=(adb/'rungic-uninstalling').exists()
 if mode=='normal':checks.update(completed=report['complete'],marker_removed=not pending,runtime_removed=not (adb/'rungic-lxc').exists(),package_removed=not device.installed)
 elif mode=='package_failure':checks.update(rejected=error is not None,incomplete=not report['complete'],marker_kept=pending,raw_failure_retained='Failure [busy]' in json.dumps(report))
 elif mode=='readback_failure':checks.update(rejected=error is not None,incomplete=not report['complete'],marker_kept=pending,readback_failure_recorded='readback_error' in report,no_false_verified_rows=not any(token in line for line in markdown.splitlines() if line.startswith('| `') for token in ('已删除并读回','已删除并确认','确认不存在')))
 elif mode=='finish_readback_marker':checks.update(incomplete_if_marker_present=not pending or not report['complete'])
 else:
  checks.update(readonly=snapshot()==original,incomplete=not report['complete'],no_package_mutation=device.installed)
  if mode=='preview_mount':checks['mount_refusal_reported']='挂载' in json.dumps(report,ensure_ascii=False) or bool(report.get('failed'))
 results.append({'mode':mode,'error':error,'complete':report['complete'],'pending_present':pending,'checks':checks,'passed':all(checks.values()),'gating':mode!='finish_readback_marker'})
 (case/'commands.json').write_text(json.dumps(device.commands,ensure_ascii=False,indent=2)+'\n')
(a.output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n');print(json.dumps(results,ensure_ascii=False,indent=2));sys.exit(0 if all(r['passed'] for r in results if r['gating']) else 1)
