from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Installation(unittest.TestCase):
    def test_clone_install_excludes_metadata_and_credentials_at_every_level(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source'
            target = Path(temporary) / 'installed'
            (source / 'scripts').mkdir(parents=True)
            shutil.copy2(ROOT / 'scripts/install.py', source / 'scripts/install.py')

            public_files = {
                'SKILL.md': '# Test Skill\n',
                'README.md': 'Installation documentation\n',
                'vendor/anatomy/LICENSE': 'Upstream license fixture\n',
                'references/guide.md': 'Reference documentation\n',
                'examples/topic/job.json': '{"topic":"public example"}\n',
                'examples/topic/.environment.md': 'Public environment guide\n',
            }
            excluded_files = {
                '.git/config': 'fake-sensitive-git-config',
                '.github/workflows/check.yml': 'fake-repository-workflow',
                '.env': 'fake-sensitive-environment',
                '.env.local': 'fake-sensitive-local-environment',
                '.env.example': 'fake-environment-template',
                '.DS_Store': 'fake-macos-metadata',
                'scripts/__pycache__/install.pyc': 'fake-python-cache',
                'scripts/old.pyc': 'fake-python-bytecode',
                'examples/topic/.git': 'fake-sensitive-worktree-git-file',
                'examples/topic/.github/workflows/check.yml': 'fake-nested-workflow',
                'examples/topic/.env': 'fake-sensitive-nested-environment',
                'examples/topic/.env.production': 'fake-sensitive-production-environment',
                'examples/topic/.DS_Store': 'fake-nested-macos-metadata',
                'examples/topic/__pycache__/example.pyc': 'fake-nested-cache',
                'examples/topic/old.pyc': 'fake-nested-bytecode',
            }
            for relative, content in {**public_files, **excluded_files}.items():
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')

            result = subprocess.run(
                [sys.executable, str(source / 'scripts/install.py'), '--target', str(target)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for relative, content in public_files.items():
                with self.subTest(preserved=relative):
                    self.assertEqual((target / relative).read_text(encoding='utf-8'), content)
            self.assertTrue((target / 'scripts/install.py').is_file())
            for relative in excluded_files:
                with self.subTest(excluded=relative):
                    self.assertFalse((target / relative).exists())
            self.assertFalse((target / '.git').exists())
            self.assertFalse((target / '.github').exists())
            self.assertFalse((target / 'examples/topic/.github').exists())


if __name__ == '__main__':
    unittest.main()
