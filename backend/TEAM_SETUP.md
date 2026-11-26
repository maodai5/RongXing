# 团队接入说明（RongXing / FuzzStar2.0）

数据库和后端跑在一台主机上，其他成员从局域网连接。

## 服务器信息（主机机主维护）

| 项目 | 值 |
| --- | --- |
| 主机局域网 IP | `10.216.251.47`（Wi-Fi 变动后以机主最新通知为准） |
| MySQL | `10.216.251.47:3307`，库名 `rongxing`，用户 `rongxing`（注意端口是 **3307**，不是默认 3306） |
| 后端 API | `http://10.216.251.47:8000/api/v1` |
| 管理后台 | `http://10.216.251.47:8000/admin` |

> 数据库账号密码**不写在这里**，统一放在 `backend/team.env`（仅局域网开发用，别用于公网）。

## 成员端启动步骤（backend/ 目录）

仓库里已经带了 `backend/team.env`，**克隆下来就能用，不需要自己创建 `.env`，也不需要手写连接串**。

```bash
# 1. 创建虚拟环境并装依赖（Python 3.11+）
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 启动后端（成员本机调试用 127.0.0.1 即可）
.venv\Scripts\python.exe -m uvicorn app.main:create_dev_app --factory --host 127.0.0.1 --port 8000
```

### 唯一可能要改的文件：`backend/team.env`

用记事本 / VS Code 打开 `backend/team.env`，**只改第一行**：

```ini
SERVER_HOST=10.197.232.47
```

把它换成一个可达的数据库主机地址即可：

| 场景 | 填什么 |
| --- | --- |
| 连主机机主的库（默认） | 机主的局域网 IP，如 `10.216.251.47` |
| 本机自己跑了一份库 | `127.0.0.1`（同时把 `DB_PORT` 改成自己的端口） |
| 云服务器 | 服务器公网/内网 IP |

保存后重启后端即可。`DATABASE_URL` 会自动拼成
`mysql+pymysql://DB_USER:DB_PASSWORD@SERVER_HOST:DB_PORT/DB_NAME?charset=utf8mb4`，
所以**不需要手写 URL，也不会漏改端口**。

> 想让某个成员用不同配置又不影响别人：在 `backend/` 下自己建一个 `.env`（已被 git 忽略），
> 里面写同名变量即可覆盖 `team.env`，例如一行 `DATABASE_URL=...` 或 `SERVER_HOST=...`。
> 读取顺序是 `team.env` → `.env` → 进程环境变量，后者优先。


数据库表结构由主机机主负责迁移；成员一般**不需要**跑 alembic。

## 主机机主操作

```bash
# 启动后端（监听所有网卡，供局域网访问）
.venv\Scripts\python.exe -m uvicorn app.main:create_dev_app --factory --host 0.0.0.0 --port 8000

# 创建/重置管理员账户（无需邮箱验证）
.venv\Scripts\python.exe -m scripts.create_admin --email admin@rongxing.app --password <密码>

# 本地演示模式（独立 SQLite 演示库 + 演示数据）
.venv\Scripts\python.exe -m scripts.run_admin_demo --host 0.0.0.0 --port 8000
```

## 管理员登录说明

- 管理后台账号 = `ADMIN_EMAILS` 里登记的邮箱 + 对应密码
- **管理员登录不需要邮箱验证**：登录接口对 `ADMIN_EMAILS` 白名单内的邮箱跳过验证检查；`scripts/create_admin.py` 也会直接把邮箱标记为已验证
- 验证方式：机主在数据库所在机器运行上面的 `create_admin` 命令即可
- 安全提示：ADMIN_EMAILS 白名单就是权限边界，请勿把陌生人的邮箱加进去

## 常见问题

- **连不上数据库**：先看 `backend/team.env` 里的 `SERVER_HOST` 对不对；再确认主机和你在同一局域网、主机防火墙 3307/8000 入站规则已放行
- **后端启动报 JWT_SECRET 错误**：`JWT_SECRET` 至少 32 字节（`team.env` 里已给了团队共用的一个）
- **登录提示"请先验证邮箱"**：普通用户账号仍需邮箱验证；管理员账号若出现此提示，说明该邮箱不在 ADMIN_EMAILS 里
- **主机 Wi-Fi 切换后**：IP 可能变化，机主改 `team.env` 的 `SERVER_HOST` 推一次即可，其他成员 `git pull` 后重启；或让机主在路由器上给这台机绑定固定 IP
