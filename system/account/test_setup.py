import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('setup',Path(__file__).with_name('setup.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
current=SimpleNamespace(pw_uid=1000,pw_gid=1000,pw_name='rungic',pw_dir='/home/rungic')

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
  self.state=Path(self.tmp.name)/'account.json'
  self.sub=(Path(self.tmp.name)/'subuid',Path(self.tmp.name)/'subgid')
  for f in self.sub:f.write_text('rungic:100000:65536\nother:165536:65536\n')
  for p in [patch.object(m,'STATE',self.state),patch.object(m,'SUBORDINATE',self.sub),patch.object(m.pwd,'getpwuid',return_value=current),
            patch.object(m.pwd,'getpwnam',side_effect=KeyError),patch.object(m,'shadow_entry',return_value='!'),
            patch.object(m.os,'getgrouplist',return_value=[1000,29]),
            patch.object(m.grp,'getgrgid',return_value=SimpleNamespace(gr_name='audio'))]:
   p.start();self.addCleanup(p.stop)
 # covers: install.account-setup/E2
 def test_validation(self):
  for name,password in [('root;id','abcdefgh'),('../name','abcdefgh'),('Alice','abcdefgh'),('alice','short'),('alice','abcde\nfgh'),('alice','密'*100)]:
   with self.assertRaises(m.SetupError):m.validate({'username':name,'password':password},current)
  with patch.object(m.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=0)):
   with self.assertRaises(m.SetupError):m.validate({'username':'root','password':'abcdefgh'},current)
  with patch.object(m.os.path,'lexists',return_value=True):
   with self.assertRaises(m.SetupError):m.validate({'username':'bob','password':'abcdefgh'},current)
 # covers: install.account-setup/E4
 def test_success_uses_stdin_and_writes_once(self):
  calls=[]
  def call(argv,payload=None,check=True):
   calls.append((argv,payload));return 1 if argv[0]=='pgrep' else 0
  with patch.object(m,'call',side_effect=call):
   result=m.configure({'username':'alice','password':'test-only-pass'})
   self.assertEqual(result['username'],'alice')
   self.assertEqual(self.state.stat().st_mode & 0o777,0o600)
   self.assertNotIn('test-only-pass',self.state.read_text().strip())
   self.assertTrue(any(a==['usermod','--login','alice','--home','/home/alice','--move-home','rungic'] for a,_ in calls))
   self.assertTrue(any(a[:1]==['runuser'] and a[-3:]==['rehome','/home/rungic','/home/alice'] for a,_ in calls))
   self.assertTrue(any(a==['chpasswd'] and b==b'alice:test-only-pass\n' for a,b in calls))
   self.assertFalse(any('test-only-pass' in ' '.join(a) for a,_ in calls))
   with self.assertRaises(m.SetupError):m.configure({'username':'alice','password':'another-test-pass'})
 # covers: install.account-setup/E7
 def test_rename_moves_subordinate_ids(self):
  def call(argv,payload=None,check=True):return 1 if argv[0]=='pgrep' else 0
  with patch.object(m,'call',side_effect=call):m.configure({'username':'alice','password':'test-only-pass'})
  for f in self.sub:self.assertEqual(f.read_text(),'alice:100000:65536\nother:165536:65536\n')
 # covers: install.account-setup/E7
 def test_subordinate_ids_kept_when_new_name_has_them(self):
  for f in self.sub:f.write_text('rungic:100000:65536\nalice:231072:65536\n')
  m.rename_subordinate('rungic','alice')
  for f in self.sub:self.assertEqual(f.read_text(),'rungic:100000:65536\nalice:231072:65536\n')
 # covers: install.account-setup/E5
 def test_failure_rolls_back(self):
  calls=[]
  def call(argv,payload=None,check=True):
   calls.append((argv,payload))
   if argv==['chpasswd']:raise m.SetupError('simulated')
   return 1 if argv[0]=='pgrep' else 0
  with patch.object(m,'call',side_effect=call):
   with self.assertRaises(m.SetupError):m.configure({'username':'alice','password':'test-only-pass'})
  self.assertFalse(self.state.exists())
  self.assertIn((['chpasswd','--encrypted'],b'alice:!\n'),calls)
  self.assertIn((['usermod','--groups','audio','alice'],None),calls)
  self.assertIn((['usermod','--login','rungic','--home','/home/rungic','--move-home','alice'],None),calls)
 # covers: install.account-setup/E5
 def test_existing_password_cannot_be_bootstrapped(self):
  with patch.object(m,'shadow_entry',return_value='$y$existing'),patch.object(m,'call') as c:
   with self.assertRaises(m.SetupError):m.configure({'username':'alice','password':'test-only-pass'})
   c.assert_not_called()

 # covers: install.account-setup/E6
 def test_status_does_not_create_account(self):
  import io
  from contextlib import redirect_stdout
  output=io.StringIO()
  with patch.object(m.os,'geteuid',return_value=0),patch.object(m.sys,'argv',['setup.py','--status']),patch.object(m,'call') as call,redirect_stdout(output):
   m.main()
  self.assertEqual(json.loads(output.getvalue()),{'configured':False})
  call.assert_not_called()
  self.assertFalse(self.state.exists())
 # covers: install.account-setup/E6
 def test_status_reports_inflight_before_committed_marker(self):
  import io, fcntl
  from contextlib import redirect_stdout
  self.state.write_text('{"configured":true,"username":"alice"}')
  with (self.state.parent/'account.lock').open('a') as held:
   fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
   output=io.StringIO()
   with patch.object(m.os,'geteuid',return_value=0),patch.object(m.sys,'argv',['setup.py','--status']),redirect_stdout(output):
    m.main()
   self.assertEqual(json.loads(output.getvalue()),{'configured':False,'pending':True})
  output=io.StringIO()
  with patch.object(m.os,'geteuid',return_value=0),patch.object(m.sys,'argv',['setup.py','--status']),redirect_stdout(output):
   m.main()
  self.assertTrue(json.loads(output.getvalue())['configured'])

class DesktopRecovery(unittest.TestCase):
 """Run real account configuration followed by the actual host start action.
 Only Android/LXC/systemd are doubles; readiness must follow the stopped session.
 """
 setUp=Tests.setUp
 def controller(self, mode='active', configure=False, broken_restore=False, password_failure=False, root_rejection=False, runs=1):
  root=Path(self.tmp.name)/'device';root.mkdir()
  bindir=root/'bin';bindir.mkdir()
  runtime=root/'run/user/1000';runtime.mkdir(parents=True)
  shared=root/'shared';shared.mkdir()
  env_file=runtime/'rungic-session.env'
  state=root/'session-state';state.write_text(mode)
  log=root/'commands'
  if mode in ('active','user_failed','manager_stopped','bus_unreachable','queued_recent','queued_expired'):env_file.write_text('ready')
  generation=root/'generation';generation.write_text('0')
  def executable(path,text):path.write_text(text);path.chmod(0o755)
  executable(bindir/'systemctl',f'''#!/usr/bin/python3
import sys,time
from pathlib import Path
state=Path({str(state)!r});env=Path({str(env_file)!r});log=Path({str(log)!r})
generation=Path({str(generation)!r})
args=sys.argv[1:]
with log.open('a') as f:f.write(' '.join(args)+'\\n')
if 'stop' in args:
 state.write_text('inactive');env.unlink(missing_ok=True)
elif 'start' in args or 'restart' in args:
 if {broken_restore!r} or state.read_text().strip()=='failed':sys.exit(1)
 state.write_text('active');env.write_text('ready');generation.write_text(str(int(generation.read_text())+1))
elif 'reset-failed' in args:state.write_text('inactive')
elif 'is-active' in args:
 if 'user@1000.service' in args:sys.exit(3 if state.read_text().strip()=='manager_stopped' else 0)
 value=state.read_text().strip()
 if '--quiet' not in args:print(value)
 sys.exit(0 if value=='active' else 3)
elif 'is-failed' in args:sys.exit(0 if state.read_text().strip()=='user_failed' else 1)
elif 'show' in args:
 if 'ActiveState' in args:
  if '--user' in args:
   value=state.read_text().strip()
   print(('activating' if 'plasma-kwin_wayland.service' in args else 'inactive') if value.startswith('queued_') else 'failed' if value=='user_failed' else 'active')
  else:print('active' if state.read_text().strip().startswith('env_') or state.read_text().strip() in ('user_failed','manager_stopped','bus_unreachable','queued_recent','queued_expired') else state.read_text().strip())
 elif 'ActiveEnterTimestampMonotonic' in args:print(int(time.monotonic()*1000000) if state.read_text().strip()=='env_recent' else int((time.monotonic()-10)*1000000) if state.read_text().strip()=='queued_recent' else 0)
 else:print((101 if 'plasma-kwin_wayland.service' in args else 202)+int(generation.read_text())*100)
''')
  helper=bindir/'user-exec'
  executable(helper,f'#!/bin/sh\ncase "$*" in *MainPID*) if [ "$(cat {state})" = queued_recent ]; then echo active > {state}; fi ;; esac\n[ "$(cat {state})" != bus_unreachable ] || exit 1\nif [ "$(cat {state})" = activating ] || [ "$(cat {state})" = env_recent ]; then echo active > {state}; echo ready > {env_file}; fi\n[ -f {env_file} ] || exit 1\nexec "$@"\n')
  base=root/'data/adb/rungic-plasma';base.mkdir(parents=True)
  executable(base/'rungic-plasma-enter',f'''#!/usr/bin/python3
import subprocess,sys
args=sys.argv[1:]
if args[0].endswith('lxc-info'):print('RUNNING');sys.exit(0)
if not args[0].endswith('lxc-attach'):sys.exit(2)
cmd=args[args.index('--')+1:]
if cmd[0].endswith('rungic-plasma-user-exec'):cmd[0]={str(helper)!r}
cmd=[x.replace('/usr/bin/rungic-plasma-user-exec',{str(helper)!r}) for x in cmd]
sys.exit(subprocess.run(cmd).returncode)
''')
  for name in ('android-audio','android-device','android-media','android-clipboard','android-calls'):
   executable(base/name,'#!/bin/sh\nexit 0\n')
  executable(bindir/'id','#!/bin/sh\necho 0\n')
  executable(bindir/'sleep','#!/bin/sh\nexit 0\n')
  executable(bindir/'seq','#!/bin/sh\nprintf "1\\n2\\n"\n')
  environment={**os.environ,'PATH':str(bindir)+':'+os.environ['PATH']}
  original=(Path(__file__).resolve().parents[2]/'system/rungic-plasma').read_text()
  controller=root/'controller'
  controller.write_text(original.replace('/data/',str(root)+'/data/').replace('/run/user/1000',str(runtime)).replace('/mnt/android-shared',str(shared)))
  if configure:
   def call(argv,payload=None,check=True):
    if argv==['chpasswd'] and password_failure:raise m.SetupError('test password failure')
    if argv[0]=='pgrep':return 1
    if argv[0]=='systemctl':
     rc=subprocess.run([str(bindir/'systemctl'),*argv[1:]],env=environment).returncode
     if check and rc:raise m.SetupError('test service failure')
     return rc
    return 0
   self.configuration_error=None
   with patch.object(m,'call',side_effect=call):
    if root_rejection:
     with patch.object(m.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=0)):
      with self.assertRaisesRegex(m.SetupError,'用户名'):m.configure({'username':'root','password':'test-only-pass'})
    try:m.configure({'username':'alice','password':'test-only-pass'})
    except m.SetupError as error:self.configuration_error=str(error)
   self.state_after_configuration=state.read_text().strip()
   self.environment_after_configuration=env_file.exists()
  for _ in range(runs):
   done=subprocess.run(['/bin/sh',str(controller),'start'],env=environment,capture_output=True,text=True,timeout=10)
   if done.returncode:break
  return done,state.read_text().strip(),log.read_text() if log.exists() else ''

 # covers: install.account-setup/E8
 def test_create_account_then_immediate_start_reaches_ready_without_reboot(self):
  done,state,log=self.controller(configure=True)
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertEqual(state,'active')
  self.assertIn('Plasma Mobile ready',done.stdout)
  self.assertTrue(json.loads(self.state.read_text().strip())['configured'])
  self.assertIsNone(self.configuration_error)
  self.assertEqual(self.state_after_configuration,'inactive')
  self.assertFalse(self.environment_after_configuration)
  self.assertFalse(any(line.startswith('restart ') for line in log.splitlines()),log)

 # covers: install.account-setup/E5, install.account-setup/E8
 def test_rollback_then_start_restores_desktop_and_keeps_original_error(self):
  done,state,log=self.controller(configure=True,password_failure=True)
  self.assertEqual(self.configuration_error,'test password failure')
  self.assertFalse(self.state.exists())
  self.assertEqual(self.state_after_configuration,'inactive')
  self.assertFalse(self.environment_after_configuration)
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertFalse(any(line.startswith('restart ') for line in log.splitlines()),log)

 # covers: install.account-setup/E6, install.account-setup/E8
 def test_committed_account_survives_failed_controller_start(self):
  done,_,_=self.controller(configure=True,broken_restore=True)
  self.assertIsNone(self.configuration_error)
  self.assertTrue(json.loads(self.state.read_text().strip())['configured'])
  self.assertEqual(self.state_after_configuration,'inactive')
  self.assertNotEqual(done.returncode,0)
  self.assertNotIn('Plasma Mobile ready',done.stdout)

 # covers: install.account-setup/E5, install.account-setup/E8
 def test_failed_controller_start_does_not_replace_password_error(self):
  done,_,_=self.controller(configure=True,broken_restore=True,password_failure=True)
  self.assertEqual(self.configuration_error,'test password failure')
  self.assertFalse(self.state.exists())
  self.assertNotEqual(done.returncode,0)

 # covers: install.account-setup/E8
 def test_start_recovers_missing_environment_in_running_container(self):
  done,state,log=self.controller('inactive')
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertEqual(state,'active')
  self.assertIn('start rungic-plasma-session.service',log)
  self.assertFalse(any(line.startswith('restart ') for line in log.splitlines()))

 # covers: install.account-setup/E8
 def test_failed_service_recovery_is_not_reported_ready(self):
  done,_,_=self.controller('unknown',broken_restore=True)
  self.assertNotEqual(done.returncode,0)
  self.assertNotIn('Plasma Mobile ready',done.stdout)

 # covers: install.account-setup/E8
 def test_activating_session_is_allowed_to_finish(self):
  done,state,log=self.controller('activating')
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertEqual(state,'active')
  self.assertFalse(any(line.startswith(('start ','restart ')) for line in log.splitlines()),log)

 # covers: install.account-setup/E8
 def test_recent_active_session_waits_for_environment(self):
  done,state,log=self.controller('env_recent')
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertEqual(state,'active')
  self.assertFalse(any(line.startswith(('start ','restart ')) for line in log.splitlines()),log)

 # covers: install.account-setup/E8
 def test_expired_missing_environment_restarts_session(self):
  done,state,log=self.controller('env_expired')
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertEqual(state,'active')
  self.assertEqual(log.splitlines().count('restart rungic-plasma-session.service'),1)

 # covers: install.account-setup/E2, install.account-setup/E8
 def test_rejected_root_then_creation_and_two_starts_use_one_session_start(self):
  done,state,log=self.controller(configure=True,root_rejection=True,runs=2)
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertEqual(state,'active')
  self.assertEqual(log.splitlines().count('start rungic-plasma-session.service'),1)
  self.assertFalse(any(line.startswith('restart ') for line in log.splitlines()),log)

 # covers: install.account-setup/E8
 def test_failed_user_unit_restarts_active_system_session(self):
  done,state,log=self.controller('user_failed')
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertEqual(state,'active')
  self.assertEqual(log.splitlines().count('restart rungic-plasma-session.service'),1)

 # covers: install.account-setup/E8
 def test_stale_environment_with_stopped_manager_or_unreachable_bus_recovers(self):
  for mode in ('manager_stopped','bus_unreachable'):
   with self.subTest(mode=mode):
    # Separate temporary device trees for each actual controller invocation.
    with tempfile.TemporaryDirectory() as temp,patch.object(self.tmp,'name',temp):
     done,state,log=self.controller(mode)
     self.assertEqual(done.returncode,0,done.stderr)
     self.assertEqual(state,'active')
     self.assertEqual(log.splitlines().count('restart rungic-plasma-session.service'),1)

 # covers: install.account-setup/E8
 def test_explicit_start_recovers_start_limit_failed_system_session(self):
  done,state,log=self.controller('failed')
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertEqual(state,'active')
  self.assertLess(log.index('reset-failed rungic-plasma-session.service'),log.index('start rungic-plasma-session.service'))

 # covers: install.account-setup/E8
 def test_recent_system_session_waits_for_queued_user_start_jobs(self):
  done,state,log=self.controller('queued_recent')
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertEqual(state,'active')
  self.assertFalse(any(line.startswith(('start ','restart ')) for line in log.splitlines()),log)

 # covers: install.account-setup/E8
 def test_expired_system_session_recovers_unready_user_units(self):
  done,state,log=self.controller('queued_expired')
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertEqual(state,'active')
  self.assertEqual(log.splitlines().count('restart rungic-plasma-session.service'),1)

 # covers: install.account-setup/E8
 def test_healthy_session_is_kept(self):
  done,_,log=self.controller()
  self.assertEqual(done.returncode,0,done.stderr)
  self.assertFalse(any(line.startswith('restart ') for line in log.splitlines()),log)

if __name__ == "__main__":
    unittest.main()
