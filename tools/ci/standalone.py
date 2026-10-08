#!/usr/bin/env python3
"""Build, verify, install or remove an independent Rungic payload.

Use a prepared Android base. An existing runtime prevents a new installation.
Upgrade and account migration are separate operations. Removal requires explicit confirmation.
Installation can resume its own incomplete release. Supply the trusted manifest digest,
exact hardware serial and ADB port. This tool has no device discovery or default handset.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import select
import uuid
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import cast_payload
import build_artifact
FILES = {'rootfs.img.gz', 'host-seed.tar.gz', 'rungic.apk', 'termux.apk',
         'termux-prefix.tar.gz', 'rungic-sparse-write', 'firstboot.sh', 'service.sh',
         'boot-dispatch.sh', 'seed.env', 'device-spec.json', 'rootfs-report.json', 'host-seed-report.json',
         'kernel-report.json', 'packages.lock.tsv', 'release.json'}
from install_paths import (PATHS, HOME, PRESERVED, COMPAT, UNINSTALLED, PENDING,
                           MAINTENANCE_LOCK, FIRSTBOOT_LOCK, RETAINED, APP, APP_DATA, staging_path)
REMOTE = PATHS['RUNGIC_PAYLOAD']


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def atomic_text(path, content):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)



def write(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def write_removal_report(path, result):
    result['path_results'] = []
    for target in result.get('delete_plan', []):
        operation = ('deleted' if target in result.get('deleted', []) else
                     'absent' if target in result.get('already_absent', []) else
                     'failed_attempt' if target in result.get('attempted', []) else 'not_attempted')
        after = result.get('after', {})
        if result.get('readback_error') or not after:
            readback = 'unknown'
        elif target in after.get('paths', {}):
            value = after['paths'][target]
            readback = 'present' if value is True else 'absent' if value is False else 'unknown'
        elif target.startswith('/data/local/tmp/rungic-') and 'stages' in after:
            readback = 'present' if target in after['stages'] else 'absent'
        else:
            readback = 'unknown'
        result['path_results'].append({'path': target, 'operation': operation, 'readback': readback})
    write(path, result)
    title = '只读预览' if result.get('plan_only') else ('范围内卸载完成' if result.get('complete') else '卸载未完成')
    lines = ['# Rungic 卸载报告', '', '**' + title + '**', '',
             f"设备：{result.get('serial', '未确认')}。ADB 端口：{result.get('adb_port', '未确认')}。",
             '默认保留当前 Linux 家目录。只有显式 --purge 才永久删除它。以前保留的家目录始终保留。', '',
             '本报告不证明首装、重启后的旧种子拦截或完整候选质量通过。', '']
    if result.get('error') and any('verified' in entry for entry in result.get('steps', [])):
        next_step = ('卸载动作已完成，此前已独立确认卸载标记清除。最终完成判定时未确认所有条件，原因见下文。下一步：运行一次只读卸载预览（同样的选项，不加 --yes-delete），核对现场。'
                     if result.get('marker_cleared') else
                     '下一步：保持现场和未完成标记，先核对失败步骤的读回。修正后，用同样的 --purge 选择续跑。')
        lines[4:4] = [next_step, '']
    lines += ['当前模式：' + ('--purge，永久删除当前 Linux 家目录，不能恢复。' if result.get('purge') else '保留当前 Linux 家目录。'),
              '开始时间：' + result.get('started_at', '未进入执行计划') + '。', '']
    before = result.get('before', {})
    if before:
        lines += ['安装前实际版本：' + ('`' + before['release'] + '`' if before.get('release') else '未读到') + '。',
                  '底座与内核：' + ('。'.join(before.get('base', [])) or '未读到'), '']
        sources = before.get('apk_sources')
        if sources is not None:
            def version(entry):
                if not entry: return '版本未读到'
                name, code = entry.get('version_name'), entry.get('version_code')
                return (name + '（版本编号 ' + (code or '未读到') + '）') if name else ('版本编号 ' + (code or '未读到') + '，版本名未读到')
            active = sources.get('active')
            flags = (active or {}).get('flags', '').split()
            label = '用户 0 更新包' if 'UPDATED_SYSTEM_APP' in flags else '用户 0 APK'
            installed = before.get('user_packages')
            current = version(active) if installed else '未安装' if installed == '' else '安装状态未确认'
            lines += [label + '：' + current + '。', '系统分区 APK：' + version(sources.get('system')) + '。', '']
        else:
            lines += ['APK 版本（旧报告未区分来源）：' + ('。'.join(before.get('apk_versions', [])) or '未读到'), '']
    def readable_error(error):
        command = re.search(r"Command '(.*)' returned non-zero exit status (\d+)\.", error)
        if command:
            phase = '读取最终现场' if result.get('phase') == 'finish-snapshot' else '当前步骤'
            return error[:command.start()] + phase + '的命令失败（退出码 ' + command[2] + '）。原始命令见 report.json。'
        return error
    if result.get('error'):
        lines += ['失败原因（程序报告）：' + readable_error(result['error']), '']
    pm_steps = [entry for entry in result.get('steps', []) if 'verified' in entry and entry['name'] != 'persist-user-app']
    if pm_steps:
        lines += ['## Android 包操作', '', '成败取决于独立读回。命令输出和退出码只作为证据。', '']
        summaries = {'clear-app-data': '用户 0 的两种数据存储都为空。',
                     'remove-user-app': '用户 0 的应用列表里已没有 Rungic。'}
        for entry in pm_steps:
            summary = summaries.get(entry['name'], '')
            if entry['name'] == 'remove-apk-update' and entry.get('verified'):
                code = entry.get('readback', {}).get('versionCode')
                summary = '只剩系统分区的底座 APK' + ('（版本编号 ' + str(code) + '）' if code is not None else '') + '。'
            lines += ['- ' + entry['label'] + '：' + ('已确认。' + summary if entry['verified'] else '未确认。')]
            if not entry['verified']:
                lines += ['  读回：`' + json.dumps(entry.get('readback'), ensure_ascii=False) + '`。',
                          '  命令退出码：`' + str(entry.get('returncode')) + '`。原始输出：',
                          '```text', entry['output'], '```', '']
        lines += ['', '各步完整读回、命令退出码与原始输出见 report.json。', '']
    persistence = next((entry for entry in result.get('steps', []) if entry['name'] == 'persist-user-app'), None)
    if persistence and not persistence['verified'] and result.get('error'):
        lines += ['下一步：等 1 分钟后，用同样的参数重新运行卸载。保留当前现场和未完成标记。', '']
    if persistence:
        warning = '现在重启手机，系统自带的 Rungic 可能会恢复。' if result.get('package_kind') == 'system' else '请先不要重启手机。'
        lines += ['卸载结果是否已保存：' + ('已确认。' if persistence['verified'] else '未确认。' + warning) + '详细读回证据见 report.json。', '']
    if result.get('before_finish'):
        lines += ['最终现场：清除卸载标记后重新读取。清除前快照保留在 report.json 的 before_finish。' if result.get('complete') else '已保留清除标记前快照。最终现场未确认，不能据旧快照判定完成。', '']
    if result.get('readback_error'):
        lines += ['最终读回失败（程序报告），不能确认当前现场：' + readable_error(result['readback_error']), '']
    if 'preserved_home' in result:
        lines += ['保留位置：`' + result['preserved_home'] + '`。移动后已核对原家目录 inode。', '']
    if 'preflight' in result:
        display = {'paths': '受保护路径', 'processes': '相关进程', 'mounts': '挂载点', 'wfd_config': '投屏配置挂载',
                   'images': 'loop／dm 设备', 'home': '家目录挂载',
                   'PASS': '通过', 'BLOCKED': '阻塞', 'UNKNOWN': '未知'}
        lines += ['## 只读预检', '', result['preflight_note'], '',
                  '当前发现需要重新检查的项目：' + (display.get(result['would_stop_at'], result['would_stop_at']) if result['would_stop_at'] else '未发现阻塞项') + '。', '',
                  '| 检查 | 当前结果 | 原因 |', '| --- | --- | --- |']
        diagnostics = []
        for entry in result['preflight']:
            reason = entry['reason'].replace('请先解除挂载并重试。', '执行时会先停止服务，再重新检查。')
            if entry['name'] == 'mounts' and entry['state'] == 'BLOCKED':
                diagnostics.append(reason)
                reason = '家目录或运行目录下面还有挂载点。执行时会先停止服务，再重新检查。'
            elif entry['name'] == 'processes' and entry['state'] == 'BLOCKED':
                diagnostics.append(reason)
                reason = '相关进程仍在运行。执行时会先停止服务，再重新检查。'
            elif entry['name'] == 'home' and entry['state'] == 'PASS':
                reason = '已确认家目录与保留目录处于同一挂载范围。' if not result.get('purge') else '已确认当前家目录未单独挂载。执行 purge 会永久删除它。'
            lines.append(f"| {display.get(entry['name'], entry['name'])} | {display.get(entry['state'], entry['state'])} | {reason.replace('|', '&#124;').replace(chr(10), '<br>')} |")
        lines.append('')
        for diagnostic in diagnostics:
            lines += ['当前匹配的诊断：', '```text', diagnostic, '```', '']
    if result.get('plan_only'):
        lines += ['## 删除范围', '', '| 路径 | 执行时计划删除 | 现在是否存在 |', '| --- | --- | --- |']
    else:
        lines += ['## 删除范围', '', '| 路径 | 操作结果 | 独立读回 |', '| --- | --- | --- |']
    operations = {'deleted': '已删除（脚本报告）', 'absent': '删除前不存在',
                  'failed_attempt': '已尝试删除，删除脚本没有报告成功', 'not_attempted': '未尝试删除'}
    observations = {'absent': '确认不存在', 'present': '仍然存在', 'unknown': '读回未完成，未确认'}
    absent_rows = 0
    for entry in result['path_results']:
        if result.get('plan_only'):
            target = entry['path']
            value = before.get('paths', {}).get(target)
            if value is None and target.startswith('/data/local/tmp/rungic-') and 'stages' in before:
                value = target in before['stages']
            present = '存在' if value is True else '不存在' if value is False else '未确认'
            lines.append(f"| `{target}` | 计划删除 | {present} |")
        else:
            if entry['operation'] == 'absent' and entry['readback'] == 'absent':
                absent_rows += 1
                continue
            observed = ('现在存在（删除后重新出现）' if entry['readback'] == 'present' and entry['operation'] in ('absent', 'deleted') else observations[entry['readback']])
            lines.append(f"| `{entry['path']}` | {operations[entry['operation']]} | {observed} |")
    if absent_rows:
        lines += ['', f'另有 {absent_rows} 项删除前就不存在，删除后确认不存在。完整列表见 report.json。']
    if not result.get('plan_only') and any(entry['operation'] == 'not_attempted' for entry in result['path_results']):
        lines += ['', '停止服务可能改变运行状态。“未尝试删除”不表示运行状态完全未变。']
    lines += ['', '## 明确保留', '', '| 路径 | 原因 |', '| --- | --- |']
    for target, reason in result.get('expected_retained', {}).items():
        lines.append(f'| `{target}` | {reason} |')
    if result.get('outside_scope'):
        lines += ['', '## 范围外残留', '', '以下项没有删除，也没有验证其内容：', '']
        lines += ['- `' + entry['path'] + '`' for entry in result['outside_scope']]
    lines += ['', '## 恢复家目录', '',
              '1. 新安装不会自动读取保留的家目录。先完成新账户创建，再停止 Linux。',
              '2. 确认新旧账户同名，家目录路径和数字 UID/GID 一致，确认 Shared 已解除挂载。',
              '3. 保留新家目录作为回退，再把保留的家目录移回原 state/home 家目录路径。不得直接覆盖正在运行的目录。',
              '4. 保留原所有者、权限、链接、ACL 和 SELinux 标签。核对内容与访问权限后才启动 Linux。',
              '5. 恢复操作须单独授权和验收，本工具不会执行恢复。', '',
              '原始现场、操作输出、失败与未尝试范围见同目录 report.json。', '']
    atomic_text(Path(path).with_suffix('.md'), '\n'.join(lines))

def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', value):
        raise ValueError('invalid release id')
    return value


def verify(folder, expected=None):
    folder = Path(folder)
    manifest_file = folder / 'manifest.json'
    if expected and digest(manifest_file) != expected:
        raise ValueError('manifest digest differs from trusted input')
    m = read(manifest_file)
    if m.get('schema') not in (1, 2) or m.get('kind') != 'rungic-standalone':
        raise ValueError('unsupported standalone manifest')
    valid_id(m['release'])
    if set(m['files']) != FILES | ({'build-manifest.json'} if m['schema'] == 2 else set()):
        raise ValueError('payload file inventory mismatch')
    for name, entry in m['files'].items():
        p = folder / name
        if p.is_symlink() or not p.is_file() or p.stat().st_size != entry['bytes'] or digest(p) != entry['sha256']:
            raise ValueError(f'payload verification failed: {name}')
    if m['schema'] == 2:
        validate_build_manifest(read(folder / 'build-manifest.json'), m['files'])
    return m


def validate_build_manifest(manifest, files):
    if manifest.get('schema') != 1 or not manifest.get('components'):
        raise ValueError('missing component build fingerprints')
    for record in manifest['components'].values():
        build_artifact.validate_record(record)
    fingerprints = {record['input_sha256']: record for record in manifest['components'].values()}
    for record in manifest['components'].values():
        for dependency in record['inputs']['dependencies'].values():
            if 'input_sha256' in dependency:
                parent = fingerprints.get(dependency['input_sha256'])
                if parent is None or not any(output['sha256'] == dependency.get('file_sha256') for output in parent['outputs'].values()):
                    raise ValueError('component dependency build is missing or mismatched')
    bindings = manifest.get('bindings', {})
    if set(bindings) != {'rootfs.img.gz', 'host-seed.tar.gz', 'rungic.apk', 'rungic-sparse-write'}:
        raise ValueError('missing primary artifact build bindings')
    for name, binding in bindings.items():
        record = manifest['components'][binding['component']]
        artifact = record['outputs'][binding['output']]
        if artifact != files[name]:
            raise ValueError(f'build record does not match payload: {name}')


def collect_build_manifest(plan_file, files):
    """Verify each expected recipe before packaging; archive portable records."""
    plan = read(plan_file)
    records = {}
    for name, entry in plan['components'].items():
        expected = build_artifact.inputs(read(entry['recipe']))
        record = build_artifact.verify(entry['directory'], expected)
        if record['component'] != name:
            raise ValueError('component name differs from build plan')
        records[name] = record
    result = {'schema': 1, 'components': records, 'bindings': plan['bindings'],
              'binary_inputs_policy': 'Pinned binary dependencies are recorded as inputs, not source cache hits.'}
    validate_build_manifest(result, files)
    return result


def validate_cast_host(host):
    build = host.get('cast_build')
    if not isinstance(build, dict) or build.get('schema') != 1 or build.get('inputs') != cast_payload.build_inputs():
        raise ValueError('host casting build is missing or stale; rebuild the host seed')
    if not build.get('jar_sha256') or build['jar_sha256'] != host.get('cast_jar_sha256'):
        raise ValueError('host casting jar does not match its build provenance')


def pack(args):
    out = args.output
    if out.exists():
        raise ValueError('output already exists; releases are immutable')
    valid_id(args.release_id)
    root = read(args.rootfs_report)
    host = read(args.host_report)
    kernel = read(args.kernel_report)
    spec = read(args.spec)
    release = read(args.package_release)
    if not all(root.get(k) is True for k in ('home_layout_checked', 'fresh_account_checked', 'preinstalled_apps_checked')):
        raise ValueError('rootfs lacks required build checks')
    if root['arch'] != 'arm64' or root['account_status_protocol'] != 2 or root['filesystem_check'] != 0:
        raise ValueError('rootfs architecture/protocol/filesystem check failed')
    if not host.get('home_layout_checked') or not host.get('fresh_account_checked') or host['arch'] != 'aarch64':
        raise ValueError('host seed is not a fresh ARM64 runtime')
    validate_cast_host(host)
    for file, expected in ((args.rootfs_gz, root['compressed_sha256']),
                           (args.host_seed, host['archive_sha256']),
                           (args.package_lock, root['package_lock_sha256']),
                           (args.package_release, root['release_sha256'])):
        if digest(file) != expected:
            raise ValueError(f'report does not match input: {file}')
    if root['release_version'] != release['version']:
        raise ValueError('package release mismatch')
    sources = {'rootfs.img.gz': args.rootfs_gz, 'host-seed.tar.gz': args.host_seed,
               'rungic.apk': args.apk, 'termux.apk': args.deps / 'termux.apk',
               'termux-prefix.tar.gz': args.deps / 'termux-prefix.tar.gz',
               'rungic-sparse-write': args.deps / 'rungic-sparse-write',
               'firstboot.sh': HERE / 'rungic-firstboot.sh',
               'service.sh': HERE / 'rungic-install-service.sh',
               'boot-dispatch.sh': HERE / 'rungic-install-boot-dispatch.sh',
               'device-spec.json': args.spec, 'rootfs-report.json': args.rootfs_report,
               'host-seed-report.json': args.host_report, 'kernel-report.json': args.kernel_report,
               'packages.lock.tsv': args.package_lock, 'release.json': args.package_release}
    build_manifest = collect_build_manifest(args.build_plan, {name: {'sha256': digest(path), 'bytes': path.stat().st_size} for name, path in sources.items()})
    out.mkdir(parents=True)
    write(out / 'build-manifest.json', build_manifest)
    for name, path in sources.items():
        subprocess.run(['cp', '--reflink=auto', str(path), str(out / name)], check=True)
    env = {'RELEASE_ID': args.release_id, 'HOST_SEED_SHA256': digest(out / 'host-seed.tar.gz'),
           'ROOTFS_GZ_SHA256': root['compressed_sha256'], 'ROOTFS_SHA256': root['rootfs_sha256'],
           'ROOTFS_BYTES': str(root['rootfs_bytes']), 'TERMUX_APK_SHA256': digest(out / 'termux.apk'),
           'TERMUX_PREFIX_SHA256': digest(out / 'termux-prefix.tar.gz'),
           'RUNGIC_APK_SHA256': digest(out / 'rungic.apk'),
           'SPARSE_WRITE_SHA256': digest(out / 'rungic-sparse-write'),
           'PHONE_HTTP_PROXY': spec['deployment']['phone_http_proxy']}
    (out / 'seed.env').write_text(''.join(f'{k}={shlex.quote(v)}\n' for k, v in env.items()))
    m = {'schema': 2, 'kind': 'rungic-standalone', 'release': args.release_id,
         'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
         'source_dirty': bool(subprocess.check_output(['git', 'diff', '--name-only'], text=True).strip()),
         'fingerprint': spec['identity']['fingerprint'], 'product': spec['identity']['product'],
         'kernel_release': kernel['kernel_banner'].split('Linux version ', 1)[1].split(' ', 1)[0],
         'boot_sha256': kernel['boot_sha256'], 'boot_bytes': kernel['boot_bytes'],
         'rootfs_bytes': root['rootfs_bytes'], 'minimum_battery_percent': spec['release_requirements']['minimum_battery_percent'],
         'package_release': root['release_version'],
         'files': {n: {'bytes': (out / n).stat().st_size, 'sha256': digest(out / n)} for n in sorted(FILES | {'build-manifest.json'})}}
    write(out / 'manifest.json', m)
    verify(out)
    print(json.dumps({'folder': str(out), 'manifest_sha256': digest(out / 'manifest.json')}))


# The active root provider, as system/root-provider decides it (Magisk while its runtime is up,
# else KernelSU, else an error): sets BB to its BusyBox and RUNGIC_ROOT. Inlined into each root
# script because removal runs after the controller (and with it root-provider) is gone.
ROOT_PROVIDER_SH = ("if [ -x /debug_ramdisk/magisk ]; then BB=/data/adb/magisk/busybox; RUNGIC_ROOT=magisk; "
                    "elif [ -x /data/adb/ksud ]; then BB=/data/adb/ksu/bin/busybox; RUNGIC_ROOT=kernelsu; "
                    "else echo 'No active root provider: neither Magisk (/debug_ramdisk/magisk) nor KernelSU "
                    "(/data/adb/ksud)' >&2; exit 1; fi")

class Device:
    def __init__(self, args):
        self.adb = [args.adb, '-P', str(args.adb_port), '-s', args.serial]

    def shell(self, script, root=False, timeout=120):
        if getattr(self, '_lease', None) is not None and self._lease.poll() is not None:
            raise ValueError('The device maintenance lock was lost.')
        return subprocess.check_output(self.adb + ['shell'] + (['su', '-c', 'sh'] if root else ['sh']),
                                       input=('set -eu\n' + script + '\n').encode(), timeout=timeout, stderr=subprocess.STDOUT).decode().strip()

    @contextmanager
    def maintenance(self, removal=False):
        locks = [MAINTENANCE_LOCK]
        if removal:
            locks += [FIRSTBOOT_LOCK, '/data/adb/rungic-install.lock',
                      '/data/adb/rungic-cast-install.lock']
        worker = "printf 'RUNGIC_LOCKED\\n'; read -r release"
        command = '/system/bin/sh -c ' + shlex.quote(worker)
        for lock in reversed(locks):
            command = '"$BB" flock -n ' + shlex.quote(lock) + ' ' + command
        # The root provider's BusyBox, resolved by the shell that holds the locks.
        command = ROOT_PROVIDER_SH + '; ' + command
        process = subprocess.Popen(self.adb + ['shell', 'su', '-c', 'sh'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        try:
            process.stdin.write(('set -eu\n' + command + '\n').encode())
            process.stdin.flush()
            if not select.select([process.stdout], [], [], 15)[0]:
                raise ValueError('The device maintenance lock did not respond.')
            line = process.stdout.readline().decode(errors='replace').strip()
            if line != 'RUNGIC_LOCKED':
                raise ValueError('Installation, first boot or removal is active: ' + line)
            self._lease = process
            yield
            if process.poll() is not None:
                raise ValueError('The device maintenance lock was lost.')
        finally:
            self._lease = None
            if process.stdin:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
            if process.stdout:
                process.stdout.close()

    def push(self, local, remote):
        subprocess.run(self.adb + ['push', str(local), remote], check=True, timeout=1800)


def preflight(device, m):
    actual = device.shell('id -u; getprop ro.build.fingerprint; getprop ro.product.device; uname -r; getenforce; getprop ro.boot.slot_suffix; cat /sys/class/power_supply/battery/capacity', root=True).splitlines()
    if len(actual) != 7 or actual[:5] != ['0', m['fingerprint'], m['product'], m['kernel_release'], 'Enforcing']:
        raise ValueError(f'prepared base does not match payload: {actual}')
    slot = actual[5]
    if slot not in ('_a', '_b') or int(actual[6]) < m['minimum_battery_percent']:
        raise ValueError('unsupported slot or insufficient battery')
    size = m['boot_bytes']
    if not isinstance(size, int) or not 0 < size <= 128 * 1024**2 or size % 4096:
        raise ValueError('invalid boot report size')
    boot = device.shell(f'dd if=/dev/block/by-name/boot{slot} bs=4096 count={size // 4096} 2>/dev/null | sha256sum', root=True).split()[0]
    if boot != m['boot_sha256']:
        raise ValueError('running base boot image differs from verified candidate')
    return {'fingerprint': actual[1], 'kernel': actual[3], 'selinux': actual[4], 'slot': slot, 'boot_sha256': boot}


def install(args):
    folder = args.payload.resolve()
    m = verify(folder, args.manifest_sha256)
    device = Device(args)
    evidence = preflight(device, m)
    with device.maintenance():
        return _install(args, device, folder, m, evidence)


def _install(args, d, folder, m, evidence):
    rid = m['release']
    # Refuse replacement, even when it would fit: a full update needs data migration.
    d.shell(f'''test ! -e {PENDING} || {{ echo '上次卸载没有完成，请先重新运行卸载。' >&2; exit 1; }}
    if [ -f {REMOTE}/active.env ]; then
        grep -qxF {shlex.quote('RELEASE_ID=' + rid)} {REMOTE}/active.env
        test "$(cat {REMOTE}/manifest.sha256)" = {shlex.quote(args.manifest_sha256)}
    else
        test ! -e {PATHS['RUNGIC_LXC']}
        test ! -e {PATHS['RUNGIC_CONTROLLER']}
    fi
    # An active root provider with the BusyBox the installer relies on.
    {ROOT_PROVIDER_SH}
    test -x "$BB"
    df -k /data | tail -n 1 | awk -v need={m['rootfs_bytes'] // 1024 + 4 * 1024**2} '{{if ($4 < need) exit 1}}'
    ''', root=True)
    stage = staging_path(rid)
    d.shell(f'mkdir -p {stage}')
    for name in sorted(set(m['files']) | {'manifest.json'}):
        d.push(folder / name, stage + '/' + name)
    checks = '\n'.join(f"echo {shlex.quote(v['sha256'] + '  ' + stage + '/' + n)} | sha256sum -c -" for n, v in m['files'].items())
    d.shell(checks, root=True, timeout=300)
    # Package and permission operations need the platform permission, which the shell identity
    # usually holds. A root manager whose domain is denied it (Magisk: shell works, KernelSU:
    # the ksu domain is the one that works) is served by the root fallback.
    def platform(script, **kwargs):
        try:
            return d.shell(script, **kwargs)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return d.shell(script, root=True, **kwargs)

    if not d.shell('pm path com.termux || true'):
        print(platform(f'pm install -r {stage}/termux.apk', timeout=180), flush=True)
    print(platform(f'pm install -r {stage}/rungic.apk', timeout=180), flush=True)
    permissions = ('RECORD_AUDIO', 'CAMERA', 'POST_NOTIFICATIONS', 'BLUETOOTH_CONNECT', 'BLUETOOTH_SCAN', 'READ_PHONE_STATE')
    # Runtime permissions are optional as before (the app asks itself): one that neither identity
    # can grant is reported and the install goes on.
    for permission in permissions:
        try:
            platform(f'pm grant {APP} android.permission.{permission}')
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print(f'permission {permission} not granted; the app asks for it when needed', flush=True)
    try:
        platform(f'appops set {APP} SYSTEM_ALERT_WINDOW allow')
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print('SYSTEM_ALERT_WINDOW not allowed; the app asks when casting (optional)', flush=True)
    # Root-owned durable descriptor and payload, published before the controller exists.
    d.shell(f'''mkdir -p {REMOTE}/payload
        chmod 700 {REMOTE} {REMOTE}/payload
        cp -a {stage}/. {REMOTE}/payload/
        chown -R 0:0 {REMOTE}
        {checks.replace(stage + '/', REMOTE + '/payload/')}
        chmod 700 {REMOTE}/payload/rungic-sparse-write
        # Old init_boot rewrites service.d from product on every boot. Magisk's
        # documented product overlay redirects that immutable caller as well.
        if [ -f /product/etc/rungic/firstboot.sh ]; then
            legacy={PATHS['RUNGIC_LEGACY']}
            mkdir -p "$legacy"
            chmod 700 "$legacy"
            if [ ! -f "$legacy/firstboot.sh" ]; then
                cp /product/etc/rungic/firstboot.sh "$legacy/firstboot.sh"
                chmod 700 "$legacy/firstboot.sh"
            fi
            mod={COMPAT}
            mkdir -p "$mod/system/product/etc/rungic"
            printf '%s\\n' 'id=rungic-install-compat' 'name=Rungic independent installer compatibility' 'version=1' 'versionCode=1' 'author=Rungic' 'description=Route legacy product seeding to the selected independent release.' > "$mod/module.prop"
            cp {REMOTE}/payload/boot-dispatch.sh "$mod/system/product/etc/rungic/firstboot.sh"
            chmod 755 "$mod" "$mod/system" "$mod/system/product" "$mod/system/product/etc" "$mod/system/product/etc/rungic" "$mod/system/product/etc/rungic/firstboot.sh"
            chmod 644 "$mod/module.prop"
            chcon -R u:object_r:system_file:s0 "$mod/system"
            test ! -e "$mod/disable"
            test ! -e "$mod/remove"
        fi
        echo {shlex.quote(args.manifest_sha256)} > {REMOTE}/manifest.sha256
        echo RELEASE_ID={rid} > {REMOTE}/active.env.tmp
        mv {REMOTE}/active.env.tmp {REMOTE}/active.env
        rm -f {UNINSTALLED}
        files=/data/user/0/{APP}/files
        mkdir -p "$files"
        owner=$(stat -c %u /data/user/0/{APP})
        case "$owner" in ''|*[!0-9]*) exit 1 ;; esac
        test "$owner" -ge 10000
        # The verified APK starts shared root bridges when its loading UI opens.
        # Grant before publishing the entry point, not only at seed completion.
        # Magisk needs the policy row pre-granted; KernelSU has no SQL policy
        # store, so the user grants root to the app in its manager instead.
        {ROOT_PROVIDER_SH}
        if [ "$RUNGIC_ROOT" = magisk ]; then
            # Fixed Magisk 31 schema: INSERT returns no SQL NULL (docs/39).
            /debug_ramdisk/magisk --sqlite "INSERT OR REPLACE INTO policies (uid,policy,until,logging,notification) VALUES($owner,2,0,1,1)"
        else
            echo "KernelSU: grant root to {APP} in the KernelSU manager to enable the shared bridges."
        fi
        label=$(ls -dZ /data/user/0/{APP} | cut -d ' ' -f1)
        echo RELEASE_ID={rid} > "$files/rungic-install-source.properties.tmp"
        chown "$owner:$owner" "$files" "$files/rungic-install-source.properties.tmp"
        chmod 600 "$files/rungic-install-source.properties.tmp"
        chcon "$label" "$files" "$files/rungic-install-source.properties.tmp"
        mv "$files/rungic-install-source.properties.tmp" "$files/rungic-install-source.properties"
        mkdir -p /data/adb/service.d
        cp {REMOTE}/payload/service.sh {PATHS['RUNGIC_BOOT_SERVICE']}
        chmod 700 {PATHS['RUNGIC_BOOT_SERVICE']}
        sync
        ''', root=True)
    print('Payload verified; starting independent installation.', flush=True)
    # Device process survives a host disconnect; the service entry retries after reboot.
    # KernelSU keeps its BusyBox elsewhere than Magisk.
    d.shell(f'''{ROOT_PROVIDER_SH}
        "$BB" setsid /system/bin/sh {REMOTE}/payload/firstboot.sh {REMOTE}/payload </dev/null >{PATHS['RUNGIC_LAUNCH_LOG']} 2>&1 &''', root=True)
    print(json.dumps({'state': 'installing', 'release': rid, 'base': evidence,
                      'status_command': 'standalone.py status with the same --serial and --adb-port'}))


def removal_apk_versions(info):
    """Keep active and hidden system versions separate; ambiguous metadata stays unknown."""
    active_text, _, system_text = info.partition('Hidden system packages:')
    def package(text):
        blocks = re.findall(r'^\s*Package \[' + re.escape(APP) + r'\][^\n]*:\n(.*?)(?=^\s*Package \[|\Z)', text, re.M | re.S)
        if len(blocks) != 1:
            return None
        body = blocks[0]
        result = {}
        for key, expression in (('version_code', r'\bversionCode=(\d+)'),
                                ('version_name', r'^\s*versionName=([^\n]+)'),
                                ('path', r'^\s*codePath=([^\n]+)')):
            values = re.findall(expression, body, re.M)
            if len(values) == 1:
                result[key] = values[0].strip()
        # Android prints both aliases. Permission flags inside user records are
        # unrelated; require package-level aliases to be unique and agree.
        flags = re.findall(r'^\s*(pkgFlags|flags)=\[([^\]]*)\]', body, re.M)
        if flags and len({name for name, _ in flags}) == len(flags) and len(
                {tuple(sorted(value.split())) for _, value in flags}) == 1:
            result['flags'] = flags[0][1].strip()
        return result or None
    active = package(active_text)
    system = package(system_text)
    if not system and active:
        flags = active.get('flags', '').split()
        if 'SYSTEM' in flags and 'UPDATED_SYSTEM_APP' not in flags:
            system = active.copy()
    return {'active': active, 'system': system}


def termux_package_path(device):
    # AOSP displayPackageFilePath returns 1 without output for an absent package;
    # errors (including RemoteException) have another status or diagnostic text.
    value = device.shell('pm path com.termux || { code=$?; [ "$code" = 1 ]; }')
    if value and any(not re.fullmatch(r'package:/[^\s]+', line) for line in value.splitlines()):
        raise ValueError('Termux 安装路径读回未知：' + value)
    return value


def uninstall_state(device, extra_paths=()):
    """Read exact owned paths and report other Rungic names without deleting them."""
    owned = list(dict.fromkeys(list(PATHS.values()) + list(RETAINED) + [PENDING, HOME, f'/data/user/0/{APP}'] + list(extra_paths)))
    script = '\n'.join(
        f'if [ -e {shlex.quote(path)} ] || [ -L {shlex.quote(path)} ]; then printf "%s\\t1\\n" {shlex.quote(path)}; else printf "%s\\t0\\n" {shlex.quote(path)}; fi'
        for path in owned)
    output = device.shell(script, root=True)
    paths = {}
    for line in output.splitlines():
        fields = line.split('\t')
        if len(fields) != 2 or fields[0] not in owned:
            raise ValueError('路径读回格式未知：' + line.replace('\t', '，'))
        if fields[0] in paths:
            raise ValueError('路径读回有重复条目：' + fields[0])
        if fields[1] not in ('0', '1'):
            raise ValueError('路径读回状态未知：' + fields[0] + '，读到的值为 ' + fields[1])
        paths[fields[0]] = fields[1] == '1'
    if set(paths) != set(owned):
        raise ValueError('路径读回缺项：' + '、'.join(path for path in owned if path not in paths))
    pending = device.shell(f'test ! -L {PENDING}; if [ -e {PENDING} ]; then test -f {PENDING}; cat {PENDING}; fi', root=True)
    stages = device.shell("find /data/local/tmp -maxdepth 1 -type d -name 'rungic-*' -print", root=True).splitlines()
    packages, _ = package_readback(device, 'remove-user-app')
    uninstalled = device.shell(f'test ! -L {UNINSTALLED}; if [ -e {UNINSTALLED} ]; then test -f {UNINSTALLED}; cat {UNINSTALLED}; fi', root=True)
    apk_info = device.shell(f'dumpsys package {APP} || true')
    return {'paths': paths, 'pending': pending, 'uninstalled': uninstalled, 'stages': stages,
            'other_paths': device.shell("find /data/adb -maxdepth 1 -name '*rungic*' -print", root=True).splitlines(),
            'user_packages': '\n'.join(line for line in packages['user_packages'] if line == f'package:{APP}'),
            'package_paths': device.shell(f'pm path {APP} || true'),
            'termux_path': termux_package_path(device),
            'base': device.shell('getprop ro.build.fingerprint; uname -r; getenforce', root=True).splitlines(),
            'apk_versions': [line.strip() for line in apk_info.splitlines() if re.search(r'\bversionCode=|\bversionName=', line)],
            'apk_sources': removal_apk_versions(apk_info),
            'release': device.shell(f'if [ -f {REMOTE}/active.env ]; then cat {REMOTE}/active.env; fi', root=True)}


def final_removal_problems(before, after, targets, retained, kind):
    """Decide from the final observations only. Missing/unknown is never absence."""
    problems = []
    paths = after.get('paths', {})
    for path in targets:
        if paths.get(path) is not False:
            problems.append({'path': path, 'reason': '最终读回存在。' if paths.get(path) is True else '最终路径状态未知或缺项。'})
    for path in retained:
        if paths.get(path) is not True:
            problems.append({'path': path, 'reason': '最终读回未确认保留项存在。'})
    if paths.get(PENDING) is not False or after.get('pending') != '':
        problems.append({'path': PENDING, 'reason': '最终现场未确认卸载标记不存在。'})
    if not isinstance(before.get('termux_path'), str) or not isinstance(after.get('termux_path'), str) or after['termux_path'] != before['termux_path']:
        problems.append({'path': 'com.termux', 'reason': '最终读回未确认 Termux 安装路径保持一致。'})
    package = after.get('package_validation', {})
    if package.get('kind') != kind or package.get('user_absent') is not True or package.get('data_empty') is not True or package.get('persisted') is not True or (kind == 'system' and package.get('system_base') is not True):
        problems.append({'path': APP, 'reason': '最终读回未确认对应包类型、用户卸载、数据清空与保存状态。'})
    return problems


def uninstall_root_script(purge=False, operation_id=None, stages=(), preview=False, package_kind=None):
    operation_id = valid_id(operation_id or uuid.uuid4().hex)
    for stage in stages:
        if not stage.startswith('/data/local/tmp/rungic-'):
            raise ValueError('Invalid staging path.')
        valid_id(stage.removeprefix('/data/local/tmp/rungic-'))
    if package_kind not in (None, 'system', 'ordinary'):
        raise ValueError('Unknown removal package kind.')
    values = {'home': HOME, 'preserved': PRESERVED, 'compat': COMPAT,
              'pending': PENDING, 'uninstalled': UNINSTALLED,
              'controller': PATHS['RUNGIC_CONTROLLER'], 'lxc': PATHS['RUNGIC_LXC'],
              'operation': operation_id, 'package_kind': package_kind or 'unknown', 'purge': '1' if purge else '0',
              'stage_release': stages[0].removeprefix('/data/local/tmp/rungic-') if stages else '-'}
    header = '\n'.join(key + '=' + shlex.quote(value) for key, value in values.items())
    paths = ' '.join(shlex.quote(path) for path in (*PATHS.values(), *stages))
    # These checks never stop workers or write device files. Execution reuses them after stop.
    checks = r"""
