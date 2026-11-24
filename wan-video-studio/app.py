import base64
import hmac
import io
import os
import re
import secrets
import time
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from flask import Flask, abort, render_template, request, send_file
from itsdangerous import BadSignature, URLSafeTimedSerializer
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import HTTPException

TOKEN_PLAN_ORIGIN = 'https://token-plan.maas.qianwenaiapi.com'
DEFAULT_API_BASE = f'{TOKEN_PLAN_ORIGIN}/api/v1'
DURATION = 3
MAX_IMAGE = 10 * 1024 * 1024
FORMATS = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp', 'BMP': 'image/bmp'}
CREAM = (254, 236, 212)  # 用户指定底色 #FEECD4，与预览舞台一致；透明图自动铺底后再作为首帧
ACTIONS = {
    'idle': ('待机', '自然慢眨一下', '保持原姿势和视线。若首帧睁眼，只缓慢眨眼一次；若本来闭眼，只表现轻微呼吸。'),
    'happy': ('开心', '轻快抬头', '轻快地将下巴抬高一点，像看到了熟悉的人。嘴巴自然闭合，身体不移动。'),
    'sad': ('低落', '轻轻垂下视线', '只将目光慢慢垂向前方地面，头部和身体保持原位，不流泪。'),
    'sick': ('不适', '安静地半眯眼', '眼睑缓缓垂下一点，像安静休息。保持姿势，不表现伤口、喘气或抽搐。'),
    'crouch': ('伏卧', '胸口轻轻放低', '若可见躯干和前爪，沿原姿势轻屈前肢，让胸口下沉一次；若已伏卧或画面是特写，只轻轻降低头部，不补画画外身体。'),
    'look_left': ('左看', '朝画面左侧看一眼', '目光与头部一起轻转向画面左侧（观众的左手边），幅度很小，身体不转动。'),
    'look_right': ('右看', '朝画面右侧看一眼', '目光与头部一起轻转向画面右侧（观众的右手边），幅度很小，身体不转动。'),
}
IDENTITY = (
    '以上传图为首帧，保持同一只宠物的物种、毛色、花纹、眼睛颜色及耳形。'
    '整体为卡通 2.5D 画风：圆润简化的形体、干净平涂与 2 层柔和分层阴影、成组蓬松绒毛块（不出现写实毛发丝），背景保持纯色 #FEECD4。'
    '保持首帧的构图、背景和主体位置，固定机位，不重构画外部位。开头短暂停住，中间只做一次小动作，结尾自然停稳。'
)
NEGATIVE = '换宠物，毛色或花纹漂移，五官变形，四肢增生，夸张拟人表情，写实毛发，塑料质感，粗黑描边，硬光，背景跳变，镜头移动或变焦，切镜头，画面闪烁。'
# 生图提示词：与工作台页面共用；以上传照片为参考图做图生图。
PORTRAIT_REFERENCE = (
    '以上传图同一只宠物为原型，保留它的物种、毛色、花纹、耳形、眼睛颜色与体型比例，重绘成一张完整的全身角色立绘：'
    '从头顶、耳朵到最后一只脚掌和尾巴尖，全部完整落在画面内，任何部位都不被画框裁切；'
    '参考图若只拍到半身或局部，请按同一只宠物的毛色与体型合理补全后腿、脚掌和尾巴，不要沿用参考图的裁切构图。'
)
PORTRAIT_BODY = (
    '画风：卡通 2.5D，带明显 3D 动画电影质感 —— 圆润立体的形体有清晰体积感与厚度，柔和的全局光照，暗部带轻微环境光遮蔽，边缘一道细软的轮廓光让主体从背景中浮出来；'
    '头略大、眼睛大而圆、口鼻小巧、四肢短圆；毛发概括成几组清晰的蓬松绒毛块，末端少量短毛尖，不画单根写实毛发丝；'
    '整体像高品质 3D 动画电影的角色设定渲染：材质干净、略带哑光绒感，保持卡通比例与简化细节；不要写实摄影感、不要毛绒玩偶实拍感、不要纯 2D 平涂插画。'
    '构图：正方形 1:1，全身完整入画，头顶、耳朵、四只脚掌与尾尖全部可见且不贴边；'
    '宠物为纵向站姿或坐姿（四脚着地、身体朝上延伸），不要横向趴卧、不要压扁身体；'
    '主体居中，高度约占画面 72%，四周留白均衡（上下各约 12%、左右各约 14%）；'
    '平视四分之三侧身，安静看向镜头，姿态自然放松。'
    '背景：纯色 #FEECD4（RGB 254, 236, 212），是明亮偏浅的奶油底色，比常见米色更亮更淡；'
    '完全平涂，无渐变、无暗角、无光斑、无地面线、无道具、无文字；主体脚下只留极淡的柔和接触阴影。'
    '光色：左上方柔和漫射主光，暖调浅阴影，整体明亮干净，主色不超过 4 种。'
)
PORTRAIT_NEGATIVE = '写实毛发，写实渲染，纯 2D 平涂，扁平贴纸感，无体积感，线稿风，毛绒玩偶摄影感，单根毛发丝，渐变背景，背景偏暗，暗角，复杂背景，地面反光，道具，文字，水印，多肢，断肢，尾巴或脚被裁切，横向趴卧，身体被压扁，半身像，胸部以上特写，只画头部和前胸，沿用参考图的裁切构图，四肢出框，尾巴出框，主体贴边，五官畸形，粗黑描边，硬光，镜头畸变，鱼眼，过曝。'
# 上游最小正方形是 512，实际按 1024 渲染再用 LANCZOS 缩到固定 480×480。
PORTRAIT_RENDER_SIZE = '1024*1024'
PORTRAIT_SIZE = 480
MAX_DESCRIPTION = 200
MAX_VIDEO = 200 * 1024 * 1024
VIDEO_TTL = 30 * 86400


