"""arq worker: DB-backed task execution with lease sweep and media cleanup.

Run with: REDIS_URL=... DATABASE_URL=... ZHIPU_API_KEY 或 DASHSCOPE_API_KEY ... MEDIA_S3_*... arq app.worker.WorkerSettings
The DB tasks table is the source of truth; the sweep dispatches pending tasks and
reclaims expired leases every 5 seconds, so a Redis outage never loses a task.
"""
import asyncio
import os
from datetime import timedelta
from pathlib import Path

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import create_engine, delete, or_, select
from sqlalchemy.orm import Session

from app.services.advice import analyze_screening, apply_template_fallback, load_advice_templates, record_llm_failure
from app.services.audio import interpret_audio
from app.core.env import load_env
from app.core.security import utcnow
from app.services.chat import load_chat_config
from app.services.avatars import S3Assets, generate_avatar
from app.services.gif import export_gif
from app.services.motion import generate_motion, load_motion_config
from app.providers.motion import provider_from_env
from app.services.idempotency import IDEMPOTENCY_TTL
from app.models import MediaUpload, QuotaDenial, RequestIdempotency, Task
from app.core.observability import QUOTA_DENIAL_RETENTION_DAYS, log_task_event
from app.services.privacy import purge_due
from app.services.tasks import MAX_ATTEMPTS, claim_task, finish_task
from app.services.triage import load_rules

load_env()

MEDIA_CLEANUP_GRACE = timedelta(hours=1)


def _claim(engine, task_id):
    with Session(engine) as db, db.begin():
        return claim_task(db, task_id)


def _finish(engine, task_id, attempt, result):
    with Session(engine) as db, db.begin():
        return finish_task(db, task_id, attempt, result)


def _load(engine, task_id):
    with Session(engine) as db:
        task = db.get(Task, task_id)
        media = db.get(MediaUpload, task.payload.get('media_id')) if task.type in ('avatar_batch', 'motion_batch') else None
        return task, media


async def run_task(ctx, task_id):
    attempt = await asyncio.to_thread(_claim, ctx['engine'], task_id)
    if attempt is None:
        return
    task = None
    try:
        task, media = await asyncio.to_thread(_load, ctx['engine'], task_id)
        log_task_event('claimed', task_id=task.id, task_type=task.type, attempts=attempt)
        if task.type in ('avatar_batch', 'motion_batch') and (
                ctx['image_provider'] is None or ctx['video_provider'] is None):
            raise RuntimeError('该任务需要 DASHSCOPE_API_KEY（头像/视频生成暂无智谱实现）')
        if task.type == 'avatar_batch':
            result = await generate_avatar(task, media, assets=ctx['assets'],
                                           image_provider=ctx['image_provider'], video_provider=ctx['video_provider'])
        elif task.type == 'screening_analysis':
            result = await analyze_screening(ctx['engine'], task, llm_provider=ctx['llm_provider'],
                                             templates=ctx['advice_templates'], rules=ctx.get('triage_rules'))
        elif task.type == 'audio_interpret':
            result = await interpret_audio(ctx['engine'], task, audio_provider=ctx.get('audio_provider'),
                                           llm_provider=ctx['llm_provider'], assets=ctx['assets'],
                                           chat_config=ctx['chat_config'])
        elif task.type == 'gif_export':
            result = await export_gif(task, assets=ctx['assets'])
        elif task.type == 'motion_batch':
            result = await generate_motion(task, media, config=ctx['motion_config'], assets=ctx['assets'],
                                           provider=ctx['motion_provider'])
        else:
            raise ValueError(f'unsupported task type: {task.type}')
    except Exception:
        if task is not None and task.type == 'screening_analysis':
            await asyncio.to_thread(record_llm_failure, ctx['engine'], task_id, ctx['llm_provider'])
            if attempt >= MAX_ATTEMPTS:
                await asyncio.to_thread(apply_template_fallback, ctx['engine'], task_id, ctx['advice_templates'])
        await asyncio.to_thread(_finish, ctx['engine'], task_id, attempt, None)
        log_task_event('failed', task_id=task_id, task_type=task.type if task else 'unknown',
                       attempts=attempt, status='failed' if attempt >= MAX_ATTEMPTS else 'retry')
        return
    await asyncio.to_thread(_finish, ctx['engine'], task_id, attempt, result)
    log_task_event('finished', task_id=task_id, task_type=task.type, attempts=attempt, status='succeeded')


