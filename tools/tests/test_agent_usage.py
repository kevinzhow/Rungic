"""The Codex usage provider (docs/research/95): read-only, provider-shaped, no fabricated API-key quotas."""
import ast
import json
from pathlib import Path
import types
import unittest
from unittest.mock import Mock
root = Path(__file__).resolve().parents[2]
source = root / 'agent/assistant/rungic_voice_agent.py'
tree = ast.parse(source.read_text())
node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'VoiceAgent')
node.body = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in {'codex_account', 'usage', 'usage_limits', 'on_notification'}]
namespace = {'_': lambda text: text, 'json': json, 'threading': types.SimpleNamespace(Thread=Mock()), 'log': Mock()}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
# Keys every provider object may carry (the rest is dropped by the service anyway).
PROVIDER_KEYS = {'accountKey', 'status', 'account', 'model', 'tokens', 'limits', 'error', 'tokenEvents'}


class UsageBridgeTests(unittest.TestCase):
    def setUp(self):
        self.agent = namespace['VoiceAgent']()
        self.agent.agent_busy = False
        self.agent.usage_identity = Mock(return_value='opaque-a')
        self.agent.usage_tokens = {}
        self.agent.usage_accounts = {}
        self.agent.server = Mock()
        self.agent.thread_id = 'active'
        self.agent.emit_raw = Mock()
        self.agent.usage_push = Mock()
        # The agent's model (docs/98): what model_catalog resolved the user's choice to.
        self.agent.agent_model = Mock(return_value={'model': 'test-model', 'name': 'test-model', 'effort': 'low'})
        self.agent.catalog = Mock()
        self.agent.phone = None     # no phone session (docs/101)

    def pushed(self, method):
        return [c.args[1:] for c in self.agent.usage_push.call_args_list if c.args[0] == method]

    # covers: agent.usage-widget/E1
    def test_api_key_never_requests_subscription_quota(self):
        self.agent.server.call.return_value = {'account': {'type': 'apiKey'}}
        data = self.agent.usage()
        self.assertNotIn('limits', data)
        self.assertEqual(self.agent.server.call.call_count, 1)
        self.assertEqual(data['account'], {'kind': 'api-key', 'label': 'API Key', 'plan': ''})
        self.assertEqual(data['status'], 'ready')
        self.assertLessEqual(set(data), PROVIDER_KEYS)

    # covers: agent.usage-widget/E1
    def test_subscription_returns_real_nullable_fields(self):
        self.agent.server.call.side_effect = [
            {'account': {'type': 'chatgpt', 'planType': 'plus', 'email': 'do-not-publish'}},
            {'rateLimits': {'primary': {'usedPercent': 23, 'resetsAt': None, 'windowDurationMins': 300}}},
            {'summary': {'lifetimeTokens': None}}]
        data = self.agent.usage()
        self.assertEqual(data['account'], {'kind': 'subscription', 'label': 'ChatGPT', 'plan': 'plus'})
        self.assertEqual(data['limits'], [{'id': 'codex.primary', 'label': 'codex', 'windowMinutes': 300,
                                           'usedPercent': 23, 'resetsAt': None}])
        self.assertIsNone(data['tokens']['account'])
        self.assertNotIn('do-not-publish', json.dumps(data))
        self.assertLessEqual(set(data), PROVIDER_KEYS)

    # covers: agent.usage-widget/E1
    def test_limit_buckets_become_named_limits(self):
        read = {'rateLimitsByLimitId': {'codex': {'limitName': 'Codex', 'primary': {'usedPercent': 3.0, 'windowDurationMins': 300, 'resetsAt': 200},
                                                  'secondary': {'usedPercent': 40, 'windowDurationMins': 10080, 'resetsAt': 900}},
                                        'other': {'primary': None, 'secondary': {'usedPercent': 'n/a'}}}}
        limits = namespace['VoiceAgent'].usage_limits(read)
        self.assertEqual([l['id'] for l in limits], ['codex.primary', 'codex.secondary'])
        self.assertEqual(limits[1]['label'], 'Codex')
        self.assertEqual(limits[1]['windowMinutes'], 10080)

    # covers: agent.usage-widget/E5
    def test_failed_quota_read_is_an_error_without_invented_limits(self):
        self.agent.server.call.side_effect = [{'account': {'type': 'chatgpt'}}, RuntimeError('timeout')]
        data = self.agent.usage()
        self.assertNotIn('limits', data)
        self.assertEqual(data['error'], 'Account usage has not updated yet')

    # covers: agent.usage-widget/E5
    def test_signed_out_and_offline_states(self):
        self.agent.server.call.return_value = {'account': None}
        self.assertEqual(self.agent.usage()['status'], 'signed-out')
        self.agent.server = None
        self.assertEqual(self.agent.usage()['status'], 'offline')
        self.agent.agent_busy = True
        self.agent.server = Mock()
        self.agent.server.call.return_value = {'account': {'type': 'apiKey'}}
        self.assertEqual(self.agent.usage()['status'], 'working')

    # covers: agent.usage-widget/E5
    def test_account_read_failures_and_malformed_replies_are_not_signed_out(self):
        for reply in ({}, {'account': {}}, {'account': {'type': 'future'}}, ['bad']):
            with self.subTest(reply=reply):
                self.agent.server.call.return_value = reply
                data = self.agent.usage()
                self.assertEqual(data['status'], 'offline')
                self.assertNotIn('limits', data)
                self.assertNotIn('tokenEvents', data)
        self.agent.server.call.side_effect = RuntimeError('private diagnostic')
        self.assertEqual(self.agent.usage()['status'], 'offline')
        self.agent.server.call.side_effect = None
        self.agent.server.call.return_value = {'account': None}
        self.assertEqual(self.agent.usage()['status'], 'signed-out')
        self.agent.server.call.return_value = {'account': {'type': 'apiKey'}}
        self.assertEqual(self.agent.usage()['status'], 'ready')

    # covers: agent.usage-widget/E5
    def test_account_change_during_read_discards_response(self):
        self.agent.server.call.return_value = {'account': {'type': 'apiKey'}}
        self.agent.usage_identity.side_effect = ['opaque-a', 'opaque-b']
        with self.assertRaises(RuntimeError): self.agent.usage()

    # covers: agent.usage-widget/E2
    def test_background_thread_token_is_recorded(self):
        self.agent.on_notification('thread/tokenUsage/updated', {'threadId': 'other', 'turnId': 't',
                                    'tokenUsage': {'total': {'totalTokens': 300}, 'last': {'totalTokens': 100}}})
        (provider, text), = self.pushed('RecordTokens')
        self.assertEqual(provider, 'codex')
        self.assertEqual(json.loads(text), {'accountKey': 'opaque-a', 'session': 'other', 'turn': 't', 'total': 300, 'last': 100})
        self.assertEqual(len(self.agent.usage_tokens), 1)
        self.agent.emit_raw.assert_not_called()  # no longer a chat event

    def test_quota_and_turn_changes_ask_for_a_new_read(self):
        self.agent.on_notification('account/rateLimits/updated', {'rateLimits': {'primary': None}})
        self.agent.on_notification('turn/started', {'threadId': 'other', 'turn': {'id': 't'}})
        self.assertEqual(self.pushed('ProviderChanged'), [('codex',), ('codex',)])

    # covers: agent.usage-widget/E5
    def test_tokens_from_another_account_never_return(self):
        self.agent.server.call.return_value = {'account': {'type': 'apiKey'}}
        self.agent.usage_tokens = {'x': {'accountKey': 'opaque-b'}}
        self.assertEqual(self.agent.usage()['tokenEvents'], [])

    # covers: agent.usage-widget/E2
    def test_late_usage_keeps_account_from_turn_start(self):
        self.agent.usage_accounts[('other', 'old-turn')] = 'opaque-old'
        self.agent.on_notification('thread/tokenUsage/updated', {'threadId': 'other', 'turnId': 'old-turn',
                                    'tokenUsage': {'total': {'totalTokens': 300}, 'last': {'totalTokens': 100}}})
        self.assertEqual(json.loads(self.pushed('RecordTokens')[0][1])['accountKey'], 'opaque-old')
        self.agent.server.call.return_value = {'account': {'type': 'apiKey'}}
        self.assertEqual(self.agent.usage()['tokenEvents'], [])