set -eu
set -o pipefail
""" + ROOT_PROVIDER_SH + r"""
present() { [ -e "$1" ] || [ -L "$1" ]; }
check_paths() {
    for parent in /data/data/com.termux /data/data/com.termux/files /data/data/com.termux/files/usr /data/data/com.termux/files/usr/tmp /data/adb /data/adb/service.d /data/adb/modules /data/local/tmp "$preserved" "$compat" "$lxc" "$controller" "$pending"; do
        [ ! -L "$parent" ] || { echo "Protected path is a symlink: $parent" >&2; return 1; }
    done
}
check_processes() {
    for proc in /proc/[0-9]*; do
        [ -d "$proc" ] || continue
        if [ -r "$proc/cmdline" ]; then :
        else
            [ -d "$proc" ] || continue
            echo "Cannot read a process command: $proc" >&2; return 2
        fi
        if command=$(tr '\000' ' ' 2>/dev/null < "$proc/cmdline"); then :
        else
            [ -d "$proc" ] || continue
            echo "Cannot read a process command: $proc" >&2; return 2
        fi
        case "$command" in
            *'/data/adb/rungic-plasma/'*|*'/data/adb/rungic-lxc/'*|*'/data/adb/rungic-wfd/'*|*'com.rungic.cast.Main watch '*|*'com.rungic.plasma.MediaDaemon '*|*'com.rungic.plasma.DeviceDaemon '*|*'com.rungic.clipboard.ClipboardDaemon '*|*'com.rungic.telephony.CallDaemon '*|*'/data/data/com.termux/files/usr/tmp/rungic-plasma-audio/'*)
                echo "相关进程仍在运行：${proc##*/}。执行会先停止服务，再重新检查。" >&2
                printf '%s\n' "$command" | sed -E 's/((--)?(password|token|secret|api[-_]key|authorization)(=| +))[^ ]+/\1[REDACTED]/Ig' >&2
                return 1;;
        esac
    done
}
# Older casting installers have no removal entry. Undo their owned bind here;
# do not call an unsupported --remove on a pre-existing installer.
wfd_config=/vendor/etc/wfdconfig.xml
wfd_binding() {
    [ -r /proc/1/mountinfo ] || { echo 'Cannot inspect init mount namespace.' >&2; return 2; }
    if state=$(awk -v target="$wfd_config" '
        NF < 10 || $1 !~ /^[0-9]+$/ {invalid_table=1}
        $5 == target {
            count++
            if ($1 !~ /^[0-9]+$/ || NF < 10) invalid=1
            if ($4 == "/adb/rungic-wfd/wfdconfig.xml" || $4 == "/data/adb/rungic-wfd/wfdconfig.xml") owned++
        }
        END {
            if (NR == 0 || invalid_table) exit 2
            if (count == 0) print "absent"
            else if (count == 1 && owned == 1 && !invalid) print "rungic"
            else print "foreign"
        }
    ' /proc/1/mountinfo); then :
    else echo 'Cannot inspect init mount namespace.' >&2; return 2; fi
    case "$state" in
        absent|rungic) printf '%s\n' "$state";;
        *) echo '投屏配置挂载来源不属于 Rungic，或存在重叠挂载。不会撤销此挂载。没有删除任何内容。' >&2; return 1;;
    esac
}
check_wfd_config() {
    state=$(wfd_binding) || return $?
    if [ "$state" = rungic ]; then
        echo '执行时会撤销投屏配置的挂载（/vendor/etc/wfdconfig.xml）。只撤销来源是 Rungic 的那一条，不动厂商原有挂载。'
    else echo '没有需要撤销的 Rungic 投屏配置挂载。'; fi
}
remove_wfd_config() {
    state=$(wfd_binding) || return $?
    [ "$state" = rungic ] || return 0
    "$BB" nsenter -t 1 -m -- "$BB" umount "$wfd_config" || {
        echo 'Cannot unmount the Rungic casting configuration.' >&2; return 1;
    }
    state=$(wfd_binding) || return $?
    [ "$state" = absent ] || { echo 'Rungic casting bind remains in init namespace.' >&2; return 1; }
}
check_mounts() {
    for table in /proc/mounts /proc/self/mountinfo /proc/[0-9]*/mountinfo; do
        code=0
        grep -Eq '/adb/(rungic-|\.rungic-)' "$table" 2>/dev/null || code=$?
        case "$code" in
            0) echo '家目录或运行目录下面还有挂载点。没有删除任何内容。请先解除挂载并重试。--purge 会永久删除家目录，不能恢复。它也不能绕过挂载检查。' >&2
               echo "匹配挂载表：$table（最多列 20 行）" >&2
               awk '/\/adb\/(rungic-|\.rungic-)/ { print; if (++shown == 20) exit }' "$table" >&2
               return 1;;
            1) :;;
            *) case "$table" in
                   /proc/mounts|/proc/self/mountinfo) :;;
                   *) [ -d "${table%/mountinfo}" ] || continue;;
               esac
               echo "Cannot inspect a mount table: $table" >&2; return 2;;
        esac
    done
}
check_images() {
    for file in /sys/block/dm-*/dm/name; do
        [ -f "$file" ] || continue
        name=$(cat "$file") || { echo "Cannot read a mapping: $file" >&2; return 2; }
        case "$name" in rungic-root|rungic-before) echo "Image mapping remains: $name" >&2; return 1;; esac
    done
    for file in /sys/block/loop*/loop/backing_file; do
        [ -f "$file" ] || continue
        backing=$(cat "$file") || { echo "Cannot read an image loop: $file" >&2; return 2; }
        case "$backing" in *'/data/adb/rungic-'*|*'/data/adb/.rungic-'*) echo "Image loop remains: $backing" >&2; return 1;; esac
    done
}
mount_id() {
    awk -v path="$1" '
        $5 == "/" || path == $5 || index(path, $5 "/") == 1 {
            size=length($5)
            if (size > best) {best=size; id=$1; count=1}
            else if (size == best) {count++}
        }
        END {if (count != 1 || id !~ /^[0-9]+$/ || id + 0 < 1) exit 1; print id}
    ' /proc/self/mountinfo
}
check_home() {
    for part in "$lxc/runtime" "$lxc/runtime/var" "$lxc/runtime/var/lib" "$lxc/runtime/var/lib/lxc" "$lxc/runtime/var/lib/lxc/plasma" "$lxc/runtime/var/lib/lxc/plasma/state" "$home"; do
        [ ! -L "$part" ] || { echo "A home parent is a symlink: $part" >&2; return 1; }
    done
    if present "$home"; then
        [ -d "$home" ] || { echo 'The home path is not a directory.' >&2; return 1; }
        source_mount=$(mount_id "$home") || { echo 'Cannot resolve the home mount.' >&2; return 2; }
        if [ "$purge" = 0 ]; then
            parent=$preserved
            [ -d "$parent" ] || parent=/data/adb
            target_mount=$(mount_id "$parent") || { echo 'Cannot resolve the preservation mount.' >&2; return 2; }
            [ "$source_mount" = "$target_mount" ] || { echo '家目录不能原地保留：目标位置跨了挂载。没有删除任何内容。请先手动备份家目录。如果不需要保留家目录，可显式加 --purge。这会永久删除家目录，不能恢复。' >&2; return 1; }
            echo "home_mount=$source_mount preservation_mount=$target_mount"
        else
            echo "home_mount=$source_mount purge=1"
        fi
    else
        echo 'No current home exists.'
    fi
}
"""
    if preview:
        return header + '\n' + checks + r"""