def _sweep_db(engine):
    now = utcnow()
    with Session(engine) as db, db.begin():
        rows = db.scalars(select(Task).where(or_(
            Task.status == 'pending',
            (Task.status == 'running') & (Task.lease_until <= now),
        ))).all()
        dispatch = []
        for task in rows:
            if task.status == 'running' and task.attempts >= MAX_ATTEMPTS:
                claim_task(db, task.id, now=now)  # exhausted lease -> failed
            else:
                dispatch.append(task.id)
        db.execute(delete(MediaUpload).where(MediaUpload.version_id.is_(None),
                                             MediaUpload.expires_at <= now - MEDIA_CLEANUP_GRACE))
        db.execute(delete(RequestIdempotency).where(RequestIdempotency.created_at <= now - IDEMPOTENCY_TTL))
        db.execute(delete(QuotaDenial).where(
            QuotaDenial.created_at <= now - timedelta(days=QUOTA_DENIAL_RETENTION_DAYS)))
        return dispatch


async def sweep(ctx):
    task_ids = await asyncio.to_thread(_sweep_db, ctx['engine'])
    for task_id in task_ids:
        await ctx['redis'].enqueue_job('run_task', task_id, _job_id=f'rongxing-task:{task_id}')


async def purge_sweep(ctx):
    # F12-4: physically purge accounts whose 15-day grace period elapsed.
    await asyncio.to_thread(purge_due, ctx['engine'], storage=ctx.get('assets'), now=utcnow())


def _providers_from_env():
    """文本 LLM 双源：智谱 glm-4-flash 优先（免费），未配再走 DashScope。

    头像/视频生成只有 DashScope 实现，未配 Key 时对应任务会显式失败，
    但不再阻断 worker 启动（初筛/音频解读不依赖它们）。
    """
    from app.providers.dashscope import DashScopeImageProvider, DashScopeLLMProvider, DashScopeVideoProvider
    from app.providers.zhipu import ZhipuLLMProvider
    dashscope_key = os.environ.get('DASHSCOPE_API_KEY', '')
    zhipu_key = os.environ.get('ZHIPU_API_KEY', '')
    image_provider = DashScopeImageProvider(dashscope_key) if dashscope_key else None
    video_provider = DashScopeVideoProvider(dashscope_key) if dashscope_key else None
    llm_provider = (ZhipuLLMProvider(zhipu_key) if zhipu_key
                    else DashScopeLLMProvider(dashscope_key) if dashscope_key else None)
    if llm_provider is None:
        raise RuntimeError('ZHIPU_API_KEY 或 DASHSCOPE_API_KEY 至少配置一个，否则初筛建议只能降级')
    return image_provider, video_provider, llm_provider


async def startup(ctx):
    ctx['engine'] = create_engine(os.environ['DATABASE_URL'], hide_parameters=True,
                                  pool_pre_ping=True, pool_recycle=1800)
    assets = S3Assets.from_env()
    if assets is None:
        raise RuntimeError('MEDIA_S3_* settings are required for the worker')
    ctx['assets'] = assets
    ctx['image_provider'], ctx['video_provider'], ctx['llm_provider'] = _providers_from_env()
    rules_dir = Path(os.environ.get('RULES_DIR') or Path(__file__).resolve().parents[1] / 'rules')
    templates = load_advice_templates(rules_dir)
    if templates is None:
        raise RuntimeError('advice_templates.yaml is required for screening fallback')
    ctx['advice_templates'] = templates
    ctx['triage_rules'] = load_rules(rules_dir)
    ctx['motion_config'] = load_motion_config(rules_dir)
    if ctx['motion_config'] is None:
        raise RuntimeError('motion.yaml is required for motion generation')
    ctx['motion_provider'] = provider_from_env()  # F14-6：默认本地占位，接真模型只换此处
    chat_config = load_chat_config(rules_dir)
    if chat_config is None:
        raise RuntimeError('chat.yaml is required for audio interpretation')
    ctx['chat_config'] = chat_config
    from app.providers.dashscope import DashScopeAudioProvider
    dashscope_key = os.environ.get('DASHSCOPE_API_KEY', '')
    ctx['audio_provider'] = DashScopeAudioProvider(dashscope_key) if dashscope_key else None


async def shutdown(ctx):
    ctx['engine'].dispose()


class WorkerSettings:
    functions = [run_task]
    cron_jobs = [cron(sweep, second=set(range(0, 60, 5))), cron(purge_sweep, second={0})]
    on_startup = startup
    on_shutdown = shutdown
    max_tries = 1  # the retry budget lives in the DB attempts column
    max_jobs = 2  # FRD 内测门槛：100 宠并发不雪崩，worker 并发限 2（FRD.md:357）
    keep_result = 3600
    redis_settings = RedisSettings.from_dsn(os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379'))
