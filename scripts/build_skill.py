#!/usr/bin/env python3
"""从仓库唯一源码生成 skill ZIP；不维护另一份 precheck.py 或参考文档。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(output_dir):
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION 必须是 x.y.z')
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=str(ROOT), text=True).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=str(ROOT), text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = 'unknown', None
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for package in ('mp-review-check', 'mp-review-guard'):
        files = {'SKILL.md': (ROOT/'skills'/package/'SKILL.md').read_bytes(),
                 'LICENSE': (ROOT/'LICENSE').read_bytes()}
        files['README.md'] = (f'# {package}\n\n解压后将本文件夹放入所用 agent 的 skills 目录，执行流程见 SKILL.md。\n\n'
                              '本包由 https://github.com/Ywron7/wechat-miniprogram-review-guide 自动生成。\n'
                              '参考文档位于 references/；版本、源提交与文件摘要见 BUILD.json。\n'
                              '请修改仓库源码后重新构建，不直接维护解压副本。结果不保证审核通过。\n').encode('utf-8')
        files.update({'references/'+p.name: p.read_bytes() for p in sorted((ROOT/'docs').glob('*.md'))})
        if package == 'mp-review-guard':
            files['scripts/precheck.py'] = (ROOT/'scripts/precheck.py').read_bytes()
            files.update({'templates/'+p.name: p.read_bytes() for p in sorted((ROOT/'templates').glob('*.md'))})
        manifest = {'version': version, 'package': package, 'repository': 'https://github.com/Ywron7/wechat-miniprogram-review-guide',
                    'source_commit': commit, 'source_dirty': dirty,
                    'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}
        files['BUILD.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
        target = output_dir/(package+'.zip')
        temp = target.with_suffix('.zip.tmp')
        with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(package+'/'+name, date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        temp.replace(target)
        outputs.append(target)
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'dist')
    args = parser.parse_args()
    for path in build(args.output_dir):
        print(path)


if __name__ == '__main__':
    main()
