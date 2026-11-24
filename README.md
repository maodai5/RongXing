# 🐱 绒星 RongXing

> 给猫主人的健康管理与陪伴 App：建档、数字分身、规则初筛、饮食体重、聊天与叫声解读。

绒星面向猫主人，把「日常记录」和「健康预警」放在同一个入口：为猫咪建立档案与数字分身，用确定性规则完成健康初筛与红线分流，再通过饮食、体重、聊天和叫声解读陪伴日常。仓库包含 **FastAPI 后端** 与 **Expo 移动端 / Web**。

## ✨ 项目亮点

- 🏠 **数字分身**：猫咪照片与视频生成分身，首页常驻健康状态与快捷操作。
- 🩺 **健康初筛**：规则驱动分级，红线直接给出就医卡，不经过 LLM；支持 24h 复查提醒。
- 🍽️ **饮食与体重**：快录、按本地日聚合、RER/DER 喂养参考、90 天体重趋势与骤变提示。
- 💬 **陪伴聊天**：每猫独立会话，20 条上下文；红线消息零 LLM，直接转就医引导。
- 🎙️ **叫声解读**：1–60 秒录音异步解读情绪与置信度，明确娱乐参考边界。
- 📴 **浅离线**：饮食等写入失败进入本机队列，恢复网络后带原幂等键补发。
- 🔐 **隐私与配额**：协议版本化、训练授权默认关闭、注销 15 天冷静期后物理清除。

## 🧰 技术栈

| 层 | 选型 |
| --- | --- |
| 🐍 后端 | Python 3.11+、FastAPI、SQLAlchemy 2、Alembic、MySQL 8（utf8mb4） |
| ⚡ 异步 | Redis 7 + arq；数据库是任务与状态的唯一事实源 |
| 🗄️ 存储 | S3 兼容私有桶（需版本控制），可选 |
| 🤖 AI | 阿里云 DashScope（qwen-image-edit、wanx2.1-i2v-turbo、qwen-max、qwen-plus、qwen2-audio） |
| 📱 移动端 | Expo SDK 58 + React Native + TypeScript + expo-router（原生与 Web 同一套代码） |
| 🐳 编排 | Docker Compose（MySQL、Redis、迁移、API、worker） |

## 📁 仓库结构

```text
backend/
  .env.example  🔑 唯一环境配置模板（本机与 Compose 共用）
  app/
    core/        ⚙️ 配置、数据库、鉴权依赖、错误与中间件
    routers/     🌐 13 个 API 路由模块
    schemas/     📦 Pydantic 请求 / 响应模型
    models/      🗃️ SQLAlchemy ORM（按业务域拆分）
    services/    🧠 规则、任务、媒体、配额等业务实现
    providers/   🔌 LLM / 生成服务适配层
  rules/         📜 版本化 YAML 规则包
  assets/        🎞️ 占位片素材
  migrations/    🧬 Alembic 迁移
  tests/         ✅ 后端测试
mobile/          📱 Expo App
wan-video-studio/  🎬 独立 Flask 工作台：宠物 2.5D 形象 + Wan 3.0 动作视频（使用说明见该目录 README）
docs/            📚 文档（索引见 docs/README.md）
docker-compose.yml  🐳 一键本地编排
```

## 🚀 快速开始（Docker Compose）

> 适合第一次跑通全栈：MySQL、Redis、数据库迁移、API 与 worker 一次拉起。需要 Docker Engine 24+ 与 Compose v2。

### 1️⃣ 准备环境变量

```sh
cp backend/.env.example backend/.env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
# 将输出填入 backend/.env 的 JWT_SECRET；
# 同时让 MYSQL_* 与 DATABASE_URL 的账号、密码、库名保持一致
```

> `docker-compose.yml` 通过 `env_file` 直接读取 `backend/.env`，根目录无需再放配置或模板。

首次启动会拉取 `python:3.12-slim`、`mysql:8.4`、`redis:7-alpine` 并安装 Python 依赖，因此需要能访问镜像源与 PyPI。

### 2️⃣ 启动并检查

```sh
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8000/api/v1/health
```

启动顺序为 `mysql`/`redis` → `migrate`（`alembic upgrade head`）→ `api` 与 `worker`。MySQL/Redis 只在容器网络内可达，宿主机仅暴露 API 端口 8000。

### 3️⃣ 常用命令

```sh
docker compose logs -f api worker                  # 查看日志
docker compose --profile mail run --rm mail-worker # 可选：邮件投递（单次运行，需 SMTP_*）
docker compose down                                # 停止，保留数据卷
docker compose down -v                             # 停止并删除数据库数据（不可恢复）
```

