import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_skill', ROOT/'scripts/build_skill.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuildTests(unittest.TestCase):
    def test_packages_match_canonical_source_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = builder.build(temp)
            self.assertEqual({p.name for p in paths}, {'mp-review-check.zip', 'mp-review-guard.zip'})
            for path in paths:
                package = path.stem
                with self.subTest(package=package), zipfile.ZipFile(path) as archive:
                    prefix = package+'/'
                    manifest = json.loads(archive.read(prefix+'BUILD.json'))
                    self.assertEqual(manifest['version'], (ROOT/'VERSION').read_text().strip())
                    self.assertEqual(archive.read(prefix+'LICENSE'), (ROOT/'LICENSE').read_bytes())
                    for name, digest in manifest['sha256'].items():
                        self.assertEqual(hashlib.sha256(archive.read(prefix+name)).hexdigest(), digest)
                    for doc in (ROOT/'docs').glob('*.md'):
                        self.assertEqual(archive.read(prefix+'references/'+doc.name), doc.read_bytes())
                    skill = archive.read(prefix+'SKILL.md').decode('utf-8')
                    for reference in re.findall(r'(?:references|templates)/[\w.-]+\.md', skill):
                        self.assertIn(prefix+reference, archive.namelist())
                    if package == 'mp-review-guard':
                        self.assertEqual(archive.read(prefix+'scripts/precheck.py'), (ROOT/'scripts/precheck.py').read_bytes())
                        for template in (ROOT/'templates').glob('*.md'):
                            self.assertEqual(archive.read(prefix+'templates/'+template.name), template.read_bytes())

    def test_build_is_reproducible_without_source_changes(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            a = {p.name: p.read_bytes() for p in builder.build(first)}
            b = {p.name: p.read_bytes() for p in builder.build(second)}
            self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
