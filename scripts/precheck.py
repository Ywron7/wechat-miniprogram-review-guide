#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小程序提审预检 · precheck.py

静态扫描微信小程序项目，按提审六道闸输出 PASS / WARN / FAIL 报告。
不联网、不上传、不修改任何文件。

用法:
    python precheck.py <项目根目录>
    python precheck.py <项目根目录> --md 报告.md

退出码:
    0  全部 PASS 或只有 WARN
    1  存在 FAIL
    2  参数错误 / 找不到 app.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 词表

MARKETING_WORDS = [
    "免费", "永久免费", "最", "最好", "最佳", "第一", "首家", "顶级",
    "国家级", "官方", "正版", "授权", "认证", "一键", "神器", "必备",
    "破解", "秒杀", "全网", "万能", "无限", "终身",
]

GENERIC_WORDS = [
    "工具箱", "工具", "助手", "大全", "导航", "查询", "平台", "中心",
    "转换", "压缩", "计算", "生成", "处理", "识别", "集合", "汇总",
]

# 需要隐私授权的接口 -> 说明
PRIVACY_APIS = {
    "getUserProfile": "获取用户信息（头像昵称）",
    "getUserInfo": "旧版获取用户信息",
    "getPhoneNumber": "获取手机号",
    "getLocation": "获取位置",
    "chooseLocation": "选择位置",
    "onLocationChange": "持续定位（审核最严）",
    "startLocationUpdate": "开启定位",
    "chooseImage": "选图（旧版）",
    "chooseMedia": "选图/视频",
    "chooseVideo": "选视频",
    "saveImageToPhotosAlbum": "存图到相册",
    "saveVideoToPhotosAlbum": "存视频到相册",
    "createCameraContext": "相机",
    "chooseAddress": "收货地址",
    "addPhoneContact": "添加手机联系人",
    "getClipboardData": "读剪贴板",
}

# 内容安全 5.18 相关（内容产出 / 外流路径）
CONTENT_APIS = {
    "onShareAppMessage": "允许分享（内容可外流）",
    "onShareTimeline": "允许分享到朋友圈",
    "shareAppMessage": "主动触发分享",
    "canvasToTempFilePath": "canvas 导出图片",
    "saveImageToPhotosAlbum": "图片存相册（生成物离开小程序）",
    "saveVideoToPhotosAlbum": "视频存相册",
    "getFileSystemManager": "文件系统（可写文件导出）",
    "chooseMessageFile": "从聊天选文件",
}

# 提审节奏相关：未完成品痕迹
UNFINISHED_WORDS = [
    "即将上线", "敬请期待", "功能开发中", "开发中", "未完待续",
    "测试版", "内测版", "beta", "Beta", "BETA", "TODO",
]

# 越界：工具类目不得有传播/公开访问
SOCIAL_WORDS = [
    "社区", "论坛", "广场", "发帖", "评论", "点赞", "关注",
    "他人作品", "公开分享", "共享笔记", "排行榜",
]

SKIP_DIRS = {
    "node_modules", ".git", "unpackage", "dist", ".hbuilderx",
    "__pycache__", ".idea", ".vscode", "miniprogram_npm",
}

TEXT_EXT = {".js", ".ts", ".wxml", ".vue", ".json", ".uvue", ".uts"}
CODE_EXT = {".js", ".ts", ".wxml", ".vue", ".uvue", ".uts"}


# ---------------------------------------------------------------- 工具