def create_app(state_dir=None):
    app = Flask(__name__)
    host = os.getenv('WAN_STUDIO_HOST', '').strip()
    password = os.getenv('WAN_STUDIO_PASSWORD', '')
    if host and (not re.fullmatch(r'[a-zA-Z0-9.-]+', host) or not password):
        raise ValueError('WAN_STUDIO_HOST 必须是域名（不含协议或端口），且必须设置 WAN_STUDIO_PASSWORD。')
    base = (os.getenv('WAN_DASHSCOPE_BASE_URL', '').strip() or DEFAULT_API_BASE).rstrip('/')
    if base == f'{TOKEN_PLAN_ORIGIN}/compatible-mode/v1':
        base = DEFAULT_API_BASE
    if base != DEFAULT_API_BASE and not re.fullmatch(r'https://(?:dashscope(?:-intl)?\.aliyuncs\.com|[a-zA-Z0-9-]+\.(?:cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com)/api/v1', base):
        raise ValueError('WAN_DASHSCOPE_BASE_URL 必须是受信任的 HTTPS 基础地址：默认 Token Plan 地址或百炼 /api/v1 端点。')
    api_key = os.getenv('DASHSCOPE_API_KEY', '').strip()
    model = os.getenv('WAN_MODEL', '').strip()
    image_model = os.getenv('WAN_IMAGE_MODEL', '').strip()
    ready = bool(api_key and model)
    portrait_ready = bool(api_key and image_model)
    app.config.update(TRUSTED_HOSTS=['127.0.0.1', 'localhost', '[::1]'] + ([host] if host else []),
                      MAX_CONTENT_LENGTH=MAX_IMAGE + 64 * 1024, MAX_FORM_PARTS=4)
    state_dir = state_dir or os.getenv('WAN_STUDIO_STATE_DIR', '').strip() or os.path.join(app.root_path, 'state')
    videos = os.path.join(state_dir, 'videos')
    images = os.path.join(state_dir, 'images')
    os.makedirs(videos, exist_ok=True)
    os.makedirs(images, exist_ok=True)
    cutoff = time.time() - VIDEO_TTL
    for folder in (videos, images):
        for name in os.listdir(folder):
            stale = os.path.join(folder, name)
            try:
                if os.path.isfile(stale) and os.path.getmtime(stale) < cutoff:
                    os.remove(stale)
            except OSError:
                pass
    # 签名密钥落盘：重启后旧任务链接仍可查询、已缓存的视频仍能播放和下载。
    secret = os.getenv('WAN_STUDIO_SECRET_KEY', '').strip()
    if not secret:
        secret_path = os.path.join(state_dir, 'secret_key')
        try:
            with open(secret_path, encoding='utf-8') as saved:
                secret = saved.read().strip()
        except OSError:
            secret = ''
        if not secret:
            secret = secrets.token_hex(32)
            try:
                fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                with open(secret_path, encoding='utf-8') as saved:
                    secret = saved.read().strip()
            else:
                with os.fdopen(fd, 'w', encoding='utf-8') as saved:
                    saved.write(secret)
    signer = URLSafeTimedSerializer(secret, salt='wan-video-job')

    @app.before_request
    def authenticate():
        if password:
            auth = request.authorization
            if (not auth or auth.type != 'basic' or auth.username != 'studio'
                    or not hmac.compare_digest((auth.password or '').encode(), password.encode())):
                return '此工作台需要登录。', 401, {'WWW-Authenticate': 'Basic realm="Wan Video Studio", charset="UTF-8"'}

    @app.after_request
    def protect(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if request.endpoint in ('video', 'portrait_image'):
            response.headers['Cache-Control'] = 'private, max-age=3600'
        response.headers['Content-Security-Policy'] = ("default-src 'self'; img-src 'self' blob:; media-src 'self' https:; "
                                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.errorhandler(HTTPException)
    def error(exc):
        detail = {413: '上传过大，请选择不超过 10 MB 的图片。', 404: '任务不存在或查询凭据已过期。'}.get(exc.code, exc.description)
        return {'detail': detail}, exc.code

    def wan(method, path, async_task=True, timeout=40, **kwargs):
        if not ready:
            abort(503, '请在 .env 中设置 WAN_MODEL 和 DASHSCOPE_API_KEY，再重启工作台。')
        headers = {'Authorization': f'Bearer {api_key}'}
        if method == 'POST' and async_task:
            headers['X-DashScope-Async'] = 'enable'
        try:
            response = httpx.request(method, f'{base}/{path}', headers=headers, timeout=timeout, **kwargs)
        except httpx.RequestError:
            if method == 'POST':
                if async_task:
                    abort(504, '未收到百炼的提交确认，任务可能已受理；不要立即重复提交，请先到百炼控制台核对。')
                abort(504, '生图请求超时或网络中断，未收到结果；请勿连续重复提交，可稍后再试。')
            abort(504, '查询失败，未重新提交生成任务。可继续查询。')
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) or not (response.is_success and isinstance(data.get('output'), dict)):
            code = error_code(data) if isinstance(data, dict) else ''
            request_id = safe_label(data.get('request_id')) if isinstance(data, dict) else ''
            detail = f'百炼返回异常（{code or response.status_code}）' + (f'，请求 ID：{request_id}' if request_id else '')
            if response.status_code in (401, 403):
                detail += '。请检查 API Key、模型和端点权限是否匹配。'
            elif response.status_code in (404, 405):
                detail += '。请检查该 API 基础地址是否支持所需的生成与任务查询路径。'
            elif method == 'POST' and (response.is_success or response.status_code >= 500):
                detail += '。任务可能已受理；不要立即重复提交，请先到百炼控制台核对。'
            abort(502, detail)
        return data['output']

    def cache_video(task_id, url):
        """把成片保存到本地，返回路径；失败返回 None（页面回退到上游临时链接）。"""
        if not safe_label(task_id):
            return None
        path = os.path.join(videos, task_id + '.mp4')
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        part = path + '.part'
        try:
            with httpx.stream('GET', url, timeout=120, follow_redirects=False) as response:
                if response.status_code != 200 or not response.headers.get('Content-Type', '').startswith('video/'):
                    return None
                total = 0
                with open(part, 'wb') as output:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_VIDEO:
                            raise ValueError('视频超过 200 MB')
                        output.write(chunk)
            os.replace(part, path)
        except (httpx.RequestError, OSError, ValueError):
            try:
                os.remove(part)
            except OSError:
                pass
            return None
        return path

    @app.get('/')
    def index():
        return render_template('index.html', actions=ACTIONS, identity=IDENTITY, negative=NEGATIVE, ready=ready,
                               portrait_ready=portrait_ready, duration=DURATION,
                               portrait_prompt=PORTRAIT_REFERENCE + PORTRAIT_BODY, portrait_negative=PORTRAIT_NEGATIVE)

    @app.post('/api/jobs')
    def submit():
        if request.headers.get('X-Studio-Request') != '1' or request.headers.get('Sec-Fetch-Site') == 'cross-site':
            abort(403, '请从工作台页面提交任务。')
        action = request.form.get('action')
        if action not in ACTIONS:
            abort(400, '请选择一个有效的动作。')
        image = request.files.get('image')
        if not image or not image.filename:
            abort(400, '请先上传照片。')
        photo = image.read(MAX_IMAGE + 1)
        if len(photo) > MAX_IMAGE:
            abort(413)
        mime, photo = validate_photo(photo)
        result = wan('POST', 'services/aigc/video-generation/video-synthesis', json={
            'model': model,
            'input': {'prompt': IDENTITY + ACTIONS[action][2], 'negative_prompt': NEGATIVE,
                      'media': [{'type': 'first_frame', 'url': f'data:{mime};base64,' + base64.b64encode(photo).decode('ascii')}]},
            'parameters': {'resolution': '720P', 'duration': DURATION, 'prompt_extend': False, 'watermark': True},
        })
        task_id = safe_label(result.get('task_id'))
        if not task_id:
            abort(502, '未收到有效任务编号；不要立即重复提交，请先到百炼控制台核对。')
        return {'token': signer.dumps(task_id), 'task_id': task_id}, 202

    @app.post('/api/portrait')
    def portrait():
        if request.headers.get('X-Studio-Request') != '1' or request.headers.get('Sec-Fetch-Site') == 'cross-site':
            abort(403, '请从工作台页面提交任务。')
        if not portrait_ready:
            abort(503, '请在 .env 中设置 WAN_IMAGE_MODEL 和 DASHSCOPE_API_KEY，再重启工作台。')
        image = request.files.get('image')
        if not image or not image.filename:
            abort(400, '请先上传一张宠物照片，再生成 2.5D 形象。')
        photo = image.read(MAX_IMAGE + 1)
        if len(photo) > MAX_IMAGE:
            abort(413)
        mime, photo = validate_photo(photo)
        mime, photo = reference_image(mime, photo)
        description = (request.form.get('description') or '').strip()
        if len(description) > MAX_DESCRIPTION:
            abort(400, f'补充描述请控制在 {MAX_DESCRIPTION} 字以内。')
        prompt = PORTRAIT_REFERENCE + (f'用户补充：{description}。' if description else '') + PORTRAIT_BODY
        result = wan('POST', 'services/aigc/multimodal-generation/generation', async_task=False, timeout=180, json={
            'model': image_model,
            'input': {'messages': [{'role': 'user', 'content': [
                {'image': f'data:{mime};base64,' + base64.b64encode(photo).decode('ascii')},
                {'text': prompt}]}]},
            'parameters': {'size': PORTRAIT_RENDER_SIZE, 'n': 1, 'prompt_extend': False, 'watermark': True,
                           'negative_prompt': PORTRAIT_NEGATIVE},
        })
        url = ''
        for choice in result.get('choices') or []:
            content = choice.get('message', {}).get('content') if isinstance(choice, dict) else None
            for item in content if isinstance(content, list) else []:
                if isinstance(item, dict):
                    url = https_url(item.get('image'))
                    if url:
                        break
            if url:
                break
        if not url:
            abort(502, '生图完成，但未返回有效图片链接。')
        try:
            image = httpx.get(url, timeout=60, follow_redirects=False)
        except httpx.RequestError:
            abort(502, '生成的图片下载失败，请稍后重新生成。')
        if image.status_code != 200 or not image.headers.get('Content-Type', '').startswith('image/') or len(image.content) > MAX_IMAGE:
            abort(502, '生成服务返回的图片无效或过大，请重新生成。')
        try:
            _, photo = validate_photo(image.content)
        except HTTPException:
            abort(502, '生成的图片无法作为首帧使用，请重新生成。')
        photo = square_image(photo)
        if not photo:
            abort(502, '生成的图片无法作为首帧使用，请重新生成。')
        mime = 'image/png'
        image_id = secrets.token_hex(12)
        with open(os.path.join(images, image_id + '.png'), 'wb') as saved:
            saved.write(photo)
        return {'image': f'data:{mime};base64,' + base64.b64encode(photo).decode('ascii'), 'mime': mime,
                'image_id': image_id, 'view_url': f'/api/portraits/{image_id}/image',
                'download_url': f'/api/portraits/{image_id}/image?download=1'}

    @app.get('/api/portraits/<image_id>/image')
    def portrait_image(image_id):
        if not re.fullmatch(r'[0-9a-f]{24}', image_id):
            abort(404)
        path = os.path.join(images, image_id + '.png')
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, mimetype='image/png', as_attachment=request.args.get('download') == '1',
                         download_name='绒星-宠物形象.png', conditional=True, max_age=0)

    @app.get('/api/jobs/<token>')
    def poll(token):
        try:
            task_id = signer.loads(token, max_age=86400)
        except BadSignature:
            abort(404)
        result = wan('GET', f'tasks/{task_id}')
        status = result.get('task_status')
        if status in ('PENDING', 'RUNNING'):
            return {'status': status}
        if status == 'SUCCEEDED':
            url = https_url(result.get('video_url'))
            if url:
                if cache_video(task_id, url):
                    local = f'/api/jobs/{token}/video'
                    return {'status': status, 'video_url': local, 'download_url': local + '?download=1'}
                return {'status': status, 'video_url': url, 'download_url': url}
            abort(502, '任务已完成，但百炼未返回有效视频链接。')
        if status in ('FAILED', 'CANCELED', 'UNKNOWN'):
            detail = {'FAILED': '视频生成失败', 'CANCELED': '任务已取消', 'UNKNOWN': '任务不存在或已过期'}[status]
            code = error_code(result)
            return {'status': status, 'detail': detail + (f'（{code}）' if code else '') + '。'}
        abort(502, '百炼返回了未识别的任务状态，可稍后继续查询。')

    @app.get('/api/jobs/<token>/video')
    def video(token):
        try:
            task_id = signer.loads(token, max_age=VIDEO_TTL)
        except BadSignature:
            abort(404)
        if not safe_label(task_id):
            abort(404)
        path = os.path.join(videos, task_id + '.mp4')
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, mimetype='video/mp4', as_attachment=request.args.get('download') == '1',
                         download_name=f'绒星-宠物动作-{task_id[:8]}.mp4', conditional=True, max_age=0)

    return app


