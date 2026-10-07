"""Environment and DSX endpoint configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()


def _strip(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


DSX_BASE_URL = _strip(os.environ.get("DSX_BASE_URL", ""))
_DEVOPS_SUFFIX = "/rest/devops/v1"
# The UI REST and Navigator URLs live on the same host as the devops API, so derive them
# from DSX_BASE_URL when they are not set explicitly in .env.
DSX_UI_BASE_URL = _strip(os.environ.get("DSX_UI_BASE_URL", "")) or (
    f"{DSX_BASE_URL[: -len(_DEVOPS_SUFFIX)]}/rest/ui/v1" if DSX_BASE_URL.endswith(_DEVOPS_SUFFIX) else ""
)
DSX_WEB_BASE_URL = _strip(os.environ.get("DSX_WEB_BASE_URL", "")) or (
    DSX_BASE_URL[: -len(_DEVOPS_SUFFIX)] if DSX_BASE_URL.endswith(_DEVOPS_SUFFIX) else ""
)
DSX_CREDENTIALS = os.environ.get("DSX_CREDENTIALS", "").strip()
DSX_CREATION_FORMS_PATH = os.environ.get("DSX_CREATION_FORMS_PATH", "").strip()

DSX_SERVICES_PATH = os.environ.get("DSX_SERVICES_PATH", "/brands").strip() or "/brands"
DSX_PRODUCT_SERVICES_PATH = os.environ.get("DSX_PRODUCT_SERVICES_PATH", "/services").strip() or "/services"
DSX_PROGRAMS_PATH = os.environ.get("DSX_PROGRAMS_PATH", "/programs").strip() or "/programs"
DSX_RELEASES_PATH = os.environ.get("DSX_RELEASES_PATH", "/releases/{program_id}").strip()
DSX_PROGRAMS_SERVICE_PARAM = os.environ.get("DSX_PROGRAMS_SERVICE_PARAM", "service").strip() or "service"
DSX_RELEASES_PROGRAM_PARAM = os.environ.get("DSX_RELEASES_PROGRAM_PARAM", "program").strip() or "program"
DSX_PARENT_BRAND_NAME = os.environ.get("DSX_PARENT_BRAND_NAME", "").strip()
DSX_PROBE_MAX_ATTEMPTS = max(1, int(os.environ.get("DSX_PROBE_MAX_ATTEMPTS", "12")))

IR_ALLOW_ENV_LOGIN = _bool("IR_ALLOW_ENV_LOGIN", bool(DSX_CREDENTIALS))


@lru_cache(maxsize=1)
def parsed_env_credentials() -> tuple[str, str]:
    if not DSX_CREDENTIALS or ":" not in DSX_CREDENTIALS:
        return "", ""
    user, password = DSX_CREDENTIALS.split(":", 1)
    return user.strip(), password.strip()


def configured_username() -> str:
    return parsed_env_credentials()[0]


def dsx_api_docs_url() -> str:
    if not DSX_BASE_URL:
        return ""
    return f"{DSX_BASE_URL}/api-docs"


def dsx_navigator_base() -> str:
    return DSX_WEB_BASE_URL


def navigator_url(physical_id: str) -> str:
    pid = str(physical_id or "").strip()
    if not pid or not DSX_WEB_BASE_URL:
        return ""
    return f"{DSX_WEB_BASE_URL}/common/emxNavigator.jsp?physicalId={pid}"


def release_hierarchy_configured() -> bool:
    return bool(
        DSX_BASE_URL
        and DSX_SERVICES_PATH
        and DSX_PRODUCT_SERVICES_PATH
        and DSX_PROGRAMS_PATH
        and DSX_RELEASES_PATH
    )


def dsx_ui_configured() -> bool:
    return bool(DSX_UI_BASE_URL)


def join_devops(path: str) -> str:
    path = path if path.startswith("/") else f"/{path}"
    return f"{DSX_BASE_URL}{path}"


def join_ui(path: str) -> str:
    path = path if path.startswith("/") else f"/{path}"
    return f"{DSX_UI_BASE_URL}{path}"


def ir_object_url(physical_id: str) -> str:
    url = navigator_url(physical_id)
    return url or ""


@dataclass(frozen=True)
class Settings:
    dsx_base_url: str
    dsx_ui_base_url: str
    dsx_web_base_url: str
    dsx_credentials: str
    dsx_creation_forms_path: str
    dsx_services_path: str
    dsx_product_services_path: str
    dsx_programs_path: str
    dsx_releases_path: str
    dsx_programs_service_param: str
    dsx_releases_program_param: str
    dsx_parent_brand_name: str
    dsx_probe_max_attempts: int
    ir_allow_env_login: bool
    cors_origins: str

    @property
    def dsx_configured(self) -> bool:
        return bool(self.dsx_base_url)

    @property
    def dsx_ui_configured(self) -> bool:
        return bool(self.dsx_ui_base_url)

    @property
    def release_hierarchy_configured(self) -> bool:
        return release_hierarchy_configured()

    @property
    def dsx_api_docs_url(self) -> str:
        return dsx_api_docs_url()

    @property
    def dsx_navigator_base(self) -> str:
        return dsx_navigator_base()

    @property
    def configured_username(self) -> str:
        return configured_username()

    @property
    def env_login_available(self) -> bool:
        user, pwd = parsed_env_credentials()
        return self.ir_allow_env_login and bool(user and pwd)

    def parse_credentials(self, username: str | None = None, password: str | None = None) -> tuple[str, str]:
        if username and password:
            return username.strip(), password
        user, pwd = parsed_env_credentials()
        if not user or not pwd:
            raise ValueError("DSX credentials are not configured")
        return user, pwd


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        dsx_base_url=DSX_BASE_URL,
        dsx_ui_base_url=DSX_UI_BASE_URL,
        dsx_web_base_url=DSX_WEB_BASE_URL,
        dsx_credentials=DSX_CREDENTIALS,
        dsx_creation_forms_path=DSX_CREATION_FORMS_PATH,
        dsx_services_path=DSX_SERVICES_PATH,
        dsx_product_services_path=DSX_PRODUCT_SERVICES_PATH,
        dsx_programs_path=DSX_PROGRAMS_PATH,
        dsx_releases_path=DSX_RELEASES_PATH,
        dsx_programs_service_param=DSX_PROGRAMS_SERVICE_PARAM,
        dsx_releases_program_param=DSX_RELEASES_PROGRAM_PARAM,
        dsx_parent_brand_name=DSX_PARENT_BRAND_NAME,
        dsx_probe_max_attempts=DSX_PROBE_MAX_ATTEMPTS,
        ir_allow_env_login=IR_ALLOW_ENV_LOGIN,
        cors_origins=os.environ.get("CORS_ORIGINS", "*").strip(),
    )


def health_payload() -> dict[str, Any]:
    user, _ = parsed_env_credentials()
    return {
        "ok": True,
        "status": "ok",
        "dsx_configured": bool(DSX_BASE_URL),
        "dsx_ui_configured": dsx_ui_configured(),
        "release_hierarchy_configured": release_hierarchy_configured(),
        "dsx_api_docs_url": dsx_api_docs_url(),
        "dsx_navigator_base": dsx_navigator_base(),
        "env_login_available": IR_ALLOW_ENV_LOGIN and bool(user),
        "configured_username": user or None,
    }