for name in paths processes wfd_config mounts images home; do
    if detail=$("check_$name" 2>&1); then state=PASS
    else
        code=$?
        if [ "$code" = 1 ]; then state=BLOCKED; else state=UNKNOWN; fi
    fi
    detail=$(printf '%s' "$detail" | tr '\n\t' '  ')
    printf 'check\t%s\t%s\t%s\n' "$name" "$state" "$detail"
done
"""
    script = r"""
umask 077
fail() { echo "Rungic removal stopped: $*" >&2; exit 1; }
check_paths || fail 'Protected paths failed the check.'
record=$operation:$purge:$stage_release
[ "$package_kind" = unknown ] || record=$record:$package_kind
resuming=0
changed=0
home_before=0
present "$home" && home_before=1
target=$preserved/home-$operation/home
if present "$pending"; then
    resuming=1
    [ -f "$pending" ] || fail 'The removal record is not a file.'
    actual=$(cat "$pending") || fail 'Cannot read the removal record.'
    [ "$actual" = "$record" ] || [ "$actual" = "$operation:$purge:$stage_release" ] || fail 'Removal record differs from this request.'
fi
finish_root() {
    code=$?
    trap - EXIT
    home_after=0
    present "$home" && home_after=1
    if [ "$code" != 0 ] && [ "$changed" = 0 ] && [ "$resuming" = 0 ] && [ "$home_before" = "$home_after" ] && ! present "$target"; then
        rm -f "$pending"
        sync
        printf 'phase\tpreflight-marker-cleared\n'
    fi
    exit "$code"
}
trap finish_root EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
[ ! -L "$pending.tmp" ] || fail 'The pending record is a symlink.'
printf '%s\n' "$record" > "$pending.tmp"
sync
mv "$pending.tmp" "$pending"
sync
if [ -x "$controller/rungic-plasma" ]; then
    "$controller/rungic-plasma" stop
    # Older controllers have no runtime-status; independently inspect the resources below.