| 服务 | 作用 | 宿主机端口 |
| --- | --- | --- |
| `mysql` | MySQL 8，utf8mb4，数据卷持久化 | 不暴露 |
| `redis` | arq 队列与计数 | 不暴露 |
| `migrate` | 一次性执行 Alembic 迁移 | — |
| `api` | FastAPI（`create_dev_app` factory） | `8000` |
| `worker` | arq 长任务 worker | 不暴露 |
| `mail-worker` | 邮件投递，`mail` profile | 不暴露 |

首次使用还需在 `backend/.env` 中配置协议版本与链接（`PRIVACY_*`、`TERMS_*`），否则协议 / consent 接口会返回 503；媒体上传需配置 `MEDIA_S3_*`，否则返回 503。Compose 不会启动 MinIO，请使用外部 S3 兼容私有桶。

## 💻 本地开发（不使用 Docker）

### 🐍 后端

需要 Python 3.11+ 与一个已建好的 MySQL 8 数据库。

```sh
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env：DATABASE_URL、JWT_SECRET、可选的 SMTP / S3 / DashScope
python -c 'import secrets; print(secrets.token_urlsafe(32))'
python -m alembic upgrade head
uvicorn app.main:create_dev_app --factory --host 127.0.0.1 --port 8000
```

`app.main:app` 只有 `/api/v1/health`；业务联调必须使用上面的 `create_dev_app`，且 **不要暴露到外网**。健康检查：<http://127.0.0.1:8000/api/v1/health>。

### 🧪 初始化演示数据（可选）

首次跑通后，在 `backend/` 执行：

```sh
python -m scripts.seed_demo
```

脚本读取 `backend/.env` 的 `DATABASE_URL`，连接本地 MySQL 并创建（或补齐）演示账号：

| 项目 | 值 |
| --- | --- |
| 邮箱 | `demo@rongxing.app` |
| 密码 | `Rongxing2026` |
| 猫咪 | 团团（中华田园猫，2023-05-20 出生，已绝育） |
| 示例数据 | 5 条体重、4 条饮食、1 次正常初筛、4 条聊天消息 |
| 协议状态 | 已同意当前隐私政策与服务协议；训练授权关闭 |

脚本是幂等的：重复执行不会重复插入数据，但会把演示账号密码重置为默认值。自定义账号：

```sh
python -m scripts.seed_demo --email demo@example.com --password 'Demo123456'
```

> 仅用于本地开发库。不要在共享环境或生产库执行；脚本会修改同名演示账号的密码。

### 📱 移动端 / 网页开发

```sh
cd mobile
npm install
export EXPO_PUBLIC_API_URL=http://127.0.0.1:8000/api/v1  # 可选；开发模式会自动探测
npm start          # 扫码用 Expo Go，或按 i / a 打开模拟器
npm run web        # 浏览器调试
```

开发模式未设置 `EXPO_PUBLIC_API_URL` 时，应用会自动使用当前 Web 页面主机或 Expo Metro 主机的 `8000` 端口；仍推荐显式设置，尤其是真机调试或后端改端口。

真机调试需让后端监听局域网：`uvicorn app.main:create_dev_app --factory --host 0.0.0.0 --port 8000`，并把 `EXPO_PUBLIC_API_URL` 改成电脑的局域网地址；发布版只接受 HTTPS。修改环境变量后需完全重启 Expo（`npx expo start -c`），仅在 App 内刷新不会让 Metro 重新注入。

### 🤖 在 Android Studio 里跑原生调试

原生工程已由 `npx expo prebuild` 生成在 `mobile/android`，直接用 Android Studio 打开该目录即可（不要打开仓库根目录）。

1. 先起后端与 Metro：后端 `uvicorn app.main:create_dev_app --factory --host 127.0.0.1 --port 8000`，Metro `cd mobile && EXPO_PUBLIC_API_URL=http://127.0.0.1:8000/api/v1 npx expo start`。
2. 模拟器/真机接上后执行端口反向，让 App 的 `127.0.0.1` 落到电脑：`adb reverse tcp:8000 tcp:8000 && adb reverse tcp:8081 tcp:8081`。
3. Android Studio 选中 `app` 配置点 Run，或命令行 `JAVA_HOME=<Android Studio>/jbr ./gradlew assembleDebug`。

本机 Windows 环境有三处已修好的坑，换机器时可复现排查：

- `settings.gradle` 不能带 UTF-8 BOM，否则 Gradle 9 报 `Unexpected character: '\ufeff'`。
- `react-native-reanimated` 在共享 node_modules 里属于 extraneous，autolinking 会把它的 codegen 路径撑到 260 字符以上；已在 `mobile/package.json` 的 `expo.autolinking.exclude` 排除。
- CMake 暂存目录默认在深层项目路径下同样会超长，已在 `app/build.gradle` 用 `buildStagingDirectory` 指到 `D:/rx-cxx`。

