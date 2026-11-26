# 绒星 · 动作工作台

独立 Flask 工作台，不依赖绒星 App 或主后端。先上传一张真实的宠物照片，工作台用生图模型按系统提示词把它重绘成同款宠物的萌系 **2.5D 卡通**全身立绘（纯色背景 #FEECD4）；预览满意后「确认使用」，再选择 **待机、开心、低落、不适、伏卧、左看、右看** 中的一个小动作。视频为 Wan 3.0 图生视频，请求 **3 秒、720P、带 AI 标识的 MP4**。

生图是图生图：照片以 Base64 data URL 内联在请求里发给服务，人物/宠物身份由参考图决定；补充描述只用于强调照片里看不清的细节，可以留空。默认生图模型为 `qwen-image-3.0-pro`。

## 本机启动

需要 Python 3.11+。在本目录首次设置：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp -n .env.example .env
# 编辑 .env，填入 WAN_MODEL（实际 Wan 3.0 图生视频模型 ID）和 DASHSCOPE_API_KEY。
# WAN_IMAGE_MODEL（生图模型 ID）默认 qwen-image-3.0-pro；留空则无法生成形象。
python app.py
```

打开 [http://127.0.0.1:5050](http://127.0.0.1:5050)。`python app.py` 自动读取同目录的 `.env`，不要提交此文件。未配置模型 ID 或 Key 时仍可打开网页，但不能提交。`WAN_DASHSCOPE_BASE_URL` 默认使用 [Token Plan 视频基础地址](https://token-plan.maas.qianwenaiapi.com/api/v1)。如果现有 `.env` 填的是同域名的 `/compatible-mode/v1`，启动时会按 `/api/v1` 使用，不需要修改 `.env`。也允许手动填入原有的百炼 HTTPS `/api/v1` 端点。

## 工作流

1. **上传照片**：JPG / PNG / WebP / BMP，≤10 MB，宽高 240–8000 px、比例 1:8–8:1，总像素不超过 3200 万；透明背景会自动铺底色 #FEECD4。不支持动图。
   > 提示：原图越接近全身越好；只拍到半身或头部时，工作台会按同一只宠物的毛色、体型补全后腿、脚掌和尾巴，但原图看得越全，补全越准。

2. **生成 2.5D 形象**：走 `POST /services/aigc/multimodal-generation/generation` 同步协议，content 里同时带参考图（data URL）与系统提示词，约 20–60 秒返回一张 1024×1024 图片，服务端居中裁成正方形并用 LANCZOS 缩到固定 **480×480** 后再交给页面预览（上游不接受 480 的请求尺寸，512 是它支持的最小正方形）。超过 4 MB 的参考图会先等比缩到最长边 2048 px 再发送。
3. **确认形象**：必须点「用它生成视频」，未确认不会启用「生成动作视频」。
4. **选择动作并生成视频**：首帧是 480×480 正方形，成片也是正方形（实测 960×960）。走 `POST /services/aigc/video-generation/video-synthesis` 异步协议，确认过的形象作为首帧；完成后服务端把 MP4 下载到本机 `state/videos/`，页面直接播放，并显示「下载 MP4」按钮保存到本地。提交后页面显示排队/生成状态与**已用时**；上游接口不返回百分比，进度条只表示任务仍在处理。

每点击一次「生成形象」或「生成动作视频」都只提交一个任务，按提供方规则计费。网页每 15 秒查询一次任务，同一标签页刷新可恢复未完成的查询。原照片不落盘；生成的形象与成片会自动保存到本机 `state/images/`、`state/videos/`（保留 30 天，重启后仍可下载），点「下载图片」「下载 MP4」可另存到浏览器的下载目录。

> 常见错误：若提交视频时页面收到 **400 BAD REQUEST**，通常是因为表单在禁用后才序列化，导致 `action` 或图片没有随请求发出；当前版本会先构建请求体再禁用表单，并用 `body.set('action', …)` 显式带上动作。

## 若要在线使用

默认只在本机启动，不是公网服务。若要私有部署，在生产 WSGI 服务前启用 HTTPS 反向代理，配置 `WAN_STUDIO_HOST`（公网域名，不带协议或端口）、`WAN_STUDIO_PASSWORD`（用户名固定为 `studio`）和 `WAN_STUDIO_SECRET_KEY`（例如 `python -c 'import secrets; print(secrets.token_hex(32))'`）。各进程使用同一个随机密钥，让重启后的任务仍可查询。不要把 Flask 开发服务器暴露到公网；代理层应限流，并避免记录任务查询路径或请求体。它没有多账号、长期视频存储或用户配额，不适合作为匿名公开生成服务。缓存图片、视频与签名密钥都放在 `state/`（可用 `WAN_STUDIO_STATE_DIR` 改路径），该目录不要提交到版本库。

## 测试

```sh
python -m unittest -v
```

测试模拟百炼响应，不发送照片，也不产生生成费用。