fi
# Only the casting watcher needs a signal. Other workers stop through the controller.
for proc in /proc/[0-9]*; do
    [ -d "$proc" ] || continue
    if [ -r "$proc/cmdline" ]; then :
    else
        [ -d "$proc" ] || continue
        fail "Cannot read a process command: $proc"
    fi
    if command=$(tr '\000' ' ' 2>/dev/null < "$proc/cmdline"); then :
    else
        [ -d "$proc" ] || continue
        fail "Cannot read a process command: $proc"
    fi
    case "$command" in
        *'/data/adb/rungic-wfd/rungic-cast-watch '*|*'com.rungic.cast.Main watch '*)
            kill -TERM "${proc##*/}" || { [ ! -d "$proc" ] || fail 'Cannot stop the casting watcher.'; };;
    esac
done
for attempt in 1 2 3 4 5; do
    if check_processes; then break; fi
    [ "$attempt" != 5 ] || fail 'Rungic processes remain or cannot be inspected.'
    sleep 1
done
remove_wfd_config || fail 'Casting configuration failed removal.'
check_mounts || fail 'Mounts failed the check.'
check_images || fail 'Image resources failed the check.'
check_home || fail 'The home failed the check.'
mkdir -p "$preserved"
chmod 700 "$preserved"
journal=$preserved/uninstall-$operation.log
[ ! -L "$journal" ] || fail 'The progress journal is a symlink.'
progress() { printf '%s\t%s\n' "$1" "$2" | tee -a "$journal"; sync; }
progress phase stopped
if [ "$purge" = 0 ]; then
    record=$preserved/home-$operation
    intent=$record/intent
    if present "$record"; then
        [ ! -L "$record" ] && [ -f "$intent" ] && [ ! -L "$intent" ] || fail 'Invalid preservation record.'
        length=$(wc -l < "$intent") || fail 'Cannot read the preservation record.'
        [ "$length" -eq 4 ] || fail 'Invalid preservation record length.'
        source=$(sed -n '1p' "$intent") || fail 'Cannot read the preservation record.'
        destination=$(sed -n '2p' "$intent") || fail 'Cannot read the preservation record.'
        inode=$(sed -n '3p' "$intent") || fail 'Cannot read the preservation record.'
        state=$(sed -n '4p' "$intent") || fail 'Cannot read the preservation record.'
        [ "$source" = "$home" ] && [ "$destination" = "$target" ] || fail 'Invalid preservation paths.'
        printf '%s\n' "$inode" | grep -Eq '^[0-9]+:[0-9]+$' || fail 'Invalid preservation inode.'
        case "$state" in intent|moved) :;; *) fail 'Invalid preservation phase.';; esac
        if present "$home"; then
            [ "$state" = intent ] && ! present "$target" || fail 'The preservation target collides with source data.'
            actual_inode=$(stat -c '%d:%i' "$home") || fail 'Cannot read the home inode.'
            [ "$actual_inode" = "$inode" ] || fail 'The home inode changed.'
        else
            [ -d "$target" ] && [ ! -L "$target" ] || fail 'The preserved home is missing.'
        fi
    elif [ -d "$home" ]; then
        inode=$(stat -c '%d:%i' "$home") || fail 'Cannot read the home inode.'
        mkdir "$record"
        printf '%s\n' "$home" "$target" "$inode" intent > "$intent"
        sync
    fi
    if [ -f "$intent" ]; then
        if present "$home"; then
            source_mount=$(mount_id "$home") || fail 'Cannot resolve the home mount.'
            target_mount=$(mount_id "$record") || fail 'Cannot resolve the preservation mount.'
            [ "$source_mount" = "$target_mount" ] || fail 'The preservation mount differs.'
            ! present "$target" || fail 'The preservation target already exists.'
            progress phase changing
            mv -T -- "$home" "$target" || fail 'Home rename failed.'
            changed=1
        fi
        actual_inode=$(stat -c '%d:%i' "$target") || fail 'Cannot read the preserved home inode.'
        [ "$actual_inode" = "$inode" ] || fail 'Rename did not preserve the home inode.'
        sync
        [ ! -L "$intent.tmp" ] || fail 'The intent temporary file is a symlink.'
        printf '%s\n' "$home" "$target" "$inode" moved > "$intent.tmp"
        sync
        mv "$intent.tmp" "$intent"
        sync
        progress preserved "$target"
        progress moved "$home"
    fi