数据库连接：`backend/.env` 的 `DATABASE_URL` 指向 MySQL（本机 `docker ps` 里的 `rx2-mysql`，库名 `rongxing`），首次运行前执行 `python -m alembic upgrade head`。

## 📱 Expo 打包与发布

移动端使用 Expo SDK 58 + expo-router。日常联调可以直接用 Expo Go；需要安装包、上架或原生模块调试时，按下面的方式打包。

### 🧭 打包方式对比

| 方式 | 命令 | 产物 | 适用场景 |
| --- | --- | --- | --- |
| Expo Go | `npm start` | 无安装包 | 日常 JS / UI 联调 |
| EAS Build（推荐） | `npx eas-cli@latest build` | `.ipa` / `.aab` / `.apk` | 内测分发与商店上架 |
| 本地原生构建 | `npx expo run:ios` / `npx expo run:android` | 模拟器或真机调试包 | 需要 Xcode / Android Studio 原生调试 |
| Web 静态导出 | `npx expo export -p web` | `dist/` 静态文件 | 网页部署 |

> Expo Go 只包含 Expo SDK 内置模块；如果后续加入自定义原生代码或未内置的 SDK 模块，需要改用 Development Build（`npx expo install expo-dev-client` 后用 EAS 打包）。

### 1️⃣ 首次配置 EAS

需要 Node.js 20+、一个 Expo 账号；上架还需 Apple Developer Program 与 Google Play Console 账号。

```sh
cd mobile
npx eas-cli@latest login
npx eas-cli@latest init       # 关联 / 创建 EAS 项目，写入 extra.eas.projectId
```

首次构建时若缺少应用标识，EAS 会提示填写，并写入 `app.json`。也可以提前在现有 `app.json` 的 `expo.ios` / `expo.android` 中补充（发布后不可随意更改）：

```json
{
  "expo": {
    "ios": { "bundleIdentifier": "com.yourcompany.rongxing" },
    "android": { "package": "com.yourcompany.rongxing" }
  }
}
```

仓库已提供 [mobile/eas.json](mobile/eas.json)（内测 APK + 生产包）；如需自行创建，内容如下：

```json
{
  "build": {
    "preview": {
      "distribution": "internal",
      "android": { "buildType": "apk" }
    },
    "production": {}
  },
  "submit": { "production": {} }
}
```

> `preview` 的 iOS 内测包需要先把设备 UDID 注册到描述文件：`npx eas-cli@latest device:create`。Android 的 `production` 默认产出 `.aab`，直接安装请用 `preview` 的 APK。

### 2️⃣ 构建安装包

```sh
# 内测：iOS 内部分发 + Android APK
npx eas-cli@latest build -p ios --profile preview
npx eas-cli@latest build -p android --profile preview

# 上架：App Store / Google Play
npx eas-cli@latest build -p ios --profile production
npx eas-cli@latest build -p android --profile production

npx eas-cli@latest build:list      # 查看构建记录
npx eas-cli@latest credentials     # 查看 / 管理签名凭证
```

EAS 会托管 iOS 证书与 Android keystore；团队协作时不要让多人重复生成 keystore。

### 3️⃣ 提交商店

```sh
npx eas-cli@latest submit -p ios --profile production
npx eas-cli@latest submit -p android --profile production
```

iOS 需要先在 App Store Connect 创建 App 并准备 `ascAppId`；Android 需要 Google Play 服务账号 JSON。`eas submit` 会交互式询问，也可以写入 `eas.json` 的 `submit.production`。

### 4️⃣ 本地原生构建

需要 Xcode 16+ / CocoaPods（iOS），或 Android Studio / JDK 17 / Android SDK（Android）。

```sh
npx expo prebuild --clean   # 生成 ios/ 与 android/ 原生工程
npx expo run:ios
npx expo run:android
```

`prebuild` 会依据 `app.json` 重新生成原生目录；如果手动改过原生代码，不要随意加 `--clean`。

### 5️⃣ 导出 Web 静态站点

```sh
npx expo export --platform web
npx serve dist              # 或部署到任意静态托管
```

产物在 `mobile/dist/`。Web 端同样通过 `EXPO_PUBLIC_API_URL` 指向后端，生产环境必须使用 HTTPS。

### 🔑 构建时环境变量

`EXPO_PUBLIC_*` 变量会在打包时内联进 bundle，**改完必须重新构建**才会生效。本地原生构建与 Web 导出会读取当前 shell：