def validate_photo(photo):
    try:
        with Image.open(io.BytesIO(photo), formats=list(FORMATS)) as image:
            w, h = image.size
            if not (240 <= min(w, h) and max(w, h) <= 8000 and max(w, h) <= 8 * min(w, h)) or w * h > 32_000_000:
                abort(400, '图片宽高须在 240–8000 像素之间，比例在 1:8–8:1 之间，且不超过 3200 万像素。')
            if getattr(image, 'is_animated', False):
                abort(400, '请上传静态图片，不支持动图。')
            image.load()
            if 'A' not in image.getbands() and 'transparency' not in image.info:
                return FORMATS[image.format], photo
            rgba = image.convert('RGBA')
            flat = Image.new('RGB', rgba.size, CREAM)
            flat.paste(rgba, mask=rgba.getchannel('A'))
            for quality in (92, 85, 75):
                out = io.BytesIO()
                flat.save(out, 'JPEG', quality=quality)
                data = out.getvalue()
                if len(data) <= MAX_IMAGE:
                    return 'image/jpeg', data
            abort(400, '图片铺底后仍超过 10 MB，请换一张较小的图片。')
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        abort(400, '无法读取图片，请上传有效的 JPG、PNG、WebP 或 BMP。')


def reference_image(mime, photo):
    """参考图请求体过大时等比缩小到最长边 2048 px 的 JPEG，避免生图请求超限。"""
    if len(photo) <= 4 * 1024 * 1024:
        return mime, photo
    with Image.open(io.BytesIO(photo)) as image:
        image = image.convert('RGB')
        image.thumbnail((2048, 2048), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, 'JPEG', quality=90)
    return 'image/jpeg', out.getvalue()


def square_image(photo, edge=PORTRAIT_SIZE):
    """把生成图居中裁成正方形并缩到固定边长（默认 480），返回 PNG 字节；失败返回 None。"""
    try:
        with Image.open(io.BytesIO(photo)) as image:
            image = image.convert('RGB')
            side = min(image.size)
            left = (image.width - side) // 2
            top = (image.height - side) // 2
            image = image.crop((left, top, left + side, top + side))
            image = image.resize((edge, edge), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, 'PNG')
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    return output.getvalue()


def https_url(value):
    try:
        parsed = urlsplit(value) if isinstance(value, str) else None
        if parsed and parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ''


def safe_label(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', value) and value not in ('.', '..') else ''


def error_code(data):
    return safe_label(data.get('code'))


if __name__ == '__main__':
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
    create_app().run(host='127.0.0.1', port=5050)
