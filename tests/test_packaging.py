"""安装包结构测试：MaiBot WebUI 的 ZIP 安装只认特定布局。

v0.3.0 的 Release 包用 `zip -r ... plugins/juya-daily` 打成
`plugins/juya-daily/...`，多套了一层，导入时报
「ZIP 根目录或唯一一层插件目录中必须包含 _manifest.json」。
这里把打包器的构建与校验规则都钉住：

- 打出来的包必须是「ZIP 内唯一一层 juya-daily/」，且含 _manifest.json + plugin.py；
- 旧的 plugins/ 前缀双层结构必须被拒绝（并给出可读的错误）；
- 混入第二个目录、缺 plugin.py、`../` 越界、符号链接、加密条目都要拒绝；
- 同样内容重复打包 100% 相同 sha256（固定时间戳，便于校验下载包）。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "package_plugin.py")
PLUGIN_DIR = os.path.join(REPO_ROOT, "plugins", "juya-daily")


def _load_packager():
    spec = importlib.util.spec_from_file_location("juya_package_plugin", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["juya_package_plugin"] = module
    spec.loader.exec_module(module)
    return module


PACKAGER = _load_packager()
PackageError = PACKAGER.PackageError

MANIFEST = {
    "manifest_version": 2,
    "id": "com.juya.daily",
    "version": "0.3.1",
    "name": "橘鸦AI早报",
}


def _manifest_bytes(**overrides) -> bytes:
    payload = dict(MANIFEST)
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _write_zip(path: str, entries: dict[str, bytes], *, symlinks=()) -> str:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        for name in symlinks:
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o120777 & 0xFFFF) << 16
            zf.writestr(info, b"")
    return path


def _quiet_main(argv):
    """跑打包器 CLI，吞掉正常输出，只返回退出码（保持测试日志干净）。"""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO()
    ):
        return PACKAGER.main(argv)


class _TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="juya-pkg-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def path(self, name: str) -> str:
        return os.path.join(self.tmp, name)


class BuildTest(_TempDirTest):
    def test_builds_single_plugin_dir_layout(self) -> None:
        out = os.path.join(self.tmp, "out", "juya-daily-0.3.1.zip")
        entries = PACKAGER.build_zip(PACKAGER.Path(out))

        self.assertTrue(os.path.isfile(out))
        self.assertEqual(entries[0], "juya-daily/")
        self.assertIn("juya-daily/_manifest.json", entries)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        top_dirs = {name.split("/", 1)[0] for name in names if "/" in name}
        self.assertEqual(top_dirs, {"juya-daily"})
        self.assertIn("juya-daily/_manifest.json", names)
        self.assertIn("juya-daily/plugin.py", names)
        # 不能出现旧包里的 plugins/ 前缀，也不能带本地运行产物
        self.assertNotIn("plugins", top_dirs)
        for name in names:
            self.assertNotIn("__pycache__", name)
            self.assertFalse(name.endswith(".pyc"))
            self.assertNotEqual(name, "juya-daily/config.toml")

    def test_build_names_version_from_manifest(self) -> None:
        out = os.path.join(self.tmp, "x.zip")
        PACKAGER.build_zip(PACKAGER.Path(out))
        manifest = PACKAGER.read_manifest()
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")

        self.assertEqual(_quiet_main(["--out-dir", self.tmp, "--print-name"]), 0)
        expected = os.path.join(self.tmp, f"juya-daily-{manifest['version']}.zip")
        self.assertTrue(os.path.isfile(expected))
        self.assertTrue(os.path.isfile(expected + ".sha256"))

    def test_build_is_reproducible(self) -> None:
        first = PACKAGER.Path(self.tmp, "a", "juya-daily-0.3.1.zip")
        second = PACKAGER.Path(self.tmp, "b", "juya-daily-0.3.1.zip")
        PACKAGER.build_zip(first)
        PACKAGER.build_zip(second)
        self.assertEqual(PACKAGER.sha256_file(first), PACKAGER.sha256_file(second))

    def test_checksum_file_is_sha256sum_compatible(self) -> None:
        out = PACKAGER.Path(self.tmp, "juya-daily-0.3.1.zip")
        PACKAGER.build_zip(out)
        checksum = PACKAGER.write_checksum(out)
        line = checksum.read_text(encoding="utf-8").strip()
        digest, filename = line.split("  ")
        self.assertEqual(filename, out.name)
        self.assertEqual(digest, hashlib.sha256(out.read_bytes()).hexdigest())

    def test_build_excludes_runtime_leftovers(self) -> None:
        src = self.path("src")
        os.makedirs(os.path.join(src, "__pycache__"))
        for name, data in (
            ("_manifest.json", _manifest_bytes()),
            ("plugin.py", b"# entry\n"),
            ("config.toml", b"[x]\n"),
            ("state.json", b"{}"),
            ("notes.pyc", b"x"),
            ("README.md", b"readme"),
        ):
            with open(os.path.join(src, name), "wb") as fh:
                fh.write(data)
        with open(os.path.join(src, "__pycache__", "plugin.cpython-312.pyc"), "wb") as fh:
            fh.write(b"x")

        out = PACKAGER.Path(self.tmp, "juya-daily-0.3.1.zip")
        PACKAGER.build_zip(out, src=PACKAGER.Path(src))
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        self.assertIn("juya-daily/plugin.py", names)
        self.assertIn("juya-daily/README.md", names)
        self.assertNotIn("juya-daily/config.toml", names)
        self.assertNotIn("juya-daily/state.json", names)
        self.assertNotIn("juya-daily/notes.pyc", names)
        self.assertFalse([n for n in names if "__pycache__" in n])


class ValidateTest(_TempDirTest):
    def test_accepts_built_zip(self) -> None:
        out = PACKAGER.Path(self.tmp, "ok.zip")
        PACKAGER.build_zip(out)
        manifest = PACKAGER.validate_zip(out, expected_id=MANIFEST["id"])
        self.assertEqual(manifest["id"], MANIFEST["id"])

    def test_accepts_root_flat_layout(self) -> None:
        """规则允许 _manifest.json 直接放在 ZIP 根目录。"""
        path = _write_zip(
            self.path("root.zip"),
            {"_manifest.json": _manifest_bytes(), "plugin.py": b"# entry\n"},
        )
        self.assertEqual(PACKAGER.validate_zip(PACKAGER.Path(path))["id"], MANIFEST["id"])

    def test_rejects_legacy_plugins_prefix_layout(self) -> None:
        """v0.3.0 的包：plugins/juya-daily/... ，多套了一层。"""
        path = _write_zip(
            self.path("legacy.zip"),
            {
                "plugins/juya-daily/_manifest.json": _manifest_bytes(),
                "plugins/juya-daily/plugin.py": b"# entry\n",
            },
        )
        with self.assertRaises(PackageError) as ctx:
            PACKAGER.validate_zip(PACKAGER.Path(path))
        self.assertIn("_manifest.json", str(ctx.exception))

    def test_rejects_mixed_root_and_subdir(self) -> None:
        path = _write_zip(
            self.path("mixed.zip"),
            {
                "_manifest.json": _manifest_bytes(),
                "plugin.py": b"# entry\n",
                "juya-daily/plugin.py": b"# entry\n",
            },
        )
        with self.assertRaises(PackageError) as ctx:
            PACKAGER.validate_zip(PACKAGER.Path(path))
        self.assertIn("一个包只能装一个插件", str(ctx.exception))

    def test_rejects_two_plugin_dirs(self) -> None:
        path = _write_zip(
            self.path("two.zip"),
            {
                "a/_manifest.json": _manifest_bytes(),
                "a/plugin.py": b"# entry\n",
                "b/plugin.py": b"# entry\n",
            },
        )
        with self.assertRaises(PackageError) as ctx:
            PACKAGER.validate_zip(PACKAGER.Path(path))
        self.assertIn("多个顶层目录", str(ctx.exception))

    def test_rejects_missing_plugin_entry(self) -> None:
        path = _write_zip(
            self.path("noentry.zip"),
            {"juya-daily/_manifest.json": _manifest_bytes()},
        )
        with self.assertRaises(PackageError) as ctx:
            PACKAGER.validate_zip(PACKAGER.Path(path))
        self.assertIn("plugin.py", str(ctx.exception))

    def test_rejects_path_traversal(self) -> None:
        path = _write_zip(
            self.path("traversal.zip"),
            {
                "juya-daily/_manifest.json": _manifest_bytes(),
                "juya-daily/plugin.py": b"# entry\n",
                "../evil.py": b"# nope\n",
            },
        )
        with self.assertRaises(PackageError) as ctx:
            PACKAGER.validate_zip(PACKAGER.Path(path))
        self.assertIn("越界路径", str(ctx.exception))

    def test_rejects_symlink_entry(self) -> None:
        path = _write_zip(
            self.path("symlink.zip"),
            {
                "juya-daily/_manifest.json": _manifest_bytes(),
                "juya-daily/plugin.py": b"# entry\n",
            },
            symlinks=("juya-daily/link.py",),
        )
        with self.assertRaises(PackageError) as ctx:
            PACKAGER.validate_zip(PACKAGER.Path(path))
        self.assertIn("符号链接", str(ctx.exception))

    def test_rejects_manifest_version_1(self) -> None:
        path = _write_zip(
            self.path("v1.zip"),
            {
                "juya-daily/_manifest.json": _manifest_bytes(manifest_version=1),
                "juya-daily/plugin.py": b"# entry\n",
            },
        )
        with self.assertRaises(PackageError) as ctx:
            PACKAGER.validate_zip(PACKAGER.Path(path))
        self.assertIn("manifest_version", str(ctx.exception))

    def test_rejects_version_not_semver(self) -> None:
        path = _write_zip(
            self.path("badver.zip"),
            {
                "juya-daily/_manifest.json": _manifest_bytes(version="0.3"),
                "juya-daily/plugin.py": b"# entry\n",
            },
        )
        with self.assertRaises(PackageError) as ctx:
            PACKAGER.validate_zip(PACKAGER.Path(path))
        self.assertIn("X.Y.Z", str(ctx.exception))

    def test_rejects_bad_id(self) -> None:
        path = _write_zip(
            self.path("badid.zip"),
            {
                "juya-daily/_manifest.json": _manifest_bytes(id="JuyaDaily"),
                "juya-daily/plugin.py": b"# entry\n",
            },
        )
        with self.assertRaises(PackageError) as ctx:
            PACKAGER.validate_zip(PACKAGER.Path(path))
        self.assertIn("id", str(ctx.exception))

    def test_rejects_corrupt_zip(self) -> None:
        path = self.path("corrupt.zip")
        with open(path, "wb") as fh:
            fh.write(b"not a zip at all")
        with self.assertRaises(PackageError) as ctx:
            PACKAGER.validate_zip(PACKAGER.Path(path))
        self.assertIn("不是合法的 ZIP", str(ctx.exception))


class CliTest(_TempDirTest):
    def test_cli_build_and_verify(self) -> None:
        self.assertEqual(_quiet_main(["--out-dir", self.tmp]), 0)
        manifest = PACKAGER.read_manifest()
        built = os.path.join(self.tmp, f"juya-daily-{manifest['version']}.zip")
        self.assertTrue(os.path.isfile(built))
        self.assertEqual(_quiet_main(["--verify", built]), 0)

    def test_cli_verify_reports_legacy_layout(self) -> None:
        path = _write_zip(
            self.path("legacy.zip"),
            {
                "plugins/juya-daily/_manifest.json": _manifest_bytes(),
                "plugins/juya-daily/plugin.py": b"# entry\n",
            },
        )
        # 命令行用退出码 1 表达「结构不合规」，CI 里靠它拦截回归
        self.assertEqual(_quiet_main(["--verify", path]), 1)


class RealPluginTest(unittest.TestCase):
    """真实插件目录必须能直接打包（防止出现符号链接、空目录等打包不了的情况）。"""

    def test_real_plugin_dir_packages_and_validates(self) -> None:
        manifest = PACKAGER.read_manifest()
        with tempfile.TemporaryDirectory(prefix="juya-real-") as tmp:
            out = PACKAGER.Path(tmp, f"juya-daily-{manifest['version']}.zip")
            PACKAGER.build_zip(out)
            self.assertEqual(
                PACKAGER.validate_zip(out, expected_id=manifest["id"])["id"],
                manifest["id"],
            )
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
        self.assertTrue(os.path.isdir(PLUGIN_DIR))
        self.assertIn("juya-daily/_manifest.json", names)
        self.assertIn("juya-daily/plugin.py", names)
        self.assertIn("juya-daily/config.example.toml", names)


if __name__ == "__main__":
    unittest.main()