fi
if [ -f /product/etc/rungic/firstboot.sh ]; then
    for part in "$compat/system" "$compat/system/product" "$compat/system/product/etc" "$compat/system/product/etc/rungic"; do
        [ ! -L "$part" ] || fail 'The compatibility module contains a symlink.'
    done
    [ ! -L "$compat/module.prop" ] || fail 'The module descriptor is a symlink.'
    [ ! -L "$compat/system/product/etc/rungic/firstboot.sh" ] || fail 'The seed guard is a symlink.'
    changed=1
    progress phase changing
    mkdir -p "$compat/system/product/etc/rungic"
    printf '%s\n' 'id=rungic-install-compat' 'name=Rungic removed' 'version=1' 'versionCode=1' 'author=Rungic' 'description=Prevent the old product seed from installing Rungic.' > "$compat/module.prop"
    printf '#!/system/bin/sh\nexit 0\n' > "$compat/system/product/etc/rungic/firstboot.sh"
    chmod 755 "$compat" "$compat/system" "$compat/system/product" "$compat/system/product/etc" "$compat/system/product/etc/rungic" "$compat/system/product/etc/rungic/firstboot.sh"
    chmod 644 "$compat/module.prop"
    chcon -R u:object_r:system_file:s0 "$compat/system"
    test ! -e "$compat/disable"
    test ! -e "$compat/remove"