class Report:
    def __init__(self):
        self.items = []

    def add(self, gate, level, title, detail="", fix=""):
        self.items.append({
            "gate": gate, "level": level, "title": title,
            "detail": detail, "fix": fix,
        })

    def count(self, level):
        return sum(1 for i in self.items if i["level"] == level)

    def render(self, root, cfg_path, cfg_kind):
        lines = []
        lines.append("# 小程序提审预检报告")
        lines.append("")
        lines.append(f"- 扫描目录：`{root}`")
        if cfg_path:
            lines.append(f"- 项目配置：`{cfg_path}`（{cfg_kind}）")
        else:
            lines.append("- 项目配置：**未找到 app.json / pages.json**")
        lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("## 总览")
        lines.append("")
        lines.append(f"**FAIL {self.count('FAIL')} ｜ WARN {self.count('WARN')} ｜ PASS {self.count('PASS')}**")
        lines.append("")
        if self.count("FAIL"):
            lines.append("> ⚠️ 存在 FAIL 项，**不要提审**。提审不会帮你发现问题，只会帮你排队一天。")
            lines.append("")

        order = ["名称", "类目", "备案", "隐私", "内容安全", "提审节奏", "其他"]
        for gate in order:
            group = [i for i in self.items if i["gate"] == gate]
            if not group:
                continue
            lines.append(f"## {gate}")
            lines.append("")
            for it in group:
                mark = {"FAIL": "❌", "WARN": "⚠️", "PASS": "✅"}[it["level"]]
                lines.append(f"{mark} **[{it['level']}]** {it['title']}")
                if it["detail"]:
                    lines.append(f"  - {it['detail']}")
                if it["fix"]:
                    lines.append(f"  - 改法：{it['fix']}")
                lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("本报告只做静态扫描，不联网、不上传。脚本扫不到的部分需要人工判断，")
        lines.append("参见 docs/ 下对应文档。")
        return "\n".join(lines)


def iter_files(root, exts):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in exts:
                yield p


def read_text(p, limit=2_000_000):
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:limit]
    except Exception:
        return ""


def loads_lenient(text):
    """HBuilderX 生成的 manifest.json / pages.json 常带注释和尾逗号，标准 json 会失败。"""
    try:
        return json.loads(text)
    except Exception:
        pass
    s = re.sub(r"/\*.*?\*/", "", text, flags=re.S)      # 块注释
    s = re.sub(r"^\s*//.*$", "", s, flags=re.M)          # 行注释
    s = re.sub(r",(\s*[}\]])", r"\1", s)                 # 尾逗号
    try:
        return json.loads(s)
    except Exception:
        return None


def find_config(root):
    """找项目配置。返回 (path, kind)，kind 为 'app.json' 或 'pages.json'（uni-app）。

    优先编译产物，其次源码。uni-app 项目源码里只有 pages.json，没有 app.json。
    """
    app_candidates = [
        root / "app.json",
        root / "src" / "app.json",
        root / "dist" / "build" / "mp-weixin" / "app.json",
        root / "unpackage" / "dist" / "build" / "mp-weixin" / "app.json",
        root / "miniprogram" / "app.json",
    ]
    for c in app_candidates:
        if c.is_file():
            return c, "app.json"

    pages_candidates = [
        root / "pages.json",
        root / "src" / "pages.json",
        root / "dist" / "build" / "mp-weixin" / "app.json",
    ]
    for c in pages_candidates:
        if c.is_file():
            return c, "pages.json"

    # 兜底：任意深度找，取路径最短的
    for name, kind in (("app.json", "app.json"), ("pages.json", "pages.json")):
        found = [p for p in root.rglob(name) if not any(s in p.parts for s in SKIP_DIRS)]
        if found:
            found.sort(key=lambda p: len(p.parts))
            return found[0], kind
    return None, None


def find_app_json(root):
    """只返回 app.json（隐私检查用）。"""
    p, kind = find_config(root)
    return p if kind == "app.json" else None


def read_manifest_name(root):
    """uni-app 的 manifest.json 里有应用名，通常就是注册名。"""
    for c in (root / "manifest.json", root / "src" / "manifest.json"):
        if c.is_file():
            data = loads_lenient(read_text(c))
            if isinstance(data, dict):
                name = data.get("name") or ""
                if name:
                    return str(name)
            # 兜底：正则
            m = re.search(r'"name"\s*:\s*"([^"]+)"', read_text(c))
            if m:
                return m.group(1)
    return ""


# ---------------------------------------------------------------- 各闸检查

