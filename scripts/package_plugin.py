#!/usr/bin/env python3
"""构建 / 校验可被 MaiBot WebUI「从 ZIP 安装插件」接受的安装包。

MaiBot 对安装包的结构要求（见 https://docs.mai-mai.org/manual/plugins/management）：

* ``_manifest.json`` 与 ``plugin.py`` 位于 ZIP 根目录，**或**位于唯一的一层插件目录里；
* 一个包只能装一个插件，混入外层目录、第二个插件目录、``.git``、``__MACOSX``
  或 ``../`` 路径都会被拒绝；
* 包 ≤ 100 MB，解压后 ≤ 300 MB，文件数 ≤ 10000，不能加密、不能有符号链接。

v0.3.0 的 Release 包用 ``zip -r ... plugins/juya-daily`` 打出 ``plugins/juya-daily/...``，
多套了 ``plugins/`` 一层，于是导入时报
「ZIP 根目录或唯一一层插件目录中必须包含 _manifest.json」。
本脚本固定打成 ``juya-daily/...``：解压即得 ``plugins/juya-daily/`` 的内容，
目录名与 README 的手工安装方式一致。

用法::

    python3 scripts/package_plugin.py                # 输出到 dist/juya-daily-<ver>.zip
    python3 scripts/package_plugin.py --out-dir .    # 输出到当前目录（CI 用）
    python3 scripts/package_plugin.py --verify foo.zip   # 只校验一个已有 zip

退出码：0 成功，1 结构不合规 / 构建失败。不使用 ``zip`` 命令，纯标准库，
本地与 CI（ubuntu-latest）行为一致。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_SRC = REPO_ROOT / "plugins" / "juya-daily"
# ZIP 里唯一的顶层目录名，必须与 plugins/ 下的插件目录名一致
PLUGIN_DIR_IN_ZIP = "juya-daily"

MANIFEST_NAME = "_manifest.json"
ENTRY_MODULE = "plugin.py"

# 被打包时必须跳过的目录（任意层级），以及必须跳过的文件名 / 后缀
EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".github",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "data",
    "node_modules",
}
EXCLUDE_NAMES = {
    "config.toml",  # 运行时配置，Host 会按 config_model 重新生成
    "config_back",
    "state.json",
    "state.json.tmp",
    ".DS_Store",
    "Thumbs.db",
}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".log", ".corrupt", ".future", ".bak", ".swp")

MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 300 * 1024 * 1024
MAX_ENTRIES = 10000

# 固定时间戳 / 权限 → 同样内容打出的 zip 逐字节一致，便于比对 sha256
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644
DIR_MODE = 0o755

PLUGIN_ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class PackageError(Exception):
    """包结构不合规（会被 MaiBot 拒绝）。"""


# ---------------------------------------------------------------- 构建


def read_manifest(src: Path = PLUGIN_SRC) -> dict:
    """读插件源码目录里的 _manifest.json。"""
    path = src / MANIFEST_NAME
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except FileNotFoundError:
        raise PackageError(f"缺少 {path}") from None
    except ValueError as exc:
        raise PackageError(f"{path} 不是合法 JSON：{exc}") from None
    if not isinstance(manifest, dict):
        raise PackageError(f"{path} 顶层必须是对象")
    return manifest


def plugin_version(manifest: dict, override: str | None = None) -> str:
    version = override or str(manifest.get("version") or "")
    if not VERSION_RE.match(version):
        raise PackageError(f"版本号必须是 X.Y.Z：{version!r}")
    return version


def iter_plugin_files(src: Path = PLUGIN_SRC) -> list[Path]:
    """列出要打进包的文件（相对 src，已排序、已过滤）。"""
    if not src.is_dir():
        raise PackageError(f"插件源码目录不存在：{src}")
    picked: list[Path] = []
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if path.is_dir():
            continue  # 目录项由 build_zip 统一生成
        if path.is_symlink():
            # MaiBot 拒绝符号链接，直接在源码侧报出来
            raise PackageError(f"插件目录里有符号链接，不能打包：{rel}")
        if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]):
            continue
        if rel.name in EXCLUDE_NAMES or rel.name.endswith(EXCLUDE_SUFFIXES):
            continue
        picked.append(rel)
    picked.sort(key=lambda p: p.as_posix())
    if not picked:
        raise PackageError(f"{src} 下没有可打包的文件")
    return picked


def _zipinfo(arcname: str, mode: int, is_dir: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE)
    info.create_system = 3  # unix，配合 external_attr 表达权限
    info.external_attr = (mode & 0xFFFF) << 16
    if is_dir:
        info.external_attr |= 0x10  # MS-DOS 目录位
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def build_zip(
    out_path: Path,
    src: Path = PLUGIN_SRC,
    dir_name: str = PLUGIN_DIR_IN_ZIP,
) -> list[str]:
    """把插件源码打成 ``<dir_name>/...`` 结构的 zip，返回条目名列表。"""
    files = iter_plugin_files(src)
    if not (src / MANIFEST_NAME).is_file() or not (src / ENTRY_MODULE).is_file():
        raise PackageError(f"{src} 缺少 {MANIFEST_NAME} 或 {ENTRY_MODULE}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    entries: list[str] = []
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # 顶层插件目录项（保持原有的空目录语义，解压后目录名确定）
        zf.writestr(_zipinfo(f"{dir_name}/", DIR_MODE, True), b"")
        entries.append(f"{dir_name}/")
        for rel in files:
            arcname = f"{dir_name}/{rel.as_posix()}"
            zf.writestr(_zipinfo(arcname, FILE_MODE, False), (src / rel).read_bytes())
            entries.append(arcname)
    tmp_path.replace(out_path)
    return entries


# ---------------------------------------------------------------- 校验


def _check_entry_names(names: list[str]) -> None:
    problems: list[str] = []
    for name in names:
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            problems.append(f"绝对路径：{name}")
        if "\\" in name:
            problems.append(f"含反斜杠路径：{name}")
        parts = name.split("/")
        if any(part == ".." for part in parts):
            problems.append(f"越界路径：{name}")
        if any(part in {".git", "__MACOSX"} for part in parts):
            problems.append(f"禁止的目录：{name}")
    if problems:
        raise PackageError("包内路径不合规：\n  " + "\n  ".join(problems[:10]))


def _resolve_layout(file_names: list[str]) -> tuple[str, str]:
    """判定 ZIP 布局。

    返回 ``("root", "")`` 表示根目录布局，``("dir", "juya-daily")`` 表示
    唯一一层目录布局。不满足 MaiBot 规则时抛 PackageError。
    """
    top_dirs = {name.split("/", 1)[0] for name in file_names if "/" in name}
    root_files = {name for name in file_names if "/" not in name}

    if MANIFEST_NAME in root_files:
        if top_dirs:
            raise PackageError(
                "ZIP 根目录和子目录 "
                f"{sorted(top_dirs)} 里都有插件文件，一个包只能装一个插件"
            )
        return "root", ""

    if len(top_dirs) == 1 and not root_files:
        dir_name = next(iter(top_dirs))
        if f"{dir_name}/{MANIFEST_NAME}" not in file_names:
            raise PackageError(
                f"ZIP 唯一一层目录 {dir_name}/ 下缺少 {MANIFEST_NAME}（"
                "ZIP 根目录或唯一一层插件目录中必须包含 _manifest.json）"
            )
        return "dir", dir_name

    if not top_dirs and not root_files:
        raise PackageError("ZIP 是空的")
    if len(top_dirs) > 1:
        raise PackageError(
            "ZIP 里有多个顶层目录 "
            f"{sorted(top_dirs)}，一个包只能装一个插件；"
            "_manifest.json 必须位于 ZIP 根目录或唯一一层插件目录中"
        )
    raise PackageError(
        "ZIP 根目录或唯一一层插件目录中必须包含 _manifest.json"
        "（当前多套了一层目录，例如 plugins/juya-daily/）"
    )


def validate_zip(zip_path: Path, expected_id: str | None = None) -> dict:
    """按 MaiBot 的安装包规则校验一个 zip，返回包内的 manifest。"""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise PackageError(f"文件不存在：{zip_path}")
    size = zip_path.stat().st_size
    if size > MAX_ZIP_BYTES:
        raise PackageError(
            f"ZIP 文件超过 100 MB（{size / 1048576:.1f} MB），MaiBot 会拒绝"
        )

    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise PackageError(f"不是合法的 ZIP：{exc}") from None

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise PackageError(f"ZIP 内文件数 {len(infos)} 超过 10000")
        extracted = sum(info.file_size for info in infos)
        if extracted > MAX_EXTRACTED_BYTES:
            raise PackageError(
                f"ZIP 解压后超过 300 MB（{extracted / 1048576:.0f} MB），MaiBot 会拒绝"
            )
        for info in infos:
            if info.flag_bits & 0x1:
                raise PackageError(f"ZIP 内有加密条目：{info.filename}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise PackageError(f"ZIP 内有符号链接：{info.filename}")
        bad = zf.testzip()
        if bad is not None:
            raise PackageError(f"ZIP 条目损坏：{bad}")

        names = zf.namelist()
        file_names = [name for name in names if not name.endswith("/")]
        _check_entry_names(names)
        layout, base = _resolve_layout(file_names)
        prefix = f"{base}/" if base else ""

        if f"{prefix}{MANIFEST_NAME}" not in file_names:
            raise PackageError("ZIP 根目录或唯一一层插件目录中必须包含 _manifest.json")
        if f"{prefix}{ENTRY_MODULE}" not in file_names:
            raise PackageError(f"{prefix or '根目录'}{ENTRY_MODULE} 缺失（插件入口文件）")

        try:
            manifest = json.loads(zf.read(f"{prefix}{MANIFEST_NAME}").decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise PackageError(f"{MANIFEST_NAME} 不是 UTF-8 合法 JSON：{exc}") from None

    if not isinstance(manifest, dict):
        raise PackageError(f"{MANIFEST_NAME} 顶层必须是对象")
    if manifest.get("manifest_version") != 2:
        raise PackageError("manifest_version 必须是 2")
    plugin_id = str(manifest.get("id") or "")
    if not PLUGIN_ID_RE.match(plugin_id):
        raise PackageError(f"插件 id 格式不合规：{plugin_id!r}")
    if expected_id and plugin_id != expected_id:
        raise PackageError(f"包内插件 id 是 {plugin_id}，期望 {expected_id}")
    if not VERSION_RE.match(str(manifest.get("version") or "")):
        raise PackageError(f"manifest 版本号必须是 X.Y.Z：{manifest.get('version')!r}")
    return manifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksum(zip_path: Path) -> Path:
    """写 ``<zip>.sha256``（sha256sum 兼容格式，路径用 basename）。"""
    checksum_path = zip_path.with_name(zip_path.name + ".sha256")
    checksum_path.write_text(
        f"{sha256_file(zip_path)}  {zip_path.name}\n", encoding="utf-8"
    )
    return checksum_path


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="打包 / 校验 MaiBot 插件安装包（唯一一层插件目录结构）"
    )
    parser.add_argument("--src", default=str(PLUGIN_SRC), help="插件源码目录")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "dist"), help="输出目录")
    parser.add_argument("--version", default=None, help="覆盖 manifest 里的版本号")
    parser.add_argument(
        "--dir-name", default=PLUGIN_DIR_IN_ZIP, help="ZIP 内唯一的插件目录名"
    )
    parser.add_argument("--verify", metavar="ZIP", default=None, help="只校验已有 zip")
    parser.add_argument(
        "--print-name", action="store_true", help="只把 zip 文件名打到 stdout（CI 用）"
    )
    args = parser.parse_args(argv)

    try:
        if args.verify:
            manifest = validate_zip(Path(args.verify), expected_id=None)
            print(
                f"结构校验通过：{args.verify}"
                f"（{manifest['id']} v{manifest['version']}）"
            )
            return 0

        src = Path(args.src)
        manifest = read_manifest(src)
        version = plugin_version(manifest, args.version)
        out_path = Path(args.out_dir) / f"{args.dir_name}-{version}.zip"
        build_zip(out_path, src=src, dir_name=args.dir_name)
        validate_zip(out_path, expected_id=str(manifest.get("id") or "") or None)
        checksum_path = write_checksum(out_path)

        if args.print_name:
            print(out_path.name)
            return 0

        with zipfile.ZipFile(out_path) as zf:
            count = len([n for n in zf.namelist() if not n.endswith("/")])
        print(f"构建完成：{out_path}（{out_path.stat().st_size} 字节，{count} 个文件）")
        print(f"SHA256：{sha256_file(out_path)}  （已写入 {checksum_path.name}）")
        print(
            f"结构校验通过：ZIP 内唯一一层目录 {args.dir_name}/，"
            f"含 {MANIFEST_NAME} + {ENTRY_MODULE}，可直接在 MaiBot WebUI 导入"
        )
        return 0
    except PackageError as exc:
        print(f"拒绝：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
