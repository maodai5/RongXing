# 绒星 2.0 · 项目速查

> 给 AI 与开发者快速定位用。事实源优先级：FRD（功能/验收/固定文案）> PRD（范围）> architecture（接口/数据结构）；本文只做索引与要点，冲突时回改本文。
> 生成时间：2026-11-21。仓库：`https://github.com/ember-L/Fuzzstar`，本地：`D:\pjoct\AndroidStudioProjects\rongxing2.0`。

## 一句话

猫主人的健康管理与陪伴 App：建档 → 数字分身 → 规则初筛（红线零 LLM）→ 饮食体重 → 聊天/叫声解读。FastAPI 后端 + Expo 移动端（原生与 Web 同一套代码）。

**第一卖点兼取舍裁判**：分诊级安全感——红线由版本化 YAML 硬规则判定，命中即「建议立即就医」卡，绝不经过 LLM；红线初筛不占配额。情感互动与视觉不得削弱安全表达。

## 事实源与必读文档

| 文档 | 权威范围 | 路径 |
| --- | --- | --- |
| FRD | 功能点 F01–F13、验收 AC、症状/红线/动作/公式、固定文案、错误码 | `docs/product/FRD.md` |
| PRODUCT | 定位、用户、边界、品牌承诺、明确不做项、事实源优先级 | `docs/product/PRODUCT.md` |
| PRD | 范围、旅程、指标、商业模式 | `docs/product/PRD.md` |
| architecture | 数据模型、API、模块边界 | `docs/architecture.md` |
| features | 当前实现行为与本地联调 | `docs/features.md` |
| operations | 环境变量、脱敏、迁移/备份、告警、回归证据 | `docs/operations.md` |
| collaboration | 分支/PR/提交/发布规范 | `docs/collaboration.md` |

固定文案由 `backend/tests/test_copy_audit.py` 一字不差锁定；改文案必须同步 FRD 与测试。规则包 `backend/rules/*.yaml` 的 version 是契约，改规则要升版本并在 FRD/features 记录。

## 技术栈与关键约束

- 后端：Python 3.11+、FastAPI、SQLAlchemy 2、Alembic、MySQL 8（运行时**不支持 SQLite**，仅测试用）、Redis 7 + arq（DB 为任务唯一事实源）、S3 兼容桶（**必须开版本控制**）、DashScope。
- 前端：Expo SDK 58 / RN 0.88 / expo-router / TS 6 / react-native-web 0.21，Node 22 本机可用。
- 规则层：`backend/rules/*.yaml` 版本化下发（symptoms sx-2026-11-19 / redlines rl-2026-11-19 / scoring sc-2026-11-19 / advice at-2026-11-19 / chat ct-2026-11-19 / feeding fd-2026-11-19 / action_map am-2026-11-18）。
- 硬性边界：AI 不替代兽医诊断；叫声解读仅娱乐参考（置信度 <0.4 标「不太确定」）；训练授权默认关；注销 15 天物理清除；配额 UTC 自然日 初筛10/分身3/聊天50，红线豁免。

## 注释风格（改代码前先对齐）

- 密度低：函数 docstring 只在有业务规则时写（239 函数中 38 个有），一句话，不写 Args/Returns。
- 模块首行一句话：业务模块中文带编号（`健康初筛：规则硬判定 + 存档…（FRD F05-1~8）。`），core/models/providers 等底层用英文（`T18: structured JSON logs (ARCH:517)…`）；需要交代取舍时空一行写第二段。
- 行内注释独立成行：编号 + 全角冒号 + 一句结论，讲约束或原因（`# F11-1：仅新建生成计数，进行中合并不计`）。
- 前端全中文，文件头 `// 功能名（编号）：职责`，副作用上方写「触发 → 行为 → 约束」，箭头用 `→`。
- 禁止：TODO/FIXME/HACK、注释掉的代码、作者/日期/changelog、逐参数解释、显而易见的状态描述。

## 后端速查（backend/，167 文件）