def check_name(rep, cfg_data, cfg_kind, root):
    """名称闸：注册名（manifest）+ 各页标题文字里的营销词 / 通用词"""
    if not cfg_data:
        rep.add("名称", "WARN", "无法读取项目配置，跳过标题检查",
                "脚本既没找到 app.json 也没找到 pages.json",
                "确认项目根目录是否正确，或手动检查各页标题")
    else:
        titles = []
        if cfg_kind == "app.json":
            window = cfg_data.get("window", {}) or {}
            if isinstance(window, dict) and window.get("navigationBarTitleText"):
                titles.append(("app.json window.navigationBarTitleText",
                               window["navigationBarTitleText"]))
        else:  # uni-app pages.json
            gs = cfg_data.get("globalStyle", {}) or {}
            if isinstance(gs, dict) and gs.get("navigationBarTitleText"):
                titles.append(("pages.json globalStyle.navigationBarTitleText",
                               gs["navigationBarTitleText"]))
            for i, pg in enumerate(cfg_data.get("pages", []) or []):
                if not isinstance(pg, dict):
                    continue
                style = pg.get("style") or {}
                if isinstance(style, dict) and style.get("navigationBarTitleText"):
                    titles.append((f"pages[{i}] {pg.get('path', '')}",
                                   style["navigationBarTitleText"]))
        tabbar = cfg_data.get("tabBar", {}) or {}
        if isinstance(tabbar, dict):
            for i, item in enumerate(tabbar.get("list", []) or []):
                if isinstance(item, dict) and item.get("text"):
                    titles.append((f"tabBar.list[{i}].text", item["text"]))

        if not titles:
            rep.add("名称", "PASS", "项目配置中没有发现标题文字",
                    f"读取的是 {cfg_kind}")
        for where, text in titles:
            hits_m = [w for w in MARKETING_WORDS if w in text]
            hits_g = [w for w in GENERIC_WORDS if w in text]
            if hits_m:
                rep.add("名称", "FAIL", f"标题含营销商业化词：{text}",
                        f"位置 {where}，命中 {hits_m}",
                        "官方 1.1-2 禁止商业化用语。页面标题虽不受命名规范约束，"
                        "但出现这些词等于给审核白送抓手，建议全部替换")
            elif hits_g:
                rep.add("名称", "WARN", f"标题含通用词：{text}",
                        f"位置 {where}，命中 {hits_g}",
                        "页面标题不受命名规范约束，但若注册名称也是这种结构会被驳（官方 1.2.1-3）")
            else:
                rep.add("名称", "PASS", f"标题无明显风险词：{text}", f"位置 {where}")

    # 注册名（uni-app manifest.json 的 name 通常就是注册名）
    app_name = read_manifest_name(root)
    if app_name:
        hits_m = [w for w in MARKETING_WORDS if w in app_name]
        hits_g = [w for w in GENERIC_WORDS if w in app_name]
        if hits_m:
            rep.add("名称", "FAIL", f"应用名含营销商业化词：{app_name}",
                    f"来自 manifest.json，命中 {hits_m}",
                    "官方 1.1-2。这个值很可能就是你的注册名，必须改")
        elif hits_g:
            rep.add("名称", "WARN", f"应用名含通用词：{app_name}",
                    f"来自 manifest.json，命中 {hits_g}",
                    "若这个名字同时是注册名，「功能词+品类词」结构会被驳（官方 1.2.1-3）。"
                    "改成「造词/品牌词+功能词」")
        else:
            rep.add("名称", "PASS", f"应用名无明显风险词：{app_name}", "来自 manifest.json")


def check_privacy(rep, root):
    """隐私闸：扫隐私接口"""
    hits = {}
    for p in iter_files(root, CODE_EXT):
        text = read_text(p)
        for api, desc in PRIVACY_APIS.items():
            if re.search(r"\b" + re.escape(api) + r"\b", text):
                hits.setdefault(api, {"desc": desc, "files": []})
                hits[api]["files"].append(str(p.relative_to(root)))

    if not hits:
        rep.add("隐私", "PASS", "未发现隐私接口调用",
                "没有调用 getUserProfile / getLocation / chooseImage 等接口",
                "不申请就不审，这一关基本不会卡你。别自己加戏")
        return

    for api, info in sorted(hits.items()):
        files = info["files"][:3]
        rep.add("隐私", "WARN", f"调用了 {api}（{info['desc']}）",
                "出现在：" + "、".join(files) + ("…" if len(info["files"]) > 3 else ""),
                "必须配隐私指引 + 授权弹窗，且后台「用户隐私保护指引」要审核通过；"
                "用户拒绝时功能要降级不崩溃")

    # 隐私指引（注意：__usePrivacyCheck__ 自 2023-10-17 后已失效，不再作为判据）
    app = find_app_json(root)
    if app:
        data = read_text(app)
        if "__usePrivacyCheck__" in data:
            rep.add("隐私", "WARN", "app.json 里设了 __usePrivacyCheck__",
                    f"检查文件：{app.relative_to(root)}",
                    "这个开关只在 2023-09-15 ~ 2023-10-17 的过渡期有效，现已失效。"
                    "留着无害，但别指望它起任何作用；真正决定接口能不能调的，"
                    "只有后台《用户隐私保护指引》是否审核通过")
    rep.add("隐私", "WARN", "后台隐私指引状态无法静态扫描，需人工确认",
            "脚本读不到 mp 后台",
            "① 每个隐私接口都能对应到后台勾选的一项 ② 状态必须是「已通过」不是「审核中」"
            "（审核中时接口一定报 scope not declared，清缓存换机都无效）"
            "③ 提审表单隐私栏必须选「采集用户隐私」。详见 docs/04-隐私.md")


