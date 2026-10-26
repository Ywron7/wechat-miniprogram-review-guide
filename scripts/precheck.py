#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小程序提审预检 · precheck.py

静态扫描微信小程序项目，按提审六项检查输出 PASS / WARN / FAIL 报告。
不联网、不上传；默认只读扫描，显式传入 --md 时写入指定报告文件。

用法:
    python precheck.py <项目根目录>
    python precheck.py <项目根目录> --md 报告.md

退出码:
    0  全部 PASS 或只有 WARN
    1  存在 FAIL
    2  参数错误 / 找不到或无法解析 app.json、pages.json / 报告写入失败
"""

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 词表

MARKETING_WORDS = [
    "免费", "永久免费", "最好", "最佳", "最高级", "第一", "首家", "顶级",
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

    def render(self, root, cfg_path, cfg_kind, scan_root=None):
        lines = []
        lines.append("# 小程序提审预检报告")
        lines.append("")
        lines.append(f"- 扫描目录：`{root}`")
        if scan_root is not None:
            lines.append(f"- 实际代码目录：`{scan_root}`")
            lines.append(f"- 扫描文本文件：{sum(1 for _ in iter_files(scan_root, TEXT_EXT))} 个")
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
            lines.append("> 存在可确认的结构问题，修复 FAIL 项后重新检查。")
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
        lines.append("PASS 只表示本次静态检查未命中对应问题，不代表审核通过。")
        lines.append("WARN 是人工核查线索。动态调用、别名及后台配置不能由本脚本确认。")
        lines.append("规则与依据见包内 docs/ 或 references/ 对应文档。")
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
    """读取 JSON 及 JSONC（注释、尾逗号），始终保留字符串内容。不是完整 JSON5。"""
    text = text.lstrip("\ufeff")
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    chars = list(text)
    quoted = False
    i = 0
    while i < len(text):
        ch = text[i]
        if quoted:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif text.startswith("//", i) or text.startswith("/*", i):
            line_comment = text.startswith("//", i)
            end = text.find("\n", i + 2) if line_comment else text.find("*/", i + 2)
            if end < 0:
                if not line_comment:
                    return None
                end = len(text)
            elif not line_comment:
                end += 2
            for j in range(i, end):
                if chars[j] not in "\r\n":
                    chars[j] = " "
            i = end
            continue
        i += 1
    s = "".join(chars)
    quoted = False
    i = 0
    while i < len(s):
        if quoted:
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == '"':
                quoted = False
        elif s[i] == '"':
            quoted = True
        elif s[i] == ",":
            end = i + 1
            while end < len(s) and s[end].isspace():
                end += 1
            if end < len(s) and s[end] in "}]":
                chars[i] = " "
        i += 1
    try:
        return json.loads("".join(chars))
    except (ValueError, TypeError):
        return None


def javascript_code(text):
    """屏蔽注释、字符串及正则字面量；模板的 ${...} 仍作为代码扫描。

    这是词法预处理，不做符号绑定和完整 JS/TS 语法分析。
    """
    chars = list(text)
    strings = []

    def blank(start, end):
        for j in range(start, min(end, len(chars))):
            if chars[j] not in "\r\n":
                chars[j] = " "

    def scan(i, in_expression=False):
        depth = 0
        regex_allowed = True
        while i < len(text):
            ch = text[i]
            if text.startswith("//", i):
                end = text.find("\n", i + 2)
                end = len(text) if end < 0 else end
                blank(i, end)
                i = end
                continue
            if text.startswith("/*", i):
                end = text.find("*/", i + 2)
                end = len(text) if end < 0 else end + 2
                blank(i, end)
                i = end
                continue
            if ch in "\"'":
                start = i
                quote = ch
                i += 1
                while i < len(text):
                    if text[i] == "\\":
                        i += 2
                    elif text[i] == quote:
                        i += 1
                        break
                    else:
                        i += 1
                strings.append((start, i, text[start + 1:i - 1]))
                blank(start, i)
                regex_allowed = False
                continue
            if ch == "`":
                blank(i, i + 1)
                i += 1
                while i < len(text):
                    if text[i] == "\\":
                        blank(i, min(i + 2, len(text)))
                        i += 2
                    elif text[i] == "`":
                        blank(i, i + 1)
                        i += 1
                        break
                    elif text.startswith("${", i):
                        blank(i, i + 2)
                        i = scan(i + 2, True)
                    else:
                        blank(i, i + 1)
                        i += 1
                regex_allowed = False
                continue
            if ch == "/" and regex_allowed:
                start = i
                i += 1
                in_class = False
                while i < len(text) and text[i] not in "\r\n":
                    if text[i] == "\\":
                        i += 2
                        continue
                    if text[i] == "[":
                        in_class = True
                    elif text[i] == "]":
                        in_class = False
                    elif text[i] == "/" and not in_class:
                        i += 1
                        while i < len(text) and text[i].isalpha():
                            i += 1
                        break
                    i += 1
                blank(start, i)
                regex_allowed = False
                continue
            if ch.isalpha() or ch in "_$":
                end = i + 1
                while end < len(text) and (text[end].isalnum() or text[end] in "_$"):
                    end += 1
                regex_allowed = text[i:end] in {"return", "throw", "case", "yield", "await", "typeof", "void", "delete", "new"}
                i = end
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                if in_expression and depth == 0:
                    blank(i, i + 1)
                    return i + 1
                depth -= 1
            if not ch.isspace():
                regex_allowed = ch in "([{=,:;!?&|+-*%^~<>/"
            i += 1
        return i

    scan(0)
    return "".join(chars), strings


def source_parts(path):
    """分开脚本与模板；HTML 注释不会进入模板扫描。"""
    text = read_text(path)
    if path.suffix.lower() in {".vue", ".uvue", ".wxml"}:
        scripts = []
        def take_script(match):
            scripts.append(match.group(1))
            return "\n"
        markup = re.sub(r"<!--.*?-->|<script\b[^>]*>(.*?)</script\s*>",
                        lambda m: take_script(m) if m.group(1) is not None else "",
                        text, flags=re.S | re.I)
        return scripts, markup
    return [text], ""


def api_names(path, apis):
    scripts, markup = source_parts(path)
    found = set()
    for script in scripts:
        code, strings = javascript_code(script)
        for api in apis:
            if re.search(r"\b(?:wx|uni)\s*(?:\?\.|\.)\s*" + re.escape(api) + r"\s*(?:\?\.)?\s*\(", code):
                found.add(api)
            if api in {"onShareAppMessage", "onShareTimeline"} and re.search(r"\b" + api + r"\s*(?:\(|:)", code):
                found.add(api)
        for start, end, value in strings:
            if value in apis and re.search(r"\b(?:wx|uni)\s*\[\s*$", code[:start]) and re.match(r"\s*\]\s*\(", code[end:]):
                found.add(value)
    for api in apis:
        if re.search(r"\bopen-type\s*=\s*['\"]" + re.escape(api) + r"['\"]", markup):
            found.add(api)
    if "createCameraContext" in apis and re.search(r"<camera\b", markup):
        found.add("createCameraContext")
    return found


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        self.parts.extend(value for key, value in attrs if key in {"title", "placeholder", "alt", "text"} and value)


def visible_text(path):
    scripts, markup = source_parts(path)
    parser = VisibleText()
    parser.feed(re.sub(r"{{.*?}}", "", markup, flags=re.S))
    return "\n".join(parser.parts)


def find_config(root):
    """找项目配置。返回 (path, kind)，kind 为 'app.json' 或 'pages.json'（uni-app）。

    优先直接项目或源码，其次编译产物。后续扫描以选中配置的目录为根。
    """
    app_candidates = [
        root / "app.json",
        root / "src" / "app.json",
        root / "dist" / "build" / "mp-weixin" / "app.json",
        root / "unpackage" / "dist" / "build" / "mp-weixin" / "app.json",
        root / "miniprogram" / "app.json",
    ]
    source_candidates = [(root / "app.json", "app.json"),
                         (root / "src" / "app.json", "app.json"),
                         (root / "pages.json", "pages.json"),
                         (root / "src" / "pages.json", "pages.json")]
    for c, kind in source_candidates:
        if c.is_file():
            return c, kind

    for c in app_candidates:
        if c.is_file():
            return c, "app.json"

    # 兜底：任意深度找，取路径最短的
    for name, kind in (("app.json", "app.json"), ("pages.json", "pages.json")):
        found = [p for p in root.rglob(name) if not any(s in p.parts for s in SKIP_DIRS)]
        if found:
            found.sort(key=lambda p: (len(p.parts), str(p)))
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


# ---------------------------------------------------------------- 各项检查

def check_name(rep, cfg_data, cfg_kind, root):
    """名称：注册名（manifest）+ 各页标题文字里的营销词 / 通用词"""
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
            text = str(text)
            hits_m = [w for w in MARKETING_WORDS if w in text]
            hits_g = [w for w in GENERIC_WORDS if w in text]
            if hits_m:
                rep.add("名称", "WARN", f"标题命中待核查用语：{text}",
                        f"位置 {where}，命中 {hits_m}",
                        "核对是否为商业宣传或与功能无关的表述；页面标题与后台注册名称分别判断，词表命中不等于违规")
            elif hits_g:
                rep.add("名称", "WARN", f"标题含通用词：{text}",
                        f"位置 {where}，命中 {hits_g}",
                        "页面标题与后台注册名称分别核查；若注册名也使用这些词，按官方 1.2.1-3 结合完整名称判断")
            else:
                rep.add("名称", "PASS", f"标题无明显风险词：{text}", f"位置 {where}")

    # 本地应用名不等同于后台注册名，只作为人工核查线索。
    app_name = read_manifest_name(root)
    if app_name:
        hits_m = [w for w in MARKETING_WORDS if w in app_name]
        hits_g = [w for w in GENERIC_WORDS if w in app_name]
        if hits_m:
            rep.add("名称", "WARN", f"本地应用名命中待核查用语：{app_name}",
                    f"来自 manifest.json，命中 {hits_m}",
                    "先核对后台实际注册名称，再按官方 1.1-2 判断；本地应用名不等同于注册名")
        elif hits_g:
            rep.add("名称", "WARN", f"应用名含通用词：{app_name}",
                    f"来自 manifest.json，命中 {hits_g}",
                    "先核对后台实际注册名；通用词组合的识别性需按官方 1.2.1-3 人工判断")
        else:
            rep.add("名称", "PASS", f"应用名无明显风险词：{app_name}", "来自 manifest.json")


def check_privacy(rep, root):
    """只报告可识别的接口或组件线索，后台声明与用户授权另行确认。"""
    hits = {}
    for p in iter_files(root, CODE_EXT):
        for api in api_names(p, PRIVACY_APIS):
            hits.setdefault(api, []).append(str(p.relative_to(root)))
    if not hits:
        rep.add("隐私", "PASS", "本次扫描未检出已知隐私接口或组件",
                "已排除注释和普通字符串；动态调用、别名及自建表单收集不在此结论内")
    for api, files in sorted(hits.items()):
        rep.add("隐私", "WARN", f"检出 {api}（{PRIVACY_APIS[api]}）",
                "出现在：" + "、".join(files[:3]),
                "核对对应版本的信息类型声明、生效状态及用户同意同步；拒绝授权时应正常降级")
    rep.add("隐私", "WARN", "隐私声明与用户授权需人工确认",
            "脚本无法读取后台，也不能验证用户已同意",
            "先确认接口对应的信息类型已声明，再确认对应版本生效及用户授权。"
            "errno 112 应先补齐缺失声明再等待生效；2023-10-17 后无需靠 __usePrivacyCheck__ 启用隐私功能。"
            "详见隐私文档")


def check_content_safety(rep, root):
    """内容接口只能提供核查线索，不能直接证明违反 5.18。"""
    found = {}
    for p in iter_files(root, CODE_EXT):
        for api in api_names(p, CONTENT_APIS):
            found.setdefault(api, []).append(str(p.relative_to(root)))
    if not found:
        rep.add("内容安全", "PASS", "本次扫描未检出已知内容生成、保存或分享接口",
                "不等于内容安全豁免；动态调用、别名和服务端功能需另查")
    for api, files in sorted(found.items()):
        rep.add("内容安全", "WARN", f"检出 {api}（{CONTENT_APIS[api]}）",
                "出现在：" + "、".join(files[:3]),
                "确认处理对象是否为用户内容、内容去向以及现有检测机制；接口名称本身不构成违规结论")
    generated = [a for a in found if a == "canvasToTempFilePath"]
    saved = [a for a in found if a in {"saveImageToPhotosAlbum", "saveVideoToPhotosAlbum"}]
    shared = [a for a in found if a in {"onShareAppMessage", "onShareTimeline", "shareAppMessage"}]
    if generated or saved or shared:
        rep.add("内容安全", "WARN", "内容处理路径需人工核查",
                f"本地生成 {generated}；相册保存 {saved}；分享 {shared}",
                "不要把临时文件、相册保存与传播视为同一件事。按实际数据流和官方 5.18 核查过滤机制")
    social_hits = []
    for p in iter_files(root, CODE_EXT):
        text = visible_text(p)
        social_hits.extend((w, str(p.relative_to(root))) for w in SOCIAL_WORDS if w in text)
    if social_hits:
        rep.add("内容安全", "WARN", f"界面文本出现社交相关用语：{sorted({w for w, _ in social_hits})}",
                "示例位置：" + "、".join(sorted({f for _, f in social_hits})[:3]),
                "核对实际功能与已选类目的边界；用语命中不等于已经具有公开传播功能")


def check_unfinished(rep, root):
    """只检查模板可见文本候选，不从注释或变量名推断产品未完成。"""
    hits = []
    for p in iter_files(root, CODE_EXT):
        text = visible_text(p)
        for word in UNFINISHED_WORDS:
            matched = re.search(r"\bbeta\b", text, re.I) if word.lower() == "beta" else word in text
            if matched:
                hits.append((word, str(p.relative_to(root))))
    if hits:
        rep.add("提审节奏", "WARN", f"界面文本命中待核查文案：{sorted({w for w, _ in hits})}",
                "示例位置：" + "、".join(sorted({f for _, f in hits})[:3]),
                "人工确认是否为未开放功能或测试提示；词表不能替代完成品验收（官方 3.3-1）")
    else:
        rep.add("提审节奏", "PASS", "模板文本未检出待核查的未完成提示",
                "不扫描注释和内部变量；运行时生成文案仍需实际检查")
    has_login = any("login" in api_names(p, {"login": "登录"}) for p in iter_files(root, CODE_EXT))
    if has_login:
        rep.add("提审节奏", "WARN", "检出 wx.login / uni.login 调用线索",
                "登录调用本身不证明存在账号密码体系",
                "若审核需要登录体验，按实际账号体系提供可用测试方式（官方 3.3-4）")
    else:
        rep.add("提审节奏", "PASS", "本次扫描未检出直接登录调用",
                "别名、动态调用及其他登录方式需人工核查")


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
    """无法静态扫描的项，给提醒"""
    rep.add("类目", "WARN", "类目无法静态扫描，需人工确认",
            "脚本读不到后台配置",
            "① 核心功能能在个人开放类目清单里找到一行 ② 每个类目首页 2 次点击内可达 "
            "③ 已填类目 ≤ 5 ④ 无传播/分享/公开访问功能。详见 docs/02-categories.md")
    rep.add("备案", "WARN", "备案状态无法静态扫描，需人工确认",
            "脚本读不到备案后台",
            "① 备注无禁词（电商/付费/广告/商用/卖家）② 地址精确到门牌号且与身份证一致 "
            "③ 备注写的功能 = 实际有的功能。详见 docs/03-icp-filing.md")
    rep.add("名称", "WARN", "注册名称无法静态扫描，需人工确认",
            "脚本只能扫代码里的标题，读不到后台注册名称",
            "① 名字里无营销词、无通用品类词 ② 是「造词/品牌词+功能词」结构 "
            "③ 后台「还能修改 N 次」已截图且 N≥1。详见 docs/01-naming.md")


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="小程序提审静态预检：风险线索不等于审核结论")
    ap.add_argument("root", help="小程序项目根目录")
    ap.add_argument("--md", help="同时把报告写到这个 .md 文件")
    args = ap.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"[错误] 目录不存在：{root}", file=sys.stderr)
        return 2
    cfg_path, cfg_kind = find_config(root)
    if cfg_path is None:
        print("[错误] 找不到 app.json / pages.json，未执行检查", file=sys.stderr)
        return 2
    cfg_data = loads_lenient(read_text(cfg_path))
    if not isinstance(cfg_data, dict):
        print(f"[错误] 配置解析失败或顶层不是对象：{cfg_path}，未执行检查", file=sys.stderr)
        return 2
    if "pages" in cfg_data and not isinstance(cfg_data["pages"], list):
        print(f"[错误] pages 必须是数组：{cfg_path}，未执行检查", file=sys.stderr)
        return 2
    if cfg_kind == "app.json" and any(not isinstance(p, str) or not p.strip() for p in cfg_data.get("pages", [])):
        print(f"[错误] app.json 的 pages 项必须是非空路径字符串：{cfg_path}", file=sys.stderr)
        return 2
    scan_root = cfg_path.parent
    rep = Report()
    if cfg_kind == "app.json" and not cfg_data.get("pages"):
        rep.add("提审节奏", "FAIL", "app.json 缺少非空 pages 入口列表",
                "原生小程序需要在 pages 中声明入口页面", "按实际项目配置入口页面后重新运行")
    check_name(rep, cfg_data, cfg_kind, root)
    check_gate_reminders(rep)
    check_privacy(rep, scan_root)
    check_content_safety(rep, scan_root)
    check_unfinished(rep, scan_root)
    check_size(rep, scan_root, cfg_data)
    out = rep.render(root, cfg_path, cfg_kind, scan_root)
    print(out)
    if args.md:
        try:
            Path(args.md).write_text(out, encoding="utf-8")
        except OSError as exc:
            print(f"[错误] 报告写入失败：{exc}", file=sys.stderr)
            return 2
        print(f"[已写入] {args.md}", file=sys.stderr)
    return 1 if rep.count("FAIL") else 0


if __name__ == "__main__":
    sys.exit(main())