fi
[ ! -L "$uninstalled" ] || fail 'The seed marker is a symlink.'
changed=1
progress phase changing
seed_record=$operation
[ "$package_kind" = unknown ] || seed_record=$seed_record:$package_kind
printf '%s\n' "$seed_record" > "$uninstalled"
sync
progress phase seed-blocked
"""
    script += '\nfor path in ' + paths + r"""; do
    if present "$path"; then
        progress removing "$path"
        rm -rf -- "$path"
        ! present "$path" || fail "Removal left a path: $path"
        progress deleted "$path"
    else
        progress absent "$path"
    fi
done
progress phase runtime-removed
"""
    return header + '\n' + checks + script


def installed_package_info(info):
    # The hidden system package is not the installed package. Do not inspect its flags.
    active = info.split('Hidden system packages:', 1)[0]
    block = re.search(r'^\s*Package \[' + re.escape(APP) + r'\][^\n]*:\n(.*?)(?=^\s*Package \[|\Z)', active, re.M | re.S)
    body = block.group(1) if block else ''
    match = re.search(r'\b(?:pkgFlags|flags)=\[([^\]]*)\]', body)
    version = re.search(r'\bversionCode=(\d+)', body)
    if not match or not version:
        raise ValueError('当前包属性读回未知。')
    return {'flags': match.group(1).split(), 'versionCode': int(version.group(1))}


def package_observation_text(name, observed):
    if name == 'clear-app-data':
        return '；'.join('用户 0 的' + label + ('已清空' if observed[kind] == 'EMPTY' else '里仍有文件或链接')
                        for kind, label in (('CE', '加密存储'), ('DE', '设备存储'))) + '。'
    if name == 'remove-apk-update':
        remaining = []
        if any(not path.startswith('package:/product/') for path in observed['paths']):
            remaining.append('当前 APK 尚未回到底座的 product 目录')
        if 'SYSTEM' not in observed['flags']:
            remaining.append('当前包尚未确认是底座系统应用')
        if 'UPDATED_SYSTEM_APP' in observed['flags']:
            remaining.append('当前包仍标记为系统应用更新')
        return '；'.join(remaining) + '。'
    return '用户 0 仍安装着 ' + APP + '。'


def package_readback(device, name, evidence=None):
    """Observe package state independently of the mutating pm command's response."""
    def observe(command, **kwargs):
        entry = {'command': command, 'root': kwargs.get('root', False), 'output': '', 'returncode': None}
        try:
            entry['output'] = device.shell(command, **kwargs)
            entry['returncode'] = 0
            return entry['output']
        except subprocess.SubprocessError as error:
            raw = getattr(error, 'output', '') or ''
            entry['output'] = raw.decode(errors='replace') if isinstance(raw, bytes) else raw
            entry['returncode'] = getattr(error, 'returncode', None)
            raise
        finally:
            if evidence is not None:
                evidence.append(entry)

    if name == 'clear-app-data':
        # Android may recreate empty cache directories. No files or symlinks may remain.
        # Check CE and DE storage; never follow an application-data symlink.
        output = observe(f'''# RUNGIC_APP_DATA_READBACK
test "$(id -u)" = 0
{ROOT_PROVIDER_SH}
for pair in CE:/data/user/0 DE:/data/user_de/0; do
    kind=${{pair%%:*}}; parent=${{pair#*:}}; path=$parent/{APP}
    test -d "$parent" && test ! -L "$parent" && test -r "$parent" && test -x "$parent" || exit 1
    test ! -L "$path" || exit 1
    if [ -e "$path" ]; then
        test -d "$path" && test -r "$path" && test -x "$path" || exit 1
        entries=$("$BB" find "$path" -mindepth 1 ! -type d -print) || exit 1
        if [ -n "$entries" ]; then state=NOT_EMPTY; else state=EMPTY; fi
    else state=EMPTY; fi
    printf '%s\\t%s\\n' "$kind" "$state"
done''', root=True)
        rows = output.splitlines()
        if len(rows) != 2 or any(row not in (kind + '\tEMPTY', kind + '\tNOT_EMPTY')
                                 for row, kind in zip(rows, ('CE', 'DE'))):
            raise ValueError('应用数据读回格式未知。')
        observed = {'CE': rows[0].split('\t')[1], 'DE': rows[1].split('\t')[1]}
        verified = all(row.endswith('\tEMPTY') for row in rows)
        # Additional diagnostic only: it cannot change the state decision above.
        if not verified:
            observed['remaining_entries'] = {}
            for kind, parent in (('CE', '/data/user/0'), ('DE', '/data/user_de/0')):
                if observed[kind] != 'NOT_EMPTY':
                    continue
                try:
                    names = observe(f'''# RUNGIC_APP_DATA_ENTRIES
{ROOT_PROVIDER_SH}
path={parent}/{APP}
test -d {parent} && test ! -L {parent} && test -d "$path" && test ! -L "$path" || exit 1
"$BB" find "$path" -mindepth 1 ! -type d -print0 | "$BB" sh -c '
count=0
while IFS= read -r -d "" item; do
    printf "%s\\000" "$item"
    count=$((count + 1))
    [ "$count" -lt 20 ] || break
done' ''', root=True)
                    observed['remaining_entries'][kind] = [item for item in names.split('\0') if item][:20]
                except subprocess.SubprocessError as error:
                    observed.setdefault('entries_error', {})[kind] = str(error)
        return observed, verified
    if name == 'verify-system-base':
        # After uninstall --user 0, pm path --user 0 returns 1 even when the
        # global system package remains. Read its active package record instead.
        source = removal_apk_versions(observe(f'dumpsys package {APP}'))['active']
        if not source or not all(key in source for key in ('path', 'flags', 'version_code')):
            raise ValueError('最终系统底座的路径或包属性读回未知。')
        observed = {'code_path': source['path'], 'flags': source['flags'].split(),
                    'versionCode': int(source['version_code'])}
        flags = observed['flags']
        return observed, source['path'].startswith('/product/') and 'SYSTEM' in flags and 'UPDATED_SYSTEM_APP' not in flags
    if name == 'remove-apk-update':
        paths = observe(f'pm path --user 0 {APP}').splitlines()
        info = observe(f'dumpsys package {APP}')
        observed = installed_package_info(info)
        if not paths or any(not row.startswith('package:/') for row in paths):
            raise ValueError('更新包路径或当前包属性读回未知。')
        observed['paths'] = paths
        flags = observed['flags']
        return observed, all(row.startswith('package:/product/') for row in paths) and 'SYSTEM' in flags and 'UPDATED_SYSTEM_APP' not in flags
    output = observe(f'pm list packages --user 0 {APP}')
    rows = output.splitlines()
    if any(not re.fullmatch(r'package:[A-Za-z0-9_.]+', row) for row in rows):
        raise ValueError('用户 0 包列表读回未知。')
    return {'user_packages': rows}, f'package:{APP}' not in rows


PACKAGE_PERSIST_TIMEOUT = 30
PACKAGE_PERSIST_SCRIPT = r'''# RUNGIC_PACKAGE_PERSISTENCE
# AOSP Settings/ResilientAtomicFile prefer backup over main at boot.
''' + ROOT_PROVIDER_SH + r'''
parent=/data/system/users/0
file=$parent/package-restrictions.xml
backup=$parent/package-restrictions-backup.xml
test "$(id -u)" = 0 || exit 1
test -d "$parent" && test ! -L "$parent" && test -r "$parent" && test -x "$parent" || exit 1
if [ -e "$backup" ] || [ -L "$backup" ]; then printf 'PENDING\tbackup\n'; exit 0; fi
test -f "$file" && test ! -L "$file" && test -r "$file" || exit 1
first=$("$BB" sha256sum "$file") || exit 1
first=${first%% *}
magic=$("$BB" od -An -tx1 -N4 "$file" | "$BB" tr -d ' \n') || exit 1
# The XML goes through a file, never an argument: an ordinary app's packages.xml is too long for
# printf (X70, 2026-10-08: "Argument list too long", the removal unconfirmed).
xml=/data/local/tmp/rungic-package-persistence.$$.xml
trap '"$BB" rm -f "$xml"' EXIT
if [ "$magic" = 41425800 ]; then
    /system/bin/abx2xml "$file" "$xml" || exit 1
else
    "$BB" cat "$file" > "$xml" || exit 1
fi
# Flush what was observed, then reject a concurrent/unfinished PM write.
sync || exit 1
last=$("$BB" sha256sum "$file") || exit 1
last=${last%% *}
if [ "$first" != "$last" ] || [ -e "$backup" ] || [ -L "$backup" ]; then
    printf 'PENDING\tchanged\n'; exit 0
fi
printf 'STABLE\t%s\n' "$last"
"$BB" cat "$xml" || exit 1
'''


