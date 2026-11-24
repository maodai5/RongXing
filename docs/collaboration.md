# 团队协作与 Git 工作流

> 适用：绒星 RongXing（FastAPI 后端 + Expo 移动端）。一句话：**`main` 受保护 + 短命任务分支 + Squash PR + tag 发布**（管理员保留直推通道）。
> 维护：仓库管理员；文档索引见 [README.md](../README.md)。

## 1. 铁律

- 不建 `develop` 等长期分支；队员的 `main` 只收 PR，管理员可直推（仅限合并收尾与线上回滚）。
- 一个任务一条分支，1–3 天内合回并删除；两人不共用分支。
- 只允许 **Squash and merge**，合并后立即删远端分支。
- 发布用 annotated tag（`v0.1.0-beta.1`），**tag 只增不改**。
- 真实 Key 只进 `backend/.env` 与 Actions Secrets，永不提交。

## 2. 一次性设置（管理员）

`main` 保护已开启：必须 PR、1 人批准、dismiss stale、线性历史、禁强推/禁删；`Include administrators` 关闭，管理员可绕过全部限制直推（代价是强推/删除也不拦，只靠自觉）。

还差三项，建议补：

1. 打开 *Require conversation resolution*（未解决的评论不能合并）；
2. 给 `v*` 加 tag ruleset（禁止 update/delete）；
3. 打基线 tag：

```sh
git switch main && git pull --ff-only
git tag -a v0.1.0-beta.1 -m "内测基线：测试版上线"
git push origin v0.1.0-beta.1
```

成员：日常给 **Write**，Admin 只留 1 人；全员开 2FA；离队当天移除权限。
仓库当前是 **public**，涉密内容先转 private 再提交。

## 3. 日常五步

```sh
# 1) 同步主干
git switch main && git pull --ff-only

# 2) 切分支
git switch -c feat/diet-quick-add-42

# 3) 开发 + 逐块提交（别用 git add .）
git add -p
git commit -m "feat(backend): 饮食快录失败进入本机队列并按原幂等键补发"

# 4) 推送、开 PR（跨天任务第一天开 Draft）
git push -u origin feat/diet-quick-add-42

# 5) 批准后 Squash and merge → Delete branch
git switch main && git pull --ff-only
git branch -D feat/diet-quick-add-42 && git fetch -p   # squash 合并后只能 -D
```

跟进主干只在自己的未合并分支上做：`git rebase main && git push --force-with-lease`。

## 4. 分支与提交

分支：`feat/` `fix/` `docs/` `chore/` `refactor/` `test/` `hotfix/` + 小写连字符 slug；有 Issue 带编号（`feat/xxx-42`）。不用中文、空格、下划线、个人名分支。

提交格式 `<type>(<scope>): <摘要>`，摘要 ≤50 字、祈使语气、不加句号。

| type | 场景 | scope |
| --- | --- | --- |
| `feat` / `fix` | 新功能 / 修 bug | `backend`、`mobile` |
| `docs` / `chore` | 文档 / 杂务 | `docs`、`deps` |
| `refactor` / `perf` / `test` | 重构 / 性能 / 测试 | `rules`、`db`、`ci` |
| `ci` / `build` / `revert` | 流水线 / 依赖 / 回滚 | 跨层可省略 |

破坏性变更：`feat(api)!: ...` + footer `BREAKING CHANGE: ...`。
禁止 `update`、`fix bug`、`WIP`、`测试` 直接进 `main`。

## 5. 合并门禁（按改动范围跑）

| 改了什么 | 必须跑 |
| --- | --- |
| `backend/app/**` | `cd backend && python -m pytest -q` |
| `backend/migrations/**` | `pytest tests/test_migrations.py` + 真 MySQL 上 `upgrade head` / `downgrade -1` 往返 |
| `backend/rules/*.yaml` | 升 `version` + `pytest tests/test_health.py tests/test_screening_worker.py` |
| 固定文案 / 错误码 | `pytest tests/test_copy_audit.py` |
| `mobile/src/**` | `cd mobile && npm run typecheck && npm run lint && node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/*.test.mjs` |
| API 路由 / schema | `pytest`，同步 `docs/features.md` + `docs/architecture.md` |
| 新增环境变量 | 同步 `backend/.env.example`、`README.md`、`docs/operations.md` |
| 隐私 / 配额 / 删除 | `pytest tests/test_privacy.py tests/test_quota.py tests/test_observability.py` |
| 迁移 / Compose | `docker compose build` + 冒烟，同步 `docs/operations.md` |

