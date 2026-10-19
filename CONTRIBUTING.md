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
- 发版流程：合并代码到 main → `git tag vX.Y.Z` → `git push origin vX.Y.Z`
- GitHub Actions 会自动：语法检查 → 打 zip → 创建 Release 并附上 zip 与 sha256

## 其他约定

- 插件本体位于 `plugins/juya-daily/`，勿在其内提交 `__pycache__`、`*.pyc`、`state.json`、`*.corrupt`
- 配置示例改动需同步更新 README 的「配置」一节与 `config.toml`
- 改动 plugin.py 的命令或配置字段时，同步检查 `_manifest.json` 的 `capabilities`