def package_persistence_script(kind):
    if kind == 'system':
        return PACKAGE_PERSIST_SCRIPT
    if kind == 'ordinary':
        return PACKAGE_PERSIST_SCRIPT.replace('parent=/data/system/users/0', 'parent=/data/system').replace(
            'file=$parent/package-restrictions.xml', 'file=$parent/packages.xml').replace(
            'backup=$parent/package-restrictions-backup.xml', 'backup=$parent/packages-backup.xml')
    raise ValueError('无法确认卸载结果已保存：包类型未知。')


def wait_user_package_persistence(device, observations, save=lambda: None, kind='system'):
    """Wait for user-0 restrictions on disk, including on a resumed removal.
    Missing XML, conversion failures and unknown reads never mean uninstalled.
    """
    command = package_persistence_script(kind)
    started = time.monotonic()
    while True:
        entry = {'command': command, 'root': True,
                 'output': '', 'returncode': None}
        observations.append(entry)
        try:
            remaining = PACKAGE_PERSIST_TIMEOUT - (time.monotonic() - started)
            entry['output'] = device.shell(command, root=True,
                                          timeout=max(1, min(10, remaining)))
            entry['returncode'] = 0
            header, _, xml = entry['output'].partition('\n')
            observed = {'state': 'pending', 'attempt': len(observations)}
            if re.fullmatch(r'STABLE\t[0-9a-f]{64}', header):
                try:
                    document = ET.fromstring(xml)
                except ET.ParseError as error:
                    raise ValueError('无法确认卸载结果已保存：包持久状态 XML 无法解析。') from error
                root, tag = ('package-restrictions', 'pkg') if kind == 'system' else ('packages', 'package')
                if document.tag != root:
                    raise ValueError('无法确认卸载结果已保存：包持久状态根节点未知。')
                packages = [node for node in document.findall(tag) if node.get('name') == APP]
                if len(packages) > 1:
                    raise ValueError('无法确认卸载结果已保存：包持久状态存在重复条目。')
                if kind == 'system':
                    if not packages:
                        raise ValueError('无法确认卸载结果已保存：用户 0 包持久状态缺少目标条目。')
                    installed = packages[0].get('inst', 'true')
                    if installed not in ('true', 'false'):
                        raise ValueError('无法确认卸载结果已保存：用户 0 包持久状态 inst 值未知。')
                    verified = installed == 'false'
                    observed['inst'] = installed
                else:
                    verified = not packages
                    observed['registered'] = bool(packages)
                observed.update(kind=kind, state='uninstalled' if verified else 'installed',
                                xml_sha256=header.split('\t')[1])
                entry['readback'] = observed
                if verified:
                    observed['elapsed_seconds'] = round(time.monotonic() - started, 3)
                    return observed
            elif header not in ('PENDING\tbackup', 'PENDING\tchanged') or xml:
                raise ValueError('无法确认卸载结果已保存：包持久状态读回格式未知。')
            entry['readback'] = observed
        except subprocess.SubprocessError as error:
            raw = getattr(error, 'output', '') or ''
            entry['output'] = raw.decode(errors='replace') if isinstance(raw, bytes) else raw
            entry['returncode'] = getattr(error, 'returncode', None)
            raise ValueError('无法确认卸载结果已保存：包持久状态读回失败。') from error
        finally:
            save()
        if time.monotonic() - started >= PACKAGE_PERSIST_TIMEOUT:
            raise ValueError('无法确认卸载结果已保存：等待超时（最多 30 秒），手机还未确认保存卸载结果。卸载未完成。')
        time.sleep(min(1, max(0, PACKAGE_PERSIST_TIMEOUT - (time.monotonic() - started))))


def uninstall(args):
    if args.report:
        args.report.mkdir(parents=True, exist_ok=False)
        write_removal_report(args.report / 'report.json', {'schema': 1, 'kind': 'rungic-uninstall',
              'complete': False, 'phase': 'identity', 'serial': args.serial})
    try:
        return _uninstall(args)
    except BaseException as error:
        if args.report:
            report = read(args.report / 'report.json')
            report['complete'] = False
            report['error'] = str(error)
            write_removal_report(args.report / 'report.json', report)
        raise