跑完把**命令与关键输出**贴进 PR；"我本地跑过了"不算证据。
新增 Python 依赖写进 `requirements.txt` 并锁版本；移动端用 `npx expo install`，不要 `npm install`。

## 6. PR 与合并

标题同提交规范（Squash 后就是主干提交信息），描述五段：

```markdown
## 改了什么
## 为什么（Closes #42）
## 怎么验证（命令 + 输出 / 截图）
## 风险与回滚（迁移、配置、灰度）
## 文档同步
```

- 有效 diff ≤400 行，超过 800 行必须拆；纯格式化单独一个 PR。
- 至少 1 人批准，作者不能自批（管理员直推或自合的合并除外）；24 小时内首轮响应。
- 合并前：门禁通过、对话已解决；只允许 Squash，禁止 merge commit。管理员直推 main 等于跳过审批，仅限收尾提交与回滚。

评审重点：红线/分级仍在规则层、红线零 LLM、写失败有本机队列与原幂等键、迁移可回滚、训练授权默认关闭、日志脱敏、文案与 FRD 一致、不手改 `mobile/ios|android`。

## 7. 发布

```sh
git switch main && git pull --ff-only
git tag -a v0.1.0-beta.1 -m "内测：健康初筛与叫声解读"
git push origin v0.1.0-beta.1
```

GitHub → Releases → Draft a new release → 选 tag，notes 写四段：变更 / 迁移 / 配置 / 回滚。
0.x：不兼容升 MINOR，修复升 PATCH，内测加 `-beta.N`。
只有"已上线版本要单独修 bug"才从 tag 切 `release/x.y`；热修复从线上 tag 切 `hotfix/*`，PR 回 `main` 后打新 PATCH。

## 8. 管理员：本地验收队员分支

```sh
git fetch --all --prune
git for-each-ref --sort=-committerdate --format='%(refname:short) %(committerdate:relative)' refs/remotes/origin | head

# 同仓库分支：独立 worktree，不动当前目录
git worktree add ../rongxing-review feat/xxx
cd ../rongxing-review && git log -1 --format='%H %s'   # 与 PR 的 headRefOid 核对

# fork PR
gh pr checkout 42        # 或 git fetch origin pull/42/head:review/pr-42

# 跑起来：独立 .env + 独立库（如 rongxing_review）
cp backend/.env.example backend/.env
cd backend && python -m alembic upgrade head
uvicorn app.main:create_dev_app --factory --host 127.0.0.1 --port 8000
cd ../mobile && npm ci && EXPO_PUBLIC_API_URL=http://127.0.0.1:8000/api/v1 npm start

# 看差异（三点，排除 main 的新提交）
git diff main...HEAD --stat
```

验收结论写进 PR；通过后 Squash and merge。清理：

```sh
git worktree remove ../rongxing-review
git branch -D feat/xxx && git fetch -p
```

对方 force-push 后不要 merge，先确认本地没有自己的提交，再 `git reset --hard origin/feat/xxx`。
**跑分支等于执行对方代码**：先看 diff 再 `npm ci`，别把生产 Key 放进验收目录。

## 9. 本仓库的坑

| 坑 | 处理 |
| --- | --- |
| 根 `.gitignore` 全局忽略 `*.png` | 确需入库：`git add -f path.png`；设计图放 `docs/` |
| `视觉参考/`、`.codegraph/`、`.superpowers/`、`.impeccable/` 不同步 | 正常，不要 `-f` 硬塞 |
| `EXPO_PUBLIC_*` 构建期内联 | 改地址后 `npx expo start -c` 重新构建 |
| 测试用 SQLite、运行时 MySQL 8 | 迁移 PR 必须上 MySQL 跑往返 |
| `git commit -a` 带入 `.env` | 用 `git add -p`，提交前看 `git diff --cached --stat` |

## 10. 速查

```sh
git status -sb                 # 状态
git diff --cached --stat       # 即将提交什么
git rebase main                # 自己的分支跟主干
git push --force-with-lease    # 永远不要裸 --force
git revert <sha>               # 公共分支回滚
git reflog                     # 找回误删 / 误 reset
```

待办：落地 CI（`.github/workflows/ci.yml`）后，把 `backend-tests`、`mobile-checks` 设为必需检查。
