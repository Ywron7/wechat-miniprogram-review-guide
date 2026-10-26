import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/precheck.py'
spec = importlib.util.spec_from_file_location('precheck', SCRIPT)
precheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(precheck)


class ParserTests(unittest.TestCase):
    def test_strings_survive_comments_and_trailing_commas(self):
        values = ['章节/*原样*/', '文本,}', '文本,]', 'https://example.com/a//b', '引号"与反斜线\\', '中文\n换行']
        for value in values:
            with self.subTest(value=value):
                text = json.dumps({'name': value, 'nested': [1, {'value': value}]}, ensure_ascii=False)
                text = text[:-1] + ', // 行尾注释\n /* block */ }'
                self.assertEqual(precheck.loads_lenient(text), {'name': value, 'nested': [1, {'value': value}]})

    def test_bom_and_nested_trailing_commas(self):
        self.assertEqual(precheck.loads_lenient('\ufeff{"pages":[{"path":"index",},],}'), {'pages': [{'path': 'index'}]})

    def test_line_comment_at_eof(self):
        self.assertEqual(precheck.loads_lenient('{"pages":[]} // done'), {'pages': []})

    def test_invalid_or_unterminated_input_is_rejected(self):
        for text in ('{', '{"x":1} /* unfinished', '{name: "not JSONC"}', "{'x':1}"):
            with self.subTest(text=text):
                self.assertIsNone(precheck.loads_lenient(text))


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, text):
        dest = self.root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8')
        return dest

    def project(self, prefix='', code='Page({});', title='记录'):
        self.write(prefix + 'app.json', json.dumps({'pages': ['pages/index/index'], 'window': {'navigationBarTitleText': title}}, ensure_ascii=False))
        self.write(prefix + 'app.js', 'App({});')
        self.write(prefix + 'pages/index/index.js', code)
        self.write(prefix + 'pages/index/index.wxml', '<view>记录</view>')

    def run_cli(self, root=None, *args):
        return subprocess.run([sys.executable, str(SCRIPT), str(root or self.root), *args], capture_output=True, text=True, encoding='utf-8', env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8'))

    def detected(self, code, suffix='.js'):
        path = self.write('source' + suffix, code)
        return precheck.api_names(path, dict(precheck.PRIVACY_APIS, **precheck.CONTENT_APIS))

    def test_comments_and_strings_do_not_count_as_calls(self):
        self.assertEqual(self.detected('// wx.chooseMedia();\n/* wx.canvasToTempFilePath(); */\nconst s="wx.chooseMedia()";'), set())

    def test_real_calls_and_share_hooks_are_detected(self):
        self.assertEqual(self.detected('wx.chooseMedia({}); uni.saveImageToPhotosAlbum({}); Page({onShareAppMessage(){return {};}});'), {'chooseMedia', 'saveImageToPhotosAlbum', 'onShareAppMessage'})

    def test_optional_and_literal_property_calls(self):
        self.assertEqual(self.detected('wx?.chooseMedia?.({}); uni["saveImageToPhotosAlbum"]({});'), {'chooseMedia', 'saveImageToPhotosAlbum'})

    def test_regex_and_division_do_not_hide_following_calls(self):
        code = r'const url=/https?:\/\//; const fake=/wx.chooseMedia\(\)/; const n=a / b; wx.chooseMedia({});'
        self.assertEqual(self.detected(code), {'chooseMedia'})

    def test_template_raw_text_is_ignored_but_expression_is_scanned(self):
        self.assertEqual(self.detected('const s=`wx.chooseMedia()`;'), set())
        self.assertEqual(self.detected('const s=`text ${ /* wx.canvasToTempFilePath(); */ wx.chooseMedia({}) }`;'), {'chooseMedia'})

    def test_vue_and_wxml_comments_and_components(self):
        text = '<!-- <script>wx.chooseMedia({})</script> --><template><!-- <camera/> --><button open-type="getPhoneNumber">授权</button></template><script>const url="https://example.com"; uni.chooseMedia({});</script>'
        self.assertEqual(self.detected(text, '.vue'), {'getPhoneNumber', 'chooseMedia'})
        self.assertEqual(self.detected('<!-- <camera/> --><camera />', '.wxml'), {'createCameraContext'})

    def test_empty_directory_is_an_error_without_pass_report(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('找不到', result.stderr)
        self.assertNotIn('[PASS]', result.stdout)

    def test_invalid_configuration_cannot_report_success(self):
        for text in ('{', '[]', 'null', '{"pages":{}}', '{"pages":[1]}', '{"pages":[""]}'):
            with self.subTest(text=text):
                self.write('app.json', text)
                result = self.run_cli()
                self.assertEqual(result.returncode, 2)
                self.assertNotIn('[PASS]', result.stdout)

    def test_nested_builds_are_scanned_from_selected_config(self):
        for prefix in ('dist/build/mp-weixin/', 'unpackage/dist/build/mp-weixin/'):
            with self.subTest(prefix=prefix):
                with tempfile.TemporaryDirectory() as temp:
                    old = self.root
                    self.root = Path(temp)
                    try:
                        self.project(prefix, 'Page({pick(){wx.chooseMedia({});}});')
                        outer = self.run_cli()
                        direct = self.run_cli(self.root / prefix)
                        self.assertEqual(outer.returncode, 0)
                        self.assertIn('检出 chooseMedia', outer.stdout)
                        self.assertIn('检出 chooseMedia', direct.stdout)
                        self.assertIn('实际代码目录', outer.stdout)
                    finally:
                        self.root = old

    def test_uniapp_source_precedes_stale_build(self):
        self.write('pages.json', '{"pages":[{"path":"pages/index/index"}], // source\n"globalStyle":{}}')
        self.write('pages/index/index.vue', '<script>uni.chooseMedia({})</script>')
        self.project('dist/build/mp-weixin/')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn('（pages.json）', result.stdout)
        self.assertIn('检出 chooseMedia', result.stdout)

    def test_internal_markers_and_recent_title_do_not_block(self):
        self.project(code='// TODO: optimize internal cache\nconst beta=0.95; Page({});', title='最近记录')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('[FAIL]', result.stdout)
        self.assertNotIn("命中 ['最']", result.stdout)
        self.assertNotIn('未完成品痕迹', result.stdout)

    def test_manifest_at_wrapper_root_is_checked_with_src_pages(self):
        self.write('src/pages.json', '{"pages":[{"path":"pages/index/index"}]}')
        self.write('manifest.json', '{"name":"一键记录"}')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn('本地应用名命中待核查用语', result.stdout)

    def test_visible_unfinished_text_is_a_warning(self):
        self.project()
        self.write('pages/index/index.wxml', '<!-- TODO --><view>功能开发中</view>')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn('界面文本命中待核查文案', result.stdout)
        self.assertNotIn('[FAIL]', result.stdout)

    def test_canvas_comment_is_not_a_product_violation(self):
        self.project(code='// This code does not call wx.canvasToTempFilePath.\nPage({});')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('检出 canvasToTempFilePath', result.stdout)
        self.assertNotIn('[FAIL]', result.stdout)

    def test_real_export_is_reported_without_automatic_violation(self):
        self.project(code='Page({draw(){wx.canvasToTempFilePath({canvasId:"brand"});}});')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn('检出 canvasToTempFilePath', result.stdout)
        self.assertIn('相册保存 []；分享 []', result.stdout)
        self.assertNotIn('[FAIL]', result.stdout)

    def test_empty_native_pages_is_confirmed_structural_fail(self):
        self.write('app.json', '{"pages":[]}')
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn('[FAIL]', result.stdout)

    def test_optional_markdown_output_and_write_error(self):
        self.project()
        output = self.root / 'report.md'
        result = self.run_cli(None, '--md', str(output))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output.read_text(encoding='utf-8'), result.stdout.rstrip('\n'))
        result = self.run_cli(None, '--md', str(self.root / 'missing/report.md'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('写入失败', result.stderr)


if __name__ == '__main__':
    unittest.main()