def _uninstall(args):
    """Preview by default. Remove only explicitly selected Rungic installation data."""
    if not args.serial or not 1 <= args.adb_port <= 65535:
        raise ValueError('Supply an explicit hardware serial and a valid ADB port.')
    if args.yes_delete and not args.report:
        raise ValueError('--report is required with --yes-delete.')
    device = Device(args)
    identity = device.shell('getprop ro.serialno')
    if identity != args.serial:
        raise ValueError('Device serial differs from the requested hardware serial.')
    users = device.shell('pm list users')
    if re.findall(r'UserInfo\{(\d+):', users) != ['0']:
        raise ValueError('Removal supports Android user 0 only.')
    if device.shell('id -u', root=True) != '0':
        raise ValueError('Root access is not available.')
    before = uninstall_state(device)
    # Report unselected stage directories. Never remove them with a glob.
    release = before.get('release', '')
    stage = ()
    if release:
        if not re.fullmatch(r'RELEASE_ID=([a-zA-Z0-9][a-zA-Z0-9_.-]{0,63})', release):
            raise ValueError('The installed release descriptor is invalid.')
        stage = (staging_path(valid_id(release.split('=', 1)[1])),)
    operation_id = uuid.uuid4().hex
    remembered_kind = None
    if before.get('pending'):
        parts = before['pending'].split(':')
        if len(parts) not in (3, 4) or parts[1] != str(int(args.purge)) or (len(parts) == 4 and parts[3] not in ('system', 'ordinary')):
            raise ValueError('Resume the previous removal with the same --purge choice.')
        remembered_kind = parts[3] if len(parts) == 4 else None
        operation_id = valid_id(parts[0])
        stage = () if parts[2] == '-' else (staging_path(valid_id(parts[2])),)
    if remembered_kind is None and not before.get('user_packages'):
        seed = before.get('uninstalled', '').split(':')
        if len(seed) == 2 and seed[1] in ('system', 'ordinary'):
            valid_id(seed[0])
            remembered_kind = seed[1]
    targets = list(PATHS.values()) + list(stage)
    result = {'schema': 1, 'kind': 'rungic-uninstall', 'serial': identity,
              'adb_port': args.adb_port, 'operation_id': operation_id, 'purge': args.purge,
              'started_at': datetime.now(timezone.utc).isoformat(),
              'plan_only': not args.yes_delete, 'complete': False, 'before': before,
              'delete_plan': targets, 'expected_retained': dict(RETAINED),
              'deleted': [], 'attempted': [], 'moved': [], 'already_absent': [], 'failed': [], 'not_touched': targets.copy(), 'steps': [],
              'limitations': ['The read-only product APK and Android base remain.',
                              'After reboot, verify the legacy seed guard.',
                              'Installation does not restore historical preserved homes.'],
              'preserved_home_status': 'Not observed yet.',
              'restore': 'Stop Linux. Match the old and new account name, home path and numeric UID/GID. Keep the new home as a rollback copy. Move the preserved home to the original state/home path. Verify data, permissions and labels before starting Linux. Authorize and verify restoration separately.'}
    if args.purge:
        result['warning'] = '--purge 永久删除当前 Linux 家目录，不能恢复。以前保留的家目录不在删除范围内。'
    if not args.yes_delete:
        if args.report:
            write_removal_report(args.report / 'report.json', result)
        raw = device.shell(uninstall_root_script(args.purge, operation_id, stage, preview=True), root=True)
        checks = []
        for line in raw.splitlines():
            fields = line.split('\t', 3)
            if len(fields) != 4 or fields[0] != 'check' or fields[2] not in ('PASS', 'BLOCKED', 'UNKNOWN'):
                raise ValueError('The removal preview response is invalid.')
            checks.append({'name': fields[1], 'state': fields[2], 'reason': fields[3]})
        if [entry['name'] for entry in checks] != ['paths', 'processes', 'wfd_config', 'mounts', 'images', 'home']:
            raise ValueError('The removal preview response is incomplete.')
        result['preflight'] = checks
        result['would_stop_at'] = next((entry['name'] for entry in checks if entry['state'] != 'PASS'), None)
        result['preflight_allowed'] = result['would_stop_at'] is None
        result['preflight_note'] = '当前只读检查结果。执行会先停止服务并重新检查。预览不能证明停止会成功，也不申请协调锁。'
        if args.report:
            (args.report / 'uninstall-root.sh').write_text(uninstall_root_script(args.purge, operation_id, stage, preview=True))
            write_removal_report(args.report / 'report.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    report_file = args.report / 'report.json'
    script = uninstall_root_script(args.purge, operation_id, stage)
    (args.report / 'uninstall-root.sh').write_text(script)
    write_removal_report(report_file, result)

    def step(name, operation):
        result['phase'] = name
        write_removal_report(report_file, result)
        try:
            output = operation()
        except subprocess.SubprocessError as error:
            raw = getattr(error, 'output', '') or ''
            if isinstance(raw, bytes):
                raw = raw.decode(errors='replace')
            result['steps'].append({'name': name, 'output': raw, 'error': str(error)})
            raise
        result['steps'].append({'name': name, 'output': output})
        write_removal_report(report_file, result)
        return output

    try:
        with device.maintenance(removal=True):
            if before.get('user_packages') or remembered_kind is None:
                try:
                    info = installed_package_info(step('inspect-package-type', lambda: device.shell(f'dumpsys package {APP}')))
                except (subprocess.SubprocessError, ValueError) as error:
                    raise ValueError('无法确认卸载包类型：' + str(error)) from error
                paths = step('inspect-package-paths', lambda: device.shell(f'pm path {APP}')).splitlines()
                if not paths or any(not row.startswith('package:/') for row in paths):
                    raise ValueError('无法确认卸载包类型：包路径读回未知。')
                if 'SYSTEM' in info['flags'] or any(row.startswith(('package:/product/', 'package:/system/')) for row in paths):
                    kind = 'system'
                elif all(row.startswith('package:/data/app/') for row in paths):
                    kind = 'ordinary'
                else:
                    raise ValueError('无法确认卸载包类型：包标志或路径不符合支持范围。')
                if remembered_kind is not None and kind != remembered_kind:
                    raise ValueError('卸载包类型与上次记录不同，请保持现场并检查。')
                result['package_kind_evidence'] = {'flags': info['flags'], 'paths': paths}
            else:
                kind = remembered_kind
                result['package_kind_evidence'] = {'source': 'existing-removal-record'}
            result['package_kind'] = kind
            script = uninstall_root_script(args.purge, operation_id, stage, package_kind=kind)
            (args.report / 'uninstall-root.sh').write_text(script)
            write_removal_report(report_file, result)
            if before.get('user_packages'):
                step('stop-app', lambda: device.shell(f'am force-stop --user 0 {APP}'))
            step('remove-runtime', lambda: device.shell(script, root=True, timeout=1800))
            if before.get('user_packages'):
                def package(name, label, command):
                    result['phase'] = name
                    entry = {'name': name, 'label': label, 'command': command,
                             'output': '', 'returncode': None, 'verified': False, 'readback': None,
                             'observations': []}
                    result['steps'].append(entry)
                    write_removal_report(report_file, result)
                    try:
                        entry['output'] = device.shell(command, timeout=180)
                        entry['returncode'] = 0
                    except subprocess.SubprocessError as error:
                        raw = getattr(error, 'output', '') or ''
                        entry['output'] = raw.decode(errors='replace') if isinstance(raw, bytes) else raw
                        entry['returncode'] = getattr(error, 'returncode', None)
                        entry['command_error'] = str(error)
                    # Save the command response even if the following observation fails.
                    write_removal_report(report_file, result)
                    try:
                        entry['readback'], entry['verified'] = package_readback(device, name, entry['observations'])
                    except (subprocess.SubprocessError, ValueError) as error:
                        entry['readback_error'] = str(error)
                        raise ValueError(label + '：读回未知，未确认完成。' + str(error)) from error
                    finally:
                        write_removal_report(report_file, result)
                    if not entry['verified']:
                        raise ValueError(label + '：读回状态未达到要求。' + package_observation_text(name, entry['readback']))
                package('clear-app-data', '清空应用数据', f'pm clear --user 0 {APP}')
                try:
                    info = installed_package_info(step('inspect-apk-update', lambda: device.shell(f'dumpsys package {APP}')))
                except (subprocess.SubprocessError, ValueError) as error:
                    raise ValueError('检查 APK 更新：读回未知，未确认是否存在更新包。' + str(error)) from error
                if 'UPDATED_SYSTEM_APP' in info['flags']:
                    package('remove-apk-update', '移除 APK 更新', f'pm uninstall-system-updates {APP}')
                package('remove-user-app', '卸载用户 0 的应用', f'pm uninstall --user 0 {APP}')
            after = uninstall_state(device)
            result['after'] = after
            for path in targets:
                if after['paths'].get(path, path in after.get('stages', [])):
                    result['failed'].append({'path': path, 'reason': 'The path remains.'})
            if after.get('user_packages') or after['paths'].get(f'/data/user/0/{APP}'):
                result['failed'].append({'path': APP, 'reason': 'The user package remains.'})
            if before['termux_path'] != after['termux_path']:
                result['failed'].append({'path': 'com.termux', 'reason': 'The package path changed.'})
            if result['failed']:
                raise ValueError('Removal is incomplete. Read the report.')
            result['phase'] = 'persist-user-app'
            persistence = {'name': 'persist-user-app', 'label': '确认用户 0 卸载状态已持久保存',
                           'verified': False, 'output': '', 'observations': []}
            result['steps'].append(persistence)
            write_removal_report(report_file, result)
            print('正在等待手机保存卸载结果（最多 30 秒）。请先不要重启手机。', flush=True)
            try:
                persistence['readback'] = wait_user_package_persistence(
                    device, persistence['observations'], lambda: write_removal_report(report_file, result), kind=kind)
                persistence['verified'] = True
            finally:
                write_removal_report(report_file, result)
            if before.get('user_packages'):
                result['deleted'].append(APP_DATA)
            step('finish', lambda: device.shell(f'rm -f {PENDING}; sync', root=True))
            marker = step('finish-readback', lambda: device.shell(
                f'if [ -e {PENDING} ] || [ -L {PENDING} ]; then echo PRESENT; else echo ABSENT; fi', root=True))
            if marker.strip() != 'ABSENT':
                raise ValueError('卸载标记仍在，或无法读回卸载标记的状态。')
            result['marker_cleared'] = True
            result['before_finish'] = result.pop('after')
            result['phase'] = 'finish-snapshot'
            preserved = [line.split('\t', 1)[1] for entry in result['steps']
                         for line in entry.get('output', '').splitlines() if line.startswith('preserved\t')]
            retained = [path for path in RETAINED if path != PENDING and before.get('paths', {}).get(path) is True]
            # The guard module exists only where a product seed could reinstall the old release: the
            # removal script writes it only then (X70, 2026-10-08: no seed, removal never "complete").
            seed = device.shell('if [ -f /product/etc/rungic/firstboot.sh ]; then echo seed; fi', root=True).strip() == 'seed'
            retained = list(dict.fromkeys(retained + ([COMPAT] if seed else []) + [UNINSTALLED] + preserved))
            result['final_retained_checks'] = retained
            result['after'] = uninstall_state(device, extra_paths=targets + retained)
            result['after_phase'] = 'after-marker-clear'
            validation = {'kind': kind, 'observations': []}
            result['after']['package_validation'] = validation
            # These are independent final observations, not earlier pm command success.
            state, validation['user_absent'] = package_readback(device, 'remove-user-app', validation['observations'])
            validation['user_state'] = state
            state, validation['data_empty'] = package_readback(device, 'clear-app-data', validation['observations'])
            validation['data_state'] = state
            if kind == 'system':
                state, validation['system_base'] = package_readback(device, 'verify-system-base', validation['observations'])
                validation['system_state'] = state
            validation['persisted'] = False
            if validation['user_absent'] and validation['data_empty'] and (kind != 'system' or validation['system_base']):
                validation['persistence'] = wait_user_package_persistence(device, validation['observations'], kind=kind)
                validation['persisted'] = validation['persistence'].get('kind') == kind and validation['persistence'].get('state') == 'uninstalled'
            problems = final_removal_problems(before, result['after'], targets, retained, kind)
            result['failed'].extend(problems)
            if problems:
                raise ValueError('最终卸载现场未满足完成条件：\n\n' + '\n'.join('- ' + item['path'] + '：' + item['reason'] for item in problems))
            result['complete'] = True
            result['phase'] = 'complete'
    except BaseException as error:
        result['complete'] = False
        result['error'] = str(error)
        result['failed'].append({'phase': result.get('phase', 'lock'), 'reason': str(error)})
        if result.get('after_phase') == 'after-marker-clear':
            result['failed_final_snapshot'] = result.pop('after')
        try:
            result['after'] = uninstall_state(device, extra_paths=targets + result.get('final_retained_checks', []))
            result['after_phase'] = 'failure-readback'
        except Exception as readback_error:
            result.pop('after', None)
            result['readback_error'] = str(readback_error)
        raise
    finally:
        for entry in result['steps']:
            for line in entry.get('output', '').splitlines():
                kind, _, value = line.partition('\t')
                if kind == 'removing' and value not in result['attempted']:
                    result['attempted'].append(value)
                if kind == 'deleted' and value not in result['deleted']:
                    result['deleted'].append(value)
                if kind == 'moved' and value not in result['moved']:
                    result['moved'].append(value)
                if kind == 'absent' and value not in result['already_absent']:
                    result['already_absent'].append(value)
                if kind == 'preserved':
                    result['preserved_home'] = value
                    result['preserved_home_status'] = 'Verified rename, with the original home inode.'
                    result['expected_retained'][value] = '移动后已核对原家目录 inode，保留用户资料。'
        if result.get('after', {}).get('paths', {}).get(PENDING) is False:
            result['expected_retained'].pop(PENDING, None)
        if result['complete'] and 'preserved_home' not in result:
            result['preserved_home_status'] = 'Removed by explicit --purge request.' if args.purge else 'No current home exists. This operation did not verify historical homes.'
        observed = result.get('after', before)
        result['outside_scope'] = [{'path': path, 'reason': 'Outside the selected deletion inventory. Retained without verification.'}
                                   for path in observed.get('other_paths', []) + observed.get('stages', [])
                                   if path not in targets and path not in RETAINED and path != PENDING]
        result['not_touched'] = [path for path in targets if path not in result['attempted'] and path not in result['already_absent']]
        write_removal_report(report_file, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('pack')
    for name in ('spec', 'rootfs-gz', 'rootfs-report', 'host-seed', 'host-report', 'kernel-report', 'package-lock', 'package-release', 'apk', 'deps', 'output'):
        b.add_argument('--' + name, type=Path, required=True)
    b.add_argument('--release-id', required=True)
    b.add_argument('--build-plan', type=Path, required=True, help='expected component recipes, cache directories and primary artifact bindings')
    v = sub.add_parser('verify'); v.add_argument('payload', type=Path)
    for action in ('install', 'status', 'uninstall'):
        d = sub.add_parser(action)
        d.add_argument('--adb', default='adb')
        d.add_argument('--adb-port', type=int, required=True)
        d.add_argument('--serial', required=True)
        if action == 'uninstall':
            d.add_argument('--yes-delete', action='store_true', help='Authorize removal. Keep the Linux home unless you select --purge.')
            d.add_argument('--purge', action='store_true', help='Permanently remove the current Linux home. Keep historical copies.')
            d.add_argument('--report', type=Path, help='new host directory for the script and report')
        if action == 'install':
            d.add_argument('payload', type=Path)
            d.add_argument('--manifest-sha256', required=True)
    args = p.parse_args()
    if args.command == 'pack': pack(args)
    elif args.command == 'verify':
        m = verify(args.payload); print(json.dumps({'verified': True, 'release': m['release'], 'manifest_sha256': digest(args.payload / 'manifest.json')}))
    elif args.command == 'install': install(args)
    elif args.command == 'uninstall': uninstall(args)
    else:
        print(Device(args).shell(f'cat /data/user/0/{APP}/files/rungic-install.properties; tail -n 12 /data/adb/rungic-firstboot.log', root=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(f'standalone installation: {error}')
