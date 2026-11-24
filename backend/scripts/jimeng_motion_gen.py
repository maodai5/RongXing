"""即梦离线动图生成（FRD F14）：把萌系底图交给即梦出动作视频，再压成 motion.yaml 规格的 GIF。

链路：jimeng-novel generate-video（首帧图 + 动作提示词）→ mp4 → ffmpeg 抽帧 → Pillow 合成 GIF。
产物对齐 scripts/seed_motion_assets.py：--out 下写 base.png 与 <action>.gif，可直接入库。
只依赖 Pillow + PyYAML，不加载后端运行时，因此不需要可用的 backend/.venv。

先算价再花钱：不带 --yes 只打印 credit estimate 报价并退出，确认后才提交。

Usage (from backend/):
    python -m scripts.jimeng_motion_gen --dry-run
    python -m scripts.jimeng_motion_gen --actions idle,happy
    python -m scripts.jimeng_motion_gen --actions idle,happy --yes
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from PIL import Image

BACKEND_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = BACKEND_ROOT / 'rules'

# F14-6：项目定版提示词，逐字保留；只把 <从动作表选一条> 换成 motion.yaml 的动作句。
PROMPT_TEMPLATE = (
    '以首帧为唯一外观基准，生成 1–5 秒动作视频：\n\n'
    '- 首帧即最终形象：毛色、花纹、五官、体态与首帧完全一致，不重新生成外貌\n'
    '- 镜头不移动，不推拉摇移旋转\n'
    '- 宠物整体保持在画面中，居中占约 75%，不走出画面、不整体位移\n'
    '- 背景保持纯色 #FFEDD7\n'
    '- 动作幅度小、节奏慢、写实，不夸张、不大开大合\n'
    '本次动作：{action}\n'
    '禁止：换色、变形、换背景、镜头运动、文字、水印。'
)


def log(message: str) -> None:
    print(message, flush=True)


def load_motion_config() -> dict:
    """读 rules/motion.yaml；只做生成脚本必需的最小校验，完整校验仍在服务端。"""
    path = RULES_DIR / 'motion.yaml'
    if not path.is_file():
        raise SystemExit(f'missing {path}')
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    for key in ('version', 'actions', 'prompts', 'max_seconds', 'height', 'fps', 'min_ready'):
        if key not in config:
            raise SystemExit(f'motion.yaml missing {key}')
    return config


def check_gif(data: bytes, *, max_seconds: float, max_height: int) -> dict:
    """F14：优先复用 app.services.motion.verify_gif，后端依赖不可用时按同一契约本地校验。"""
    try:
        from app.services.motion import verify_gif  # noqa: PLC0415 - 可选依赖，尽力复用线上契约
    except Exception:  # noqa: BLE001 - .venv 缺失/依赖不全时退化，不阻断离线出图
        verify_gif = None
    if verify_gif is not None:
        return verify_gif(data, max_seconds=max_seconds, max_height=max_height)

    image = Image.open(io.BytesIO(data))
    if image.format != 'GIF':
        raise ValueError(f'expected GIF, got {image.format}')
    frames = getattr(image, 'n_frames', 1)
    duration = 0.0
    for index in range(frames):
        image.seek(index)
        duration += float(image.info.get('duration', 0)) / 1000
    if duration <= 0:
        raise ValueError('GIF has no frame duration')
    if duration > max_seconds + 0.1:
        raise ValueError(f'GIF duration {duration:.2f}s exceeds {max_seconds}s')
    width, height = image.size
    if height > max_height:
        raise ValueError(f'GIF height {height} exceeds {max_height}')
    if image.info.get('loop') != 0:
        raise ValueError('GIF must loop forever')
    return {'width': width, 'height': height, 'frames': frames,
            'duration_s': round(duration, 2), 'bytes': len(data)}


def resolve_cli(explicit: str | None) -> Path:
    """找 jimeng-novel：显式参数 > 环境变量 > PATH > 安装器默认 releases 目录。"""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_cli = os.environ.get('JIMENG_NOVEL_CLI')
    if env_cli:
        candidates.append(Path(env_cli))
    found = shutil.which('jimeng-novel')
    if found:
        candidates.append(Path(found))
    releases = Path.home() / '.local' / 'share' / 'jimeng-novel' / 'releases'
    if releases.is_dir():
        candidates.extend(sorted(releases.glob('*/jimeng-novel.exe'), reverse=True))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit('no jimeng-novel: 传 --cli，或设 JIMENG_NOVEL_CLI，或先跑安装器')


def resolve_ffmpeg(explicit: str | None) -> str:
    """找 ffmpeg：显式参数 > JIMENG_FFMPEG > PATH > 本机常见安装位置。"""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env_ffmpeg = os.environ.get('JIMENG_FFMPEG')
    if env_ffmpeg:
        candidates.append(env_ffmpeg)
    found = shutil.which('ffmpeg')
    if found:
        candidates.append(found)
    candidates.extend([
        r'D:\Trae CN\resources\app\bin\ffmpeg.exe',
        r'D:\传输\D盘数据\Apps\JianyingPro\11.6.0.14552\ffmpeg.exe',
    ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit('no ffmpeg: 传 --ffmpeg，或设 JIMENG_FFMPEG，或装 ffmpeg 到 PATH')


def run_cli(cli: Path, args: list[str], *, timeout: int = 1800) -> dict:
    """调 CLI 并解析 JSON 信封；stderr 原样透出，便于定位权限/登录问题。"""
    command = [str(cli), *args, '-j', '--full']
    log('  $ ' + ' '.join(f'"{part}"' if ' ' in part else part for part in command))
    completed = subprocess.run(command, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=timeout)
    payload = None
    for line in (completed.stdout or '').splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
    if payload is None:
        raise SystemExit(f'CLI 没有返回 JSON：rc={completed.returncode} '
                         f'stdout={completed.stdout[-800:]} stderr={completed.stderr[-800:]}')
    return payload


def quote(cli: Path, model: str, duration: int, resolution: str, count: int) -> dict | None:
    """按模型参数报价；priceStatus=reference 说明只是参考价，实扣以结果为谁。"""
    payload = run_cli(cli, ['credit', 'estimate', '--model-id', model,
                            '--duration', str(duration), '--resolution', resolution])
    items = (payload.get('data') or {}).get('items') or []
    if not items:
        return None
    item = items[0]
    return {'model': model, 'per_clip': item.get('estimatedCredits'),
            'price_status': item.get('priceStatus'), 'count': count,
            'total': (item.get('estimatedCredits') or 0) * count}


BADGE_BOX = (0.0, 0.015, 0.255, 0.108)   # 样片左上角「AI生成」角标，按画面宽高比例
BADGE_SOURCE_GAP = 3                     # 回填取自框下方多少像素处，保证取到纯色背景
BADGE_FEATHER = 4                        # 边缘羽化像素，避免出现矩形接缝


def _feather_mask(size: tuple[int, int], feather: int) -> Image.Image:
    width, height = size
    mask = Image.new('L', size, 255)
    pixels = mask.load()
    for y in range(height):
        for x in range(width):
            distance = min(x, width - 1 - x, y, height - 1 - y)
            if distance < feather:
                pixels[x, y] = int(255 * distance / max(1, feather))
    return mask


def erase_badge(frames: list[Image.Image], *, box: tuple[float, float, float, float] = BADGE_BOX,
                gap: int = BADGE_SOURCE_GAP, feather: int = BADGE_FEATHER) -> int:
    """擦除即梦样片左上角的「AI生成」角标。

    角标为固定叠加层，框内是纯色背景，把框下方同尺寸的背景块平移上来覆盖，边缘羽化。
    返回被覆盖的像素数。
    """
    width, height = frames[0].size
    left, top = int(width * box[0]), int(height * box[1])
    right, bottom = int(width * box[2]), int(height * box[3])
    block_width, block_height = right - left, bottom - top
    if block_width <= 0 or block_height <= 0:
        return 0
    source_top = min(height - block_height, bottom + gap)
    mask = _feather_mask((block_width, block_height), feather)
    for frame in frames:
        patch = frame.crop((left, source_top, right, source_top + block_height))
        frame.paste(patch, (left, top), mask)
    return block_width * block_height * len(frames)


def to_gif(mp4: Path, gif: Path, ffmpeg: str, *, fps: int, height: int, max_seconds: float,
           strip_badge: bool = True) -> dict:
    """mp4 → GIF：抽帧到临时目录，Pillow 按 motion.yaml 的 fps/高度合成，再过 check_gif。"""
    max_frames = int(max_seconds * fps)
    with tempfile.TemporaryDirectory(prefix='jimeng-gif-') as tmp:
        pattern = str(Path(tmp) / 'f_%04d.png')
        subprocess.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(mp4),
                        '-vf', f'fps={fps},scale=-2:{height}', '-frames:v', str(max_frames), pattern],
                       check=True)
        frames = [Image.open(path).convert('RGB') for path in sorted(Path(tmp).glob('f_*.png'))]
    if len(frames) < 2:
        raise ValueError(f'抽帧不足：{len(frames)}')
    stripped = erase_badge(frames) if strip_badge else 0
    frames[0].save(gif, 'GIF', save_all=True, append_images=frames[1:],
                   duration=round(1000 / fps), loop=0, optimize=False, disposal=2)
    spec = check_gif(gif.read_bytes(), max_seconds=max_seconds, max_height=height)
    spec['badge_pixels'] = stripped
    return spec


def generate_one(args, cli: Path, ffmpeg: str, config: dict, action: str) -> dict:
    """单个动作：生成视频 → 转 GIF → 校验；失败不抛，交给上层汇总。"""
    prompt = PROMPT_TEMPLATE.format(action=config['prompts'].get(action, action))
    with tempfile.TemporaryDirectory(prefix='jimeng-motion-') as tmp:
        command = ['generate-video', '--model', args.model, '--prompt', prompt,
                   '--image', str(args.base), '--duration', str(args.duration),
                   '--resolution', args.resolution, '--ratio', args.ratio,
                   '--wait', str(args.wait), '--out', str(Path(tmp)) + os.sep]
        if args.model.endswith('_draft'):
            # CLI 0.3.x 起样片模型强制要求 draft=true，缺参会报 draft model requires draft=true。
            command.append('--draft')
        if args.project_id:
            # F14：同一画布复用，避免一次批量在账号里建出十几个孤岛画布。
            command.extend(['--project-id', args.project_id])
        payload = run_cli(cli, command)
        # F14：--out 给目录时 CLI 按节点 key 命名，名字不可预测，只认目录里唯一的 mp4。
        clips = sorted(Path(tmp).glob('*.mp4'))
        if not clips:
            return {'status': 'failed', 'action': action,
                    'detail': failure_reason(cli, payload, args)}
        gif = Path(args.out) / f'{action}.gif'
        gif.parent.mkdir(parents=True, exist_ok=True)
        try:
            spec = to_gif(clips[0], gif, ffmpeg, fps=int(config['fps']), height=int(config['height']),
                          max_seconds=float(config['max_seconds']))
        except Exception as error:  # noqa: BLE001 - 单条失败不影响其余动作
            return {'status': 'failed', 'action': action, 'detail': str(error)}
    return {'status': 'ready', 'action': action, **spec}


def failure_reason(cli: Path, payload: dict, args) -> str:
    """没拿到产物时回查节点，把服务端的真实原因（如「仅对会员用户可用」）带出来。"""
    key = ((payload.get('data') or {}).get('key')) or ''
    if not key:
        return str((payload.get('error') or {}).get('message') or payload.get('status'))
    query = ['node', 'result', '--key', key]
    if args.project_id:
        query.extend(['--project-id', args.project_id])
    try:
        result = run_cli(cli, query)
    except SystemExit as error:
        return f'{error}（节点 {key}）'
    items = (result.get('data') or {}).get('items') or []
    if items and items[0].get('error_message'):
        return f'{items[0]["error_message"]}（节点 {key}）'
    return f'no output（节点 {key}）'


def main() -> int:
    config = load_motion_config()

    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default=str(BACKEND_ROOT / 'assets' / 'motion' / 'base.png'),
                        help='萌系底图（enchant 的产物）')
    parser.add_argument('--out', default=str(BACKEND_ROOT / 'assets' / 'motion'),
                        help='产物目录，对齐 seed_motion_assets')
    parser.add_argument('--actions', default=','.join(config['actions']), help='逗号分隔，默认 motion.yaml 全部')
    # Seedance_2.0_mini 报价最低但 firstFrame=false，传底图会被忽略，出不来原猫。
    # 链路要求首帧锁定身份，故默认取支持首帧且价格较低的一档。
    parser.add_argument('--model', default='Seedance_2.5_draft')
    parser.add_argument('--duration', type=int, default=5)
    parser.add_argument('--resolution', default='720p')
    parser.add_argument('--ratio', default='adaptive')
    parser.add_argument('--wait', type=int, default=900)
    parser.add_argument('--project-id', default=os.environ.get('JIMENG_PROJECT_ID'),
                        help='画布 ID；不传则每条新建画布，会污染账号，建议先 jimeng-novel init')
    parser.add_argument('--cli', default=None)
    parser.add_argument('--ffmpeg', default=None)
    parser.add_argument('--force', action='store_true', help='已存在的 GIF 也重做')
    parser.add_argument('--dry-run', action='store_true', help='只打印要跑的动作和提示词')
    parser.add_argument('--yes', action='store_true', help='确认报价后真正提交（花积分）')
    args = parser.parse_args()

    base = Path(args.base)
    actions = [item.strip() for item in args.actions.split(',') if item.strip()]
    unknown = [item for item in actions if item not in config['actions']]
    if unknown:
        raise SystemExit(f'unknown actions: {unknown}')
    if not base.is_file():
        raise SystemExit(f'base image missing: {base}（先跑 enchant 生成底图，或 --base 指定）')

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'base.png').resolve() != base.resolve():
        shutil.copyfile(base, out / 'base.png')

    todo = actions if args.force else [a for a in actions if not (out / f'{a}.gif').is_file()]
    log(f'base={base} out={out}')
    log(f'actions todo={len(todo)}/{len(actions)}: {",".join(todo) or "-"}')

    if args.dry_run:
        for action in todo:
            log(f'  [{action}] ' + PROMPT_TEMPLATE.format(action=config['prompts'].get(action, action)))
        return 0
    if not todo:
        log('nothing to do')
        return 0

    cli = resolve_cli(args.cli)
    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    estimate = quote(cli, args.model, args.duration, args.resolution, len(todo))
    if estimate:
        log(f'报价: {args.model} {args.duration}s {args.resolution} '
            f'{estimate["per_clip"]} 积分/条 × {len(todo)} 条 = {estimate["total"]} 积分'
            f'（priceStatus={estimate["price_status"]}）')
    if not args.yes:
        log('未加 --yes，停在报价。确认后再运行同一条命令加 --yes。')
        return 0

    results = []
    for action in todo:
        log(f'--- {action} ---')
        result = generate_one(args, cli, ffmpeg, config, action)
        results.append(result)
        if result['status'] == 'ready':
            log(f'  ok {result["width"]}x{result["height"]} {result["frames"]}f '
                f'{result["duration_s"]}s {result["bytes"] // 1024}KB')
        else:
            log(f'  failed: {result.get("detail")}')

    ready = sum(1 for item in results if item['status'] == 'ready')
    log(f'done ready={ready}/{len(todo)}（min_ready={config["min_ready"]}）')
    for item in results:
        if item['status'] != 'ready':
            log(f'  failed {item["action"]}: {item.get("detail")}')
    return 0 if ready >= int(config['min_ready']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