- 入口：`app/main.py:58 create_app(settings)`、`app/main.py:108 create_dev_app()`。模块级 `app`（`:113`）只有 health，**业务联调必须用 factory**。
- 13 个 router 注册于 `app/main.py:37-51`，统一前缀 `/api/v1`。鉴权四档：公开 / current_user / consented_user / owned_pet。
- 中间件：auth 限流（30/min/IP，仅 auth POST）、request 日志（X-Request-ID）、CORS（仅 localhost 开发源）。
- 25 张表，按域分 `app/models/{user,pet,health,task,media,chat,tracker,platform}.py`。迁移 head = `20261119_15`（quota_denials）。
- 规则判定核心：`app/services/triage.py:184-218`（红线最先硬判 urgent → 基础项 vet → 症状最高档 → normal，纯函数可复现）；AI 建议 `advice.py`、健康分 `health.py:11-36`（读时衰减，observe 72h）。
- 任务：`app/services/tasks.py` 幂等入队/租约 15min/最多 3 次；worker `app/worker.py`（arq，max_jobs=2，sweep 每 5s）。DB 为队列真源，Redis 宕不丢任务。
- Provider：`app/providers/dashscope.py`（qwen-image-edit / wanx2.1-i2v-turbo / qwen-max / qwen-plus / qwen2-audio），`fake.py` 全链路假实现。
- 萌宠动图（F14）：`app/providers/motion.py` 持 `MotionProvider` 协议（enchant/animate），`provider_from_env` 是唯一接入点，当前仅 `placeholder`（离线程序化绘制）；接入真模型时在此加分支，模型名与凭据由外部配置提供，不写入仓库。规则在 `rules/motion.yaml`（12 动作、健康/手势/情绪映射），产物 GIF 规格硬约束 ≤7s/高 480/循环，由 `app/services/motion.py:verify_gif` 逐条校验。
- 预生成资产入库：`scripts/seed_motion_assets.py` 把本地 GIF 写入 MinIO `motions/{pet_id}/{task_id}/` 并刷新 succeeded 的 `motion_batch` 任务，客户端零改动。产物体积大，不入库。
- 测试：`backend/tests/` 43 文件，unittest + 临时 SQLite + TestClient，**无 conftest.py**。`python -m pytest -q` 或 `python -m unittest discover -s tests`。
- 启动：`cd backend && uvicorn app.main:create_dev_app --factory --host 127.0.0.1 --port 8000`。全栈用 `docker compose up --build -d`（MySQL/Redis/migrate/api/worker）。
- 演示数据：`python -m scripts.seed_demo` → demo@rongxing.app / Rongxing2026（幂等，重置密码）。
- 管理员账号：`python -m scripts.create_admin` → admin@rongxing.app / Rongxing2026（幂等，提升 role=admin 并重置密码）。
- 坑：upsert 双方言 `quota.py:47-52`/`tasks.py:23-26`；`with_for_update()` 在 SQLite 空操作；rules_version 拼接 ≤32 字符；媒体校验依赖系统 ffprobe；S3 桶无版本控制则上传确认全失败；worker 启动强依赖 DASHSCOPE_API_KEY + MEDIA_S3_* + 两个 YAML。

## 前端速查（mobile/，85 源文件）