def check_content_safety(rep, root):
    """内容安全闸：内容产出 + 外流路径"""
    found = {}
    for p in iter_files(root, CODE_EXT):
        text = read_text(p)
        for api, desc in CONTENT_APIS.items():
            if re.search(r"\b" + re.escape(api) + r"\b", text):
                found.setdefault(api, {"desc": desc, "files": []})
                found[api]["files"].append(str(p.relative_to(root)))

    produces = any(k in found for k in
                   ("canvasToTempFilePath", "saveImageToPhotosAlbum", "saveVideoToPhotosAlbum"))
    shares = any(k in found for k in
                 ("onShareAppMessage", "onShareTimeline", "shareAppMessage"))
    leaves = produces or shares

    if not found:
        rep.add("内容安全", "PASS", "未发现内容产出或外流路径",
                "没有 canvas 导出、存相册、分享相关调用",
                "5.18 射程外，这一关风险低")
        return

    for api, info in sorted(found.items()):
        files = info["files"][:3]
        rep.add("内容安全", "WARN", f"发现 {api}（{info['desc']}）",
                "出现在：" + "、".join(files) + ("…" if len(info["files"]) > 3 else ""),
                "见 docs/05-内容安全.md")

    if produces and leaves:
        rep.add("内容安全", "FAIL",
                "产品形态落在 5.18 射程内：既产出新内容，又让内容离开小程序",
                f"产出类接口 {[k for k in found if k in ('canvasToTempFilePath','saveImageToPhotosAlbum')]}；"
                f"外流类接口 {[k for k in found if k in ('onShareAppMessage','onShareTimeline','shareAppMessage')]}",
                "官方口径：只要「处理用户提供的内容并产出新内容」，就要求你对内容做识别。"
                "界面显不显示、上传不上传都不改变这个要求。三条路选一条："
                "① 接服务端内容安全接口（要钱要后端）② 改形态让内容不出本机也不产出新内容 ③ 不做这个形态")

    # 越界：工具类目不得有社交功能
    social_hits = []
    for p in iter_files(root, CODE_EXT | {".json"}):
        text = read_text(p, 300_000)
        for w in SOCIAL_WORDS:
            if w in text:
                social_hits.append((w, str(p.relative_to(root))))
    if social_hits:
        words = sorted({w for w, _ in social_hits})
        rep.add("内容安全", "WARN", f"出现社交/传播类词汇：{words}",
                "示例位置：" + "、".join(sorted({f for _, f in social_hits})[:3]),
                "工具类目的边界是「无传播分享/公开访问」（图片处理、备忘录都是这么写的）。"
                "带社区/评论/排行榜等功能属于社交范畴，个人主体拿不到该类目")


