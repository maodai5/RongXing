"""Environment-driven application settings.

Both the API process and the scripts read configuration through
:class:`Settings`, so validation lives in one place instead of inside the
application factory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from app.core.env import load_env
from app.providers.dashscope import DashScopeLLMProvider, DashScopeVisionProvider
from app.providers.zhipu import ZhipuLLMProvider, ZhipuVisionProvider
from app.services.advice import load_advice_templates
from app.services.chat import load_chat_config
from app.services.feeding import load_feeding_config
from app.services.media import PhotoStorage
from app.services.motion import load_motion_config
from app.services.placeholders import load_library
from app.services.rules import load_action_map
from app.services.triage import load_rules

BACKEND_ROOT = Path(__file__).resolve().parents[2]
POLICY_KINDS = ('privacy', 'terms')


@dataclass(frozen=True, eq=False)
class Settings:
    """Validated runtime configuration; construct with :meth:`from_env`."""

    database_url: str
    jwt_secret: str
    policy_versions: dict
    policy_links: dict
    photo_storage: Any
    action_map: Any
    triage_rules: Any
    advice_templates: Any
    feeding_config: Any
    chat_config: Any
    motion_config: Any
    chat_llm_provider: Any
    vision_provider: Any
    placeholder_library: Any

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> 'Settings':
        """Read and validate settings, raising RuntimeError on misconfiguration.

        When the process environment is used, ``.env`` is loaded first so every
        entry point (API, worker, scripts) sees the same configuration.
        """
        if environ is None:
            load_env()
            env = os.environ
        else:
            env = environ
        database_url = env.get('DATABASE_URL')
        if not database_url:
            raise RuntimeError('DATABASE_URL is required for local auth')
        jwt_secret = env.get('JWT_SECRET', '')
        if len(jwt_secret.encode('utf-8')) < 32:
            raise RuntimeError('JWT_SECRET must be at least 32 bytes')

        versions = {kind: env.get(f'{kind.upper()}_VERSION', '').strip() for kind in POLICY_KINDS}
        if any(len(version) > 32 for version in versions.values()):
            raise RuntimeError('Policy versions must be at most 32 characters')
        links = {kind: env.get(f'{kind.upper()}_URL', '').strip() for kind in POLICY_KINDS}
        for link in filter(None, links.values()):
            parsed = urlsplit(link)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None:
                raise RuntimeError('Policy URLs must be HTTPS links')

        rules_dir = Path(env.get('RULES_DIR') or BACKEND_ROOT / 'rules')
        placeholder_dir = Path(env.get('PLACEHOLDER_DIR') or BACKEND_ROOT / 'assets' / 'placeholders')
        dashscope_key = env.get('DASHSCOPE_API_KEY')
        zhipu_key = env.get('ZHIPU_API_KEY')
        # 视觉与聊天 provider 双源：智谱优先（glm-4-flash / glm-4v-flash 免费），未配再走 DashScope
        vision_provider = (ZhipuVisionProvider(zhipu_key) if zhipu_key
                           else DashScopeVisionProvider(dashscope_key) if dashscope_key else None)
        chat_llm_provider = (ZhipuLLMProvider(zhipu_key) if zhipu_key
                             else DashScopeLLMProvider(dashscope_key, model='qwen-plus') if dashscope_key else None)
        return cls(
            database_url=database_url,
            jwt_secret=jwt_secret,
            policy_versions=versions,
            policy_links=links,
            photo_storage=PhotoStorage.from_env(),
            action_map=load_action_map(rules_dir),
            triage_rules=load_rules(rules_dir),
            advice_templates=load_advice_templates(rules_dir),
            feeding_config=load_feeding_config(rules_dir),
            chat_config=load_chat_config(rules_dir),
            motion_config=load_motion_config(rules_dir),
            chat_llm_provider=chat_llm_provider,
            vision_provider=vision_provider,
            placeholder_library=load_library(placeholder_dir),
        )