- 路由：`src/app/(tabs)/{index,health,chat,profile}.tsx` 四 tab；子页 `diet|weight|feeding|reminders|timeline/[petId].tsx`、`screening/{[petId],history,result}`。`App.tsx`/`index.ts` 是遗留模板，可忽略。
- 鉴权：`src/lib/session.ts` createSessionClient（内存 access + 持久 refresh，401 单飞刷新只重试一次）；`api.ts` 单例，web 用内存 token、原生用 SecureStore。
- 离线：`retry-queue.ts`（diet/chat_text，音频不入队）→ `offline-store.ts`（原生 expo-sqlite，web 内存）→ `offline-sync.ts`（5s 轮询 + 回前台 + web online，带原幂等键补发）。
- 分身：`avatar.ts` 三级回退（个人片→占位片→静态）、`player-machine.ts` 仅待机态切换 + 300ms 淡变、`action-map*.ts` 版本映射、`asset-cache.ts` LRU 200MB、`task-poller.ts` 2s 轮询（avatar 600s / screening 60s / audio 45s / gif 60s）。
- 情绪：`chat-emotion.ts` emotionBus（聊天 publish → 首页 AvatarPlayer 播一次回待机）。
- 主题：`src/theme.ts`（brand `#FEAE36`、surface `#FAF8F6`、文字 `#743A10`、画板橙 `#A34400`、urgent `#B42318`）；老页面仍有内联硬编码色值。
- Web 限制：刷新丢登录态/离线队列（内存 store）、无录音、无本地通知、无系统分享；生产必须 HTTPS 的 `EXPO_PUBLIC_API_URL`。
- 测试：`node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/*.test.mjs`（26 文件 / 155 项）；`npm run typecheck`、`npm run lint`。**无 test 脚本**，无组件渲染测试。
- 启动：`cd mobile && npm ci && CI=1 npx expo start --web --port 19006`（本机后台保活方式见记忆）；健康检查 `curl 127.0.0.1:19006`。
- API 地址：dev 未设 `EXPO_PUBLIC_API_URL` 时自动探测 `http://<host>:8000/api/v1`；改变量必须完全重启 Expo（`npx expo start -c`）。

## 常用命令（在此仓库执行）

```sh
# 后端门禁
cd backend && python -m pytest -q
python -m unittest discover -s tests
python -m scripts.ops_report --days 7
python -m scripts.latency_sample --samples 200

# 前端门禁
cd mobile && npm run typecheck && npm run lint && node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/*.test.mjs

# Web 起服务
cd mobile && CI=1 npx expo start --web --port 19006

# git（本机 schannel 握手失败，必须带 openssl）
git -c http.sslBackend=openssl pull --ff-only
```

## Git 与协作（docs/collaboration.md）

- main 受保护只收 PR；每次改动从 main 切短命任务分支（feat/fix/docs/chore/refactor/test/hotfix + `<slug>`，1–3 天）；禁止长期 dev、禁止直推 main；Squash and merge 后删分支；tag 冻结发布。
- 提交信息 `<type>(<scope>): <摘要>`，scope：backend/mobile/docs/rules/db/ci/deps。摘要 ≤50 字、祈使语气、无句号。
- 门禁按改动范围执行（改动 backend/app → pytest；mobile/src → typecheck+lint+node test；规则 YAML → 升 version + 相关测试；固定文案 → copy_audit）。无 CI，把命令与输出贴进 PR。
- 坑：根 `.gitignore` 全局忽略 `*.png`、`视觉参考/`、`.codegraph/` 等，需入库的图片用 `git add -f`；`backend/.env`、`.venv*` 已忽略，**禁止提交**；仓库 public，push 即公开，提交前查 Key/内网地址/个人路径。

## 当前状态与待办（不虚构）

- 已完成：F01–F13 的 P0 本地实现与 417 后端 / 155 前端用例；MySQL 迁移回滚、备份恢复演练；本地 p95 2.34ms（n=1600）；Web 端四路由实测可渲染。
- F14 萌宠动图：上传→生成→播放全链路本地跑通；占位实现可离线产出全部 12 个动作。此前离线预生成的 11 个 GIF 已 seed 到 MinIO 并对 demo 账号下发（`usable: true`），产物不在仓库内。
- **待外部**：真实 DashScope 生成/建议/音频质量、真实 SMTP/S3、生产 HTTPS、真机指标与 crash-free（Sentry）、100 宠并发压测、兽医评审、Figma 授权与原创占位素材、TestFlight/APK 内测。
- 本机坑：git 直连偶发 schannel TLS 握手失败，加 `-c http.sslBackend=openssl`；浏览器 headless 前台启动会被回收，须后台保活。