def check_unfinished(rep, root):
    """提审节奏闸：未完成品痕迹"""
    hits = []
    for p in iter_files(root, TEXT_EXT):
        text = read_text(p, 500_000)
        for w in UNFINISHED_WORDS:
            if w in text:
                hits.append((w, str(p.relative_to(root))))
    if hits:
        words = sorted({w for w, _ in hits})
        rep.add("提审节奏", "FAIL", f"发现未完成品痕迹：{words}",
                "示例位置：" + "、".join(sorted({f for _, f in hits})[:3]),
                "官方 3.3-1：提交的小程序须是完成品，不可为测试版。"
                "「即将上线」「敬请期待」这类字样会被直接驳，提审前全部清掉")
    else:
        rep.add("提审节奏", "PASS", "未发现未完成品痕迹")

    # 账号体系 -> 需要测试号
    has_login = False
    for p in iter_files(root, CODE_EXT):
        text = read_text(p, 200_000)
        if re.search(r"\b(wx\.login|uni\.login)\b", text):
            has_login = True
            break
    if has_login:
        rep.add("提审节奏", "WARN", "检测到登录逻辑（wx.login / uni.login）",
                "有账号体系的小程序",
                "官方 3.3-4：须提供测试号（账号+密码，可体验所有功能），提审时填进备注。"
                "另外须有易于发现的「退出」入口（官方 3.5-5）")
    else:
        rep.add("提审节奏", "PASS", "未检测到账号体系，无需提供测试号")


def check_size(rep, root, app_json_data):
    """包体积提示"""
    app = find_app_json(root)
    if not app:
        return
    base = app.parent
    total = 0
    count = 0
    for p in iter_files(base, {".js", ".json", ".wxml", ".wxss", ".wxs",
                               ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg",
                               ".ttf", ".woff", ".woff2", ".mp4", ".mp3"}):
        try:
            total += p.stat().st_size
            count += 1
        except Exception:
            pass
    mb = total / 1024 / 1024
    level = "WARN" if mb > 2 else "PASS"
    note = "主包上限 2MB，超了要分包" if mb > 2 else "主包上限 2MB，当前在范围内"
    rep.add("提审节奏", level, f"代码目录体积约 {mb:.2f} MB（{count} 个文件）",
            f"统计目录：{base}",
            note + "。注意：这是源码/产物目录总和，不等于上传后的主包体积，"
                   "最终以开发者工具「详情→本地代码」显示为准")


def check_gate_reminders(rep):
    """无法静态扫描的闸，给提醒"""
    rep.add("类目", "WARN", "类目无法静态扫描，需人工确认",
            "脚本读不到后台配置",
            "① 核心功能能在个人开放类目清单里找到一行 ② 每个类目首页 2 次点击内可达 "
            "③ 已填类目 ≤ 5 ④ 无传播/分享/公开访问功能。详见 docs/02-服务类目.md")
    rep.add("备案", "WARN", "备案状态无法静态扫描，需人工确认",
            "脚本读不到备案后台",
            "① 备注无禁词（电商/付费/广告/商用/卖家）② 地址精确到门牌号且与身份证一致 "
            "③ 备注写的功能 = 实际有的功能。详见 docs/03-备案.md")
    rep.add("名称", "WARN", "注册名称无法静态扫描，需人工确认",
            "脚本只能扫代码里的标题，读不到后台注册名称",
            "① 名字里无营销词、无通用品类词 ② 是「造词/品牌词+功能词」结构 "
            "③ 后台「还能修改 N 次」已截图且 N≥1。详见 docs/01-名称.md")


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="小程序提审预检")
    ap.add_argument("root", help="小程序项目根目录")
    ap.add_argument("--md", help="同时把报告写到这个 .md 文件")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"[错误] 目录不存在：{root}", file=sys.stderr)
        return 2

    cfg_path, cfg_kind = find_config(root)
    cfg_data = None
    if cfg_path:
        cfg_data = loads_lenient(read_text(cfg_path))
        if cfg_data is None:
            print(f"[提示] 配置解析失败（{cfg_path.name}），跳过配置相关检查", file=sys.stderr)

    rep = Report()
    check_name(rep, cfg_data, cfg_kind, root)
    check_gate_reminders(rep)
    check_privacy(rep, root)
    check_content_safety(rep, root)
    check_unfinished(rep, root)
    check_size(rep, root, cfg_data)

    out = rep.render(root, cfg_path, cfg_kind)
    print(out)

    if args.md:
        Path(args.md).write_text(out, encoding="utf-8")
        print(f"\n[已写入] {args.md}", file=sys.stderr)

    return 1 if rep.count("FAIL") else 0


if __name__ == "__main__":
    sys.exit(main())
