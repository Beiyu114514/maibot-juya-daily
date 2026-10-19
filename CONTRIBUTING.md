# 提交规范（Conventional Commits）

格式：`<type>(<scope>?): <subject>`

常用 type：

| type | 用途 |
| --- | --- |
| feat | 新功能（ minor 版本） |
| fix | 修复 bug（ patch 版本） |
| docs | 文档变更 |
| ci | CI/CD 配置 |
| refactor | 重构（不改行为） |
| chore | 杂项（依赖、构建等） |

示例：

```
feat: 支持 push.max_items 限制每期条数
fix(scheduler): push_time 配置非法时回退 09:30
docs: README 补充配置说明
ci: 打包 workflow 增加 sha256 校验文件
```

## 版本与发布

- 版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)：`主.次.修订`
- 发版流程：改 `plugins/juya-daily/_manifest.json` 的 `version` → 合并到 main → `git tag vX.Y.Z` → `git push origin vX.Y.Z`
- GitHub Actions 会自动：语法检查 → 打 zip → 创建 Release 并附上 zip 与 sha256
- 本地打包用 `python3 scripts/package_plugin.py`（产出 `dist/juya-daily-X.Y.Z.zip` 与 `.sha256`）

## 安装包结构（别踩的坑）

MaiBot WebUI 的「从 ZIP 安装插件」只认两种布局：`_manifest.json` + `plugin.py` 在 ZIP 根目录，或放在**唯一一层**插件目录里。打成 `plugins/juya-daily/…` 会报「ZIP 根目录或唯一一层插件目录中必须包含 _manifest.json」（0.3.0 的线上包就是这么坏掉的）。

- 打包一律走 `scripts/package_plugin.py`，不要手写 `zip -r ... plugins/juya-daily`
- `tests/test_packaging.py` 与 CI 的 package job 都会复核结构，回归会直接红
- 包内不能有 `__pycache__`、`config.toml`、`state.json`、`.git`、符号链接、`../` 路径，压缩包 ≤ 100 MB、解压 ≤ 300 MB、文件数 ≤ 10000

## 其他约定

- 插件本体位于 `plugins/juya-daily/`，勿在其内提交 `__pycache__`、`*.pyc`、`state.json`、`*.corrupt`
- 配置示例改动需同步更新 README 的「配置」一节与 `config.toml`
- 改动 plugin.py 的命令或配置字段时，同步检查 `_manifest.json` 的 `capabilities`
