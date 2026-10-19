## 改了什么

<!-- 简述本次变更内容，关联 Issue 请写 Fixes #N -->

## 变更类型

- [ ] feat 新功能
- [ ] fix 修复
- [ ] docs 文档
- [ ] ci 构建/CI
- [ ] refactor 重构
- [ ] chore 杂项

## 自查清单

- [ ] `python3 -m py_compile plugins/juya-daily/*.py` 通过
- [ ] 配置字段变更已同步 `config.toml`、`README.md` 与 WebUI label
- [ ] 命令/能力变更已核对 `_manifest.json` 的 `capabilities`
- [ ] 涉及推送/定时逻辑的改动已考虑：热重载可取消、状态落盘、失败重试
