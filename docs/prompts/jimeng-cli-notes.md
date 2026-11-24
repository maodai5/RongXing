# 即梦 CLI 接入：实测结论

## 最新结论（2026-11-23 20:40 实测，已可出片）

**可以用。** 账号 `mortal🦊💎`，余额 825 积分，`is_new_user: false`，会员门槛已解除。

实测链路（`Seedance_2.5_draft`，4 秒 / 720p）：

| 项 | 结果 |
| --- | --- |
| `credit estimate` | `affordable: true`，36 积分 |
| 实际提交 | 成功，耗时约 1 分 50 秒 |
| 扣费 | 825 → 789，正好 36 分 |
| 产物 | 480×480 / 49 帧 / 3.92 秒 / 6.37 MB |
| 校验 | `check_gif` 通过（`max_seconds=7`、`max_height=480`） |

报价参考（4 秒 / 720p）：`Seedance_2.0_mini` 24 分、`Seedance_2.5_draft` 36 分、`Seedance_2.5_flash` 76 分。
首帧一致性必须用支持 `firstFrame` 的档位，`Seedance_2.0_mini` 不支持，传 `--image` 会被忽略。

### 两个新踩的坑

1. **样片模型必须显式 `--draft`**
   `Seedance_2.5_draft` 提交时若不带该参数，服务端直接报
   `draft model requires either draft=true or draftTaskId`，扣费 0。
   `scripts/jimeng_motion_gen.py` 已按模型名后缀自动补参。

2. **样片输出带「AI生成」角标**
   位置固定在左上角，半透明，肉眼可见但颜色与底色只差 5–10 个色阶。
   处理方式：角标是固定叠加层，框内为纯色背景，**把框下方同尺寸背景块平移上来覆盖 + 边缘 4px 羽化**。
   已内置为 `erase_badge()`，由 `to_gif()` 默认调用（`strip_badge=True`），返回覆盖像素数记在 `badge_pixels`。
   注意不要按亮度/颜色阈值挑像素回填：笔画太淡会残留，且容易把猫耳当角标擦掉。

### jimeng-cli（npm 0.3.9）不可用

- 全局安装缺关键依赖：`date-fns` 只有 308 个文件、无 `index.mjs`，CLI 启动即 `ERR_MODULE_NOT_FOUND`
- 已在 `D:/pjoct/_tools/jimeng` 重新安装（`--registry https://registry.npmmirror.com`），CLI 可运行
- 但 **token 池为空**：`token points --all` 报 `No token available`，`image generate` 报 `未找到可评估的 token 候选集`
- 它需要 `jimeng login` 交互式登录拿 `sessionid`，与 `jimeng-novel` 的登录态不通用

**结论：走 `jimeng-novel`，不要用 npm 的 `jimeng-cli`。**

## 历史结论（同日早些时候，已被上面覆盖）

## 结论（2026-11-23 实测）

即梦 CLI（`jimeng-novel` 1.0.10）已在本机登录，**账号余额 92 积分，非会员**。

视频生成被服务端硬性限制为会员功能，**不开会员无法出片**：

- `credit estimate` 对全部视频模型返回 `affordable: false`，连 24 分/条的档位也判不可负担，而余额 92
- 实际提交最便宜的 `Seedance_2.0_mini`（4 秒、24 分）后，服务端返回：
  ```json
  {"status":"failed", "error_message":"仅对会员用户可用",
   "credits":{"deducted":0,"preDeducted":0}}
  ```
- 扣费为 0，说明不是余额不足，是账号等级门槛
- `promotion get` 显示 `active: false`，当前无免费活动
- `account get` 显示 `is_new_user: true`，无 VIP 标识

这是产品策略，CLI 只是客户端，绕不过鉴权，也不应该绕。

## 可用模型与报价（4 秒，720p）

| 模型 | 积分 | 支持首帧 | 会员限制 |
| --- | --- | --- | --- |
| `Seedance_2.0_mini` | 24 | ✗ | 仅会员 |
| `seedance2.0_fast_vision` | 24 | ✓ | 仅会员 |
| `Seedance_2.5_draft` | 36 | ✓ | 仅会员 |
| `wan3.0` | 70 | — | 仅会员 |
| `Seedance_2.5_flash` | 190 | ✓ | 仅会员 |

注意 `Seedance_2.0_mini` 虽然最便宜，但 `firstFrame: false`——本链路靠首帧锁定原猫身份，
传 `--image` 会被忽略，出来的不是同一只宠物。脚本默认已改为 `Seedance_2.5_draft`（支持首帧，36 分/条）。

## 不想开会员的可行路径

### 1. 白嫖即梦网页端免费额度（如果有）

CLI 走的是画布积分体系，网页创作页有时另给每日免费次数。路径：
- 打开 https://jimeng.jianying.com/ → 视频生成 → 看"今日免费次数"
- 若有，用网页端手动出片，下载 mp4 后走本仓库的转 GIF + 入库脚本：
  ```sh
  # 把 mp4 放到产物目录后
  python -m scripts.seed_motion_assets --source backend/assets/motion
  ```
- 这与 CLI 不冲突：CLI 负责可复现流程，网页端只作为"素材来源"。

### 2. 换回免费视频模型（推荐，之前已跑通）

智谱 CogVideoX-Flash 是免费视频生成模型，之前用它已经产出 11 个动作（480×480 / 5.08s / 循环），
身份一致性验证通过。按你的要求，接入代码已从仓库移除、接口留空，但历史经验可复用：

- 接入点：`app/providers/motion.py` 的 `provider_from_env`
- 首帧：方形 1024×1024，背景 `#FFEDD7`
- 提示词：见 `docs/prompts/pet-avatar-chain.md`
- 踩坑：429 容量门禁、内容审核误判词、GIF 角标擦除

要恢复时只需在 `provider_from_env` 加一个分支，模型名与凭据从环境变量注入，不写进仓库。

### 3. 本地开源图生视频（零成本，质量次之）

有显卡时可跑 AnimateDiff / CogVideoX 本地权重，完全免费。适合离线批量预生成，
但本机当前无 ffmpeg/ffprobe 系统安装、无 GPU 验证记录，质量与耗时需实测。

### 4. 不生成新动作，复用已有 11 个

MinIO 里已有 11 个合格 GIF，只差 `look_right`。App 端对缺失动作回退待机动图，
不影响可用性。这是零成本方案。

## 脚本现状

`backend/scripts/jimeng_motion_gen.py`（未入库，本地工作区）：

- 调 `jimeng-novel generate-video`，首帧图 + 动作提示词 → mp4 → ffmpeg 抽帧 → Pillow 合成 GIF
- 产物对齐 `seed_motion_assets.py`，可直接 `--seed` 入库
- 不带 `--yes` 只报价不提交，避免误花积分
- `failure_reason()` 会回查节点，把"仅对会员用户可用"这类真实原因带出来
- 默认模型已从不支持首帧的 `Seedance_2.0_mini` 改为 `Seedance_2.5_draft`

等账号开通会员后，直接跑：
```sh
cd backend
python -m scripts.jimeng_motion_gen --actions idle,happy --yes
python -m scripts.seed_motion_assets --source assets/motion
```