class DescriptorTests(unittest.TestCase):
    # covers: agent.usage-widget/E3
    def test_codex_descriptor_names_the_agents_usage_method(self):
        d = json.loads((root / 'agent/assistant/agent-usage/codex.json').read_text())
        self.assertEqual((d['schema'], d['id']), (1, 'codex'))
        interface = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
                                          and getattr(n.targets[0], 'id', '') == 'INTERFACE'))
        self.assertIn(f'<interface name="{d["dbus"]["interface"]}">', interface)
        self.assertIn(f'<method name="{d["dbus"]["method"]}"><arg type="s" direction="out"/></method>', interface)
        consts = {n.targets[0].id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)}
        self.assertEqual(d['dbus']['service'], consts['BUS_NAME'])
        self.assertEqual(d['dbus']['path'], consts['OBJECT_PATH'])
        build = (root / 'packaging/rungic-voice-agent/build.sh').read_text()
        self.assertIn('/usr/share/rungic/agent-usage/providers/codex.json', build)
        self.assertIn('"$V"/agent-usage/icons/*.svg', build)

    # covers: agent.usage-widget/E3
    def test_shipped_icons_are_the_files_their_packages_install(self):
        # Each descriptor's icon must be a file its package installs to /usr/share/rungic/agent-usage/icons.
        for descriptor, icons in (('agent/assistant/agent-usage/codex.json', 'agent/assistant/agent-usage/icons'),
                                  ('agent/suggestions/agent-usage/claude-code.json', 'agent/suggestions/agent-usage/icons')):
            icon = json.loads((root / descriptor).read_text())['icon']
            self.assertIn('light', icon)
            for path in icon.values():
                self.assertTrue(path.startswith('/usr/share/rungic/agent-usage/icons/'), path)
                self.assertTrue((root / icons / Path(path).name).read_text().lstrip().startswith('<svg'), path)
        cmake = (root / 'agent/suggestions/CMakeLists.txt').read_text()
        self.assertIn('install(FILES agent-usage/icons/claude-code.svg DESTINATION share/rungic/agent-usage/icons)', cmake)
        sources = json.loads((root / 'provenance/agent-usage-icons-20260930/sources.json').read_text())['files']
        import hashlib
        for name, record in sources.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), record['sha256'], name)
