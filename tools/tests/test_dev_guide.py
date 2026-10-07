#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What a developer or development agent finds on arrival (docs/README.md): the dated user
requirements of AGENTS.md and the project skills it names, the document index with every document,
and README pictures that are finished products, not raw captures.

Requires Pillow (the development venv, sh tools/dev-setup.sh)."""
import re
import subprocess
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def tracked(*paths):
    out = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', *paths], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode()
    return sorted(n for n in out.split('\0') if n and (ROOT / n).exists())


class AgentGuideTests(unittest.TestCase):
    # covers: delivery.dev-guide/E1
    def test_dated_requirements_and_the_skills_they_name(self):
        text = (ROOT / 'AGENTS.md').read_text()
        dated = re.findall(r'用户于\s*(\d{4}-\d{2}-\d{2})', text)
        self.assertGreaterEqual(len(dated), 10, 'the user\'s requirements carry the date they were given')
        named = set(re.findall(r'\$(rungic-[a-z0-9-]+)', text))
        self.assertEqual(named, {'rungic-three-stage-image', 'rungic-dev-release', 'rungic-phone-acceptance'})
        for skill in named:
            with self.subTest(skill):
                body = (ROOT / '.agents/skills' / skill / 'SKILL.md').read_text()
                front = re.match(r'---\n(.*?)\n---\n', body, re.S)
                self.assertTrue(front, 'SKILL.md starts with its front matter')
                self.assertIn(f'name: {skill}\n', front[1] + '\n')
                self.assertRegex(front[1], r'(?m)^description: \S')
                self.assertIn(f'.agents/skills/{skill}/SKILL.md', text, 'AGENTS.md says where the skill is')
        # Claude Code finds a skill through its link in .claude/skills: each is a project skill of the
        # same name (one AGENTS.md names, or another project-local one, such as asd-ste100).
        links = sorted((ROOT / '.claude/skills').iterdir())
        self.assertTrue(links)
        for link in links:
            with self.subTest(link.name):
                self.assertTrue(link.is_symlink())
                self.assertEqual(link.resolve(), (ROOT / '.agents/skills' / link.name).resolve())
                front = re.match(r'---\n(.*?)\n---\n', (link / 'SKILL.md').read_text(), re.S)
                self.assertTrue(front, 'SKILL.md starts with its front matter')
                self.assertRegex(front[1], rf'(?m)^name: {re.escape(link.name)}$')
        self.assertIn('Claude Code 经 `.claude/skills/` 链接调用', text)


class DocumentIndexTests(unittest.TestCase):
    # covers: delivery.dev-guide/E2
    def test_the_index_lists_every_document(self):
        index = (ROOT / 'docs/README.md').read_text()
        targets = {(ROOT / 'docs' / t).resolve() for t in re.findall(r'\]\(([^)#\s]+)', index)
                   if not t.startswith(('http:', 'https:'))}
        documents = [n for n in tracked('docs') if n.endswith('.md') and n != 'docs/README.md']
        self.assertGreater(len(documents), 100)
        missing = [n for n in documents if (ROOT / n).resolve() not in targets]
        self.assertEqual(missing, [], 'documents not in the index of docs/README.md')
        broken = sorted(str(t.relative_to(ROOT)) for t in targets if t.is_relative_to(ROOT) and not t.exists())
        self.assertEqual(broken, [], 'the index links to documents that do not exist')


class ReadmePictureTests(unittest.TestCase):
    # covers: delivery.dev-guide/E3
    def test_readme_pictures_are_chosen_reduced_and_carry_no_metadata(self):
        pictures = tracked('docs/images')
        self.assertTrue(pictures)
        readme = (ROOT / 'README.md').read_text()
        for name in pictures:
            with self.subTest(name):
                self.assertTrue(name.startswith('docs/images/readme/'), 'README pictures live in docs/images/readme/')
                self.assertIn(name, readme, 'chosen for the README: nothing kept that it does not show')
                path = ROOT / name
                self.assertIn(path.suffix, ('.jpg', '.gif', '.webp'), 'finished pictures, not raw captures (.png, video)')
                image = Image.open(path)
                self.assertLessEqual(max(image.size), 1600, 'reduced in size')
                self.assertLess(path.stat().st_size, 5 << 20)
                self.assertFalse(image.getexif(), 'no camera, time or place metadata')
                self.assertNotIn('xmp', image.info)
                self.assertNotIn('icc_profile', image.info)