```sh
EXPO_PUBLIC_API_URL=https://api.example.com/api/v1 npx expo run:ios
EXPO_PUBLIC_API_URL=https://api.example.com/api/v1 npx expo export --platform web
```

EAS 云端构建读不到本地 shell 变量，可在 `eas.json` 的 profile 中声明，或使用 EAS Environment Variables：

```json
{
  "build": {
    "production": {
      "env": { "EXPO_PUBLIC_API_URL": "https://api.example.com/api/v1" }
    }
  }
}
```

## ⚙️ 配置

- 🐍 `backend/.env`：唯一配置源，本地进程（API、worker、脚本、Alembic）与 Docker Compose 共用；模板为 `backend/.env.example`。
- 🐳 Docker Compose 通过 `env_file` 读取同一份 `backend/.env`；其中 `MYSQL_*` 用于初始化 MySQL 容器，容器内的 `DATABASE_URL` 由入口脚本按这些值生成，因此根目录不再需要 `.env`。
- 📱 `mobile/.env` 或 shell：`EXPO_PUBLIC_API_URL` 指向后端，必须以 `/api/v1` 结尾。
- 进程环境变量优先于 `.env`，便于容器与 CI 覆盖。所有 `.env` 均已被 git 忽略，禁止提交真实密码或 API Key。
- 关键变量：`DATABASE_URL`、`MYSQL_DATABASE` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_ROOT_PASSWORD`（Compose）、`JWT_SECRET`（≥32 字节）、`REDIS_URL`、`PRIVACY_*` / `TERMS_*`、`SMTP_*`、`MEDIA_S3_*`、`DASHSCOPE_API_KEY`、`QUOTA_*`、`EXPO_PUBLIC_API_URL`。

## 🧪 测试

```sh
cd backend
python -m pytest -q
```

依赖真实 Redis / arq 的模块在缺少 `arq` 或 `REDIS_TEST_URL` 时会跳过；S3 端到端检查需显式设置 `MEDIA_S3_SMOKE=1` 并指向一次性测试桶。移动端：

```sh
cd mobile
npm run typecheck
npm run lint
node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON --test tests/*.test.mjs
```

## 📚 文档

| 文档 | 内容 |
| --- | --- |
| [文档索引](docs/README.md) | 全部文档的阅读路径与维护约定 |
| [产品总览](docs/product/PRODUCT.md) | 定位、用户、边界与事实源优先级 |
| [PRD](docs/product/PRD.md) | 产品范围、用户旅程、指标与合规 |
| [FRD](docs/product/FRD.md) | 功能点、验收标准、症状 / 红线 / 公式附录 |
| [架构](docs/architecture.md) | 技术栈、模块结构、数据模型、API 与部署 |
| [设计系统](docs/design.md) | 色彩、排版、组件与动效规范 |
| [功能与 API](docs/features.md) | 当前功能细节、联调步骤与本地 API 范围 |
| [运维 Runbook](docs/operations.md) | 环境变量、日志、迁移 / 备份、告警与回归矩阵 |
| [团队协作与 Git 工作流](docs/collaboration.md) | 分支、提交、门禁、发布与本地验收命令 |
| [交付计划](docs/plans/2026-11-17-rongxing-full-stack.md) | 历史任务拆分与验收记录 |

## 🩺 常见问题

- **接口返回 503**：协议 / consent 未配置（`PRIVACY_*`、`TERMS_*`），或媒体上传未配置 `MEDIA_S3_*`。
- **Expo 改了 API 地址不生效**：`EXPO_PUBLIC_*` 是构建期内联，执行 `npx expo start -c` 完全重启。
- **Docker 构建失败**：确认能访问镜像源与 PyPI；国内网络可先为 Docker 配置镜像加速，再执行 `docker compose build api`。
- **8000 端口被占用**：停止占用 8000 的进程，或临时修改 `docker-compose.yml` 中 `api.ports` 的宿主机端口后重新 `docker compose up -d`。
- **MySQL 连接串报错**：密码会拼进 `DATABASE_URL`，请使用 URL 安全字符，避免 `@ : / ? #`。

## 🔒 安全与边界

- 本地入口按来源 IP 做数据库限流，适用于开发与内测，不是生产级分布式防滥用方案。
- 健康初筛、健康分与红线分流是纯规则判定；AI 只生成建议文案，不参与分级。
- 训练数据授权默认关闭；注销为 15 天冷静期后物理清除，审计日志匿名保留。
- 支付未接入，`plans` / `entitlements` 仅作代码目录与权益预留。
- 真实 DashScope / SMTP / S3、生产 HTTPS、真机通知与压测仍待外部验收，详见 [运维 Runbook](docs/operations.md)。
