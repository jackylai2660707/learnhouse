import csv
import io
import json
import logging
import re
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Optional

import redis
from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import EmailStr, TypeAdapter
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization, OrganizationRead, OrganizationUser
from src.db.roles import Role, RoleRead
from src.db.user_organizations import UserOrganization
from src.db.usergroup_user import UserGroupUser
from src.db.usergroups import UserGroup, UserGroupRead
from src.db.users import AnonymousUser, APITokenUser, PublicUser, User, UserRead
from src.security.auth import resolve_acting_user_id
from src.security.features_utils.usage import decrease_feature_usage
from src.security.features_utils.usage import check_limits_with_usage, increase_feature_usage
from src.security.org_auth import is_org_member
from src.security.rbac.constants import ADMIN_ROLE_ID
from src.security.security import security_hash_password
from src.services.orgs.invites import send_invite_email
from src.services.orgs.orgs import get_org_default_language, rbac_check
from src.services.search.normalization import LIKE_ESCAPE_CHAR, build_like_pattern
from src.services.users.emails import send_role_changed_email
from src.services.webhooks.dispatch import dispatch_webhooks

logger = logging.getLogger(__name__)
DEFAULT_USER_ROLE_ID = 4
BULK_USER_CSV_COLUMNS = [
    "email",
    "username",
    "first_name",
    "last_name",
    "password",
    "role_uuid",
    "usergroups",
    "email_verified",
]
BULK_USER_REQUIRED_CSV_COLUMNS = [
    "email",
    "username",
    "first_name",
    "last_name",
    "password",
    "role_uuid",
    "email_verified",
]
BULK_USER_CSV_COLUMN_LABELS = {
    "email": "電郵(email)",
    "username": "用戶名(username)",
    "first_name": "名字(first_name)",
    "last_name": "姓氏(last_name)",
    "password": "密碼(password)",
    "role_uuid": "角色(role_uuid)",
    "usergroups": "班級/群組(usergroups)",
    "email_verified": "電郵已驗證(email_verified)",
}
BULK_USER_CSV_HEADER_ALIASES = {
    "電郵": "email",
    "電子郵件": "email",
    "邮箱": "email",
    "电子邮件": "email",
    "用戶名": "username",
    "用户名": "username",
    "帳號": "username",
    "账号": "username",
    "名字": "first_name",
    "名": "first_name",
    "姓氏": "last_name",
    "姓": "last_name",
    "密碼": "password",
    "密码": "password",
    "角色": "role_uuid",
    "身份": "role_uuid",
    "班級": "usergroups",
    "班级": "usergroups",
    "群組": "usergroups",
    "群组": "usergroups",
    "班級/群組": "usergroups",
    "班级/群组": "usergroups",
    "電郵已驗證": "email_verified",
    "邮箱已验证": "email_verified",
    "已驗證": "email_verified",
    "已验证": "email_verified",
}
SCHOOL_ROLE_ALIASES = {
    "student": "role_global_user",
    "learner": "role_global_user",
    "user": "role_global_user",
    "學生": "role_global_user",
    "学生": "role_global_user",
    "學員": "role_global_user",
    "学员": "role_global_user",
    "teacher": "role_global_instructor",
    "instructor": "role_global_instructor",
    "老師": "role_global_instructor",
    "老师": "role_global_instructor",
    "教師": "role_global_instructor",
    "教师": "role_global_instructor",
}
_email_adapter = TypeAdapter(EmailStr)


def _csv_bool(value: str | None, default: bool = True) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "是", "已驗證"}


def _username_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0]
    username = re.sub(r"[^a-zA-Z0-9_.-]", "_", local_part).strip("._-")
    return username or f"user_{uuid.uuid4().hex[:8]}"


def _school_role_value(value: str | None) -> str:
    raw = unicodedata.normalize("NFKC", value or "").strip()
    return SCHOOL_ROLE_ALIASES.get(raw.casefold(), SCHOOL_ROLE_ALIASES.get(raw, raw))


def _school_role_summary(value: str | None) -> tuple[str, str]:
    role_uuid = _school_role_value(value)
    if role_uuid in {"", "role_global_user"}:
        return "student", "學生"
    if role_uuid == "role_global_instructor":
        return "teacher", "老師"
    return "other", "其他角色"


def _csv_header_name(value: str | None) -> str | None:
    if value is None:
        return None
    raw = unicodedata.normalize("NFKC", value).strip()
    key = raw.casefold()
    if key in BULK_USER_CSV_COLUMNS:
        return key
    return BULK_USER_CSV_HEADER_ALIASES.get(key, raw)


def _csv_column_label(column: str) -> str:
    return BULK_USER_CSV_COLUMN_LABELS.get(column, column)


def _csv_usergroup_names(value: str | None) -> list[str]:
    if not value:
        return []
    names: list[str] = []
    seen_keys: set[str] = set()
    for item in re.split(r"[;；|、,，\n]+", value):
        name = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", item).strip())
        key = _csv_usergroup_key(name)
        if name and key not in seen_keys:
            names.append(name[:120])
            seen_keys.add(key)
    return names[:20]


def _csv_usergroup_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


def _validate_csv_initial_password(password: str) -> str | None:
    if len(password) < 8:
        return "密碼至少需要 8 個字符"
    return None


async def get_organization_users(
    request: Request,
    org_id: int,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    page: int = 1,
    limit: int = 20,
    search: str = "",
    usergroup_id: int | None = None,
    usergroup_filter: str | None = None,
    sort_order: str = "desc",
    role_id: int | None = None,
    status: str | None = None,
):
    """
    Get paginated list of users in an organization.

    SECURITY:
    - Requires authentication (enforced at router level)
    - User must be a member of the organization to view member list
    - Maximum limit enforced to prevent data dumping
    """
    # SECURITY: Enforce maximum limit
    limit = min(limit, 100)
    page = max(page, 1)

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    # SECURITY: Verify current user is a member of this organization
    # This prevents users from enumerating members of orgs they don't belong to
    if isinstance(current_user, AnonymousUser):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    acting_user_id = resolve_acting_user_id(current_user)

    # Membership check (superadmins bypass)
    if not await is_org_member(acting_user_id, org.id, db_session):
        raise HTTPException(
            status_code=403,
            detail="You must be a member of this organization to view its members",
        )

    # Only admins/maintainers can list organization members
    from src.security.superadmin import is_user_superadmin
    if not await is_user_superadmin(acting_user_id, db_session):
        from src.security.org_auth import is_org_admin
        if not await is_org_admin(acting_user_id, org.id, db_session):
            raise HTTPException(
                status_code=403,
                detail="Only administrators and maintainers can view organization members",
            )

    # Base query for users in the organization
    base_statement = (
        select(User)
        .join(UserOrganization)
        .join(Organization)
        .where(Organization.id == org_id)
    )

    # Apply search filter if provided
    if search:
        search_pattern = build_like_pattern(search)
        base_statement = base_statement.where(
            (User.first_name.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
            | (User.last_name.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
            | (User.username.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
            | (User.email.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
        )

    # Apply role filter
    if role_id is not None:
        base_statement = base_statement.where(UserOrganization.role_id == role_id)

    # Apply status filter (verified/unverified)
    if status == "verified":
        base_statement = base_statement.where(User.email_verified == True)  # noqa: E712
    elif status == "unverified":
        base_statement = base_statement.where(User.email_verified == False)  # noqa: E712

    # Compute group membership counts when usergroup_id is provided (before applying filter)
    in_group_total = None
    all_total = None
    if usergroup_id is not None:
        # Count all org users matching search (unfiltered) using SQL COUNT
        # Count user ids instead of wrapping the full User row in DISTINCT.
        # PostgreSQL cannot compare the legacy JSON fields on User.
        all_count_stmt = base_statement.with_only_columns(
            func.count(func.distinct(User.id))
        ).order_by(None)
        all_total = (await db_session.execute(all_count_stmt)).scalar_one()

        # Count in-group users matching search using SQL COUNT
        in_group_count_stmt = (
            select(func.count(func.distinct(User.id)))
            .join(UserOrganization)
            .join(Organization)
            .where(Organization.id == org_id)
            .join(UserGroupUser, (UserGroupUser.user_id == User.id) & (UserGroupUser.usergroup_id == usergroup_id))
        )
        if search:
            search_pattern = build_like_pattern(search)
            in_group_count_stmt = in_group_count_stmt.where(
                (User.first_name.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
                | (User.last_name.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
                | (User.username.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
                | (User.email.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
            )
        in_group_total = (await db_session.execute(in_group_count_stmt)).scalar_one()

    # Apply usergroup membership filter
    if usergroup_id is not None and usergroup_filter:
        membership_exists = (
            select(UserGroupUser.id)
            .where(
                UserGroupUser.user_id == User.id,
                UserGroupUser.usergroup_id == usergroup_id,
                UserGroupUser.org_id == org_id,
            )
            .exists()
        )
        if usergroup_filter == "in_group":
            base_statement = base_statement.where(membership_exists)
        elif usergroup_filter == "not_in_group":
            base_statement = base_statement.where(~membership_exists)

    # Only compare scalar ids. DISTINCT over the full User model fails on
    # PostgreSQL because details/profile use the non-comparable JSON type.
    total_statement = base_statement.with_only_columns(
        func.count(func.distinct(User.id))
    ).order_by(None)
    total = (await db_session.execute(total_statement)).scalar_one()

    # Sort by join date — use UserOrganization.id as it's auto-increment
    # and directly correlates with join order (creation_date is a str, unreliable for sorting)
    if sort_order == "asc":
        base_statement = base_statement.order_by(UserOrganization.id.asc())
    else:
        base_statement = base_statement.order_by(UserOrganization.id.desc())

    # Apply pagination
    offset = (page - 1) * limit
    paginated_statement = base_statement.offset(offset).limit(limit)
    users = (await db_session.execute(paginated_statement)).scalars().all()

    org_users_list = []

    if not users:
        pass
    else:
        user_ids = [user.id for user in users]

        # Batch fetch all UserOrganization records for these users
        user_orgs_statement = select(UserOrganization).where(
            UserOrganization.user_id.in_(user_ids),  # type: ignore
            UserOrganization.org_id == org_id
        )
        user_orgs = (await db_session.execute(user_orgs_statement)).scalars().all()
        user_org_map = {uo.user_id: uo for uo in user_orgs}

        # Batch fetch all roles needed
        role_ids = list({uo.role_id for uo in user_orgs if uo.role_id is not None})
        if role_ids:
            roles_statement = select(Role).where(Role.id.in_(role_ids))  # type: ignore
            roles = (await db_session.execute(roles_statement)).scalars().all()
            role_map = {role.id: role for role in roles}
        else:
            role_map = {}

        # Batch fetch all usergroups for these users in this org
        usergroups_statement = (
            select(UserGroupUser, UserGroup)
            .join(UserGroup, UserGroupUser.usergroup_id == UserGroup.id)  # type: ignore
            .where(
                UserGroupUser.user_id.in_(user_ids),  # type: ignore
                UserGroupUser.org_id == org_id
            )
        )
        usergroup_results = (await db_session.execute(usergroups_statement)).all()
        user_usergroups_map: dict[int, list[UserGroupRead]] = {}
        for ugu, ug in usergroup_results:
            existing = user_usergroups_map.setdefault(ugu.user_id, [])
            if all(group.id != ug.id for group in existing):
                existing.append(UserGroupRead.model_validate(ug))

        for user in users:
            user_org = user_org_map.get(user.id)
            if not user_org:
                logging.error(f"User {user.id} not found")
                continue

            role = role_map.get(user_org.role_id)
            if not role:
                logging.error(f"Role {user_org.role_id} not found")
                continue

            user_read = UserRead.model_validate(user)
            role_read = RoleRead.model_validate(role)
            usergroups = user_usergroups_map.get(user.id, [])

            org_user = OrganizationUser(
                user=user_read,
                role=role_read,
                usergroups=usergroups,
                joined_at=user_org.creation_date,
            )

            org_users_list.append(org_user)

    result = {
        "items": org_users_list,
        "total": total,
        "page": page,
        "limit": limit,
    }

    if in_group_total is not None:
        result["in_group_total"] = in_group_total
    if all_total is not None:
        result["all_total"] = all_total

    return result


async def export_organization_users_csv(
    request: Request,
    org_id: int,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
    search: str = "",
    usergroup_id: int | None = None,
    usergroup_filter: str | None = None,
    sort_order: str = "desc",
    role_id: int | None = None,
    status: str | None = None,
):
    """
    Export all organization users as CSV.
    Reuses the same auth/filtering logic as get_organization_users but without pagination.
    """
    if isinstance(current_user, AnonymousUser):
        raise HTTPException(status_code=401, detail="Authentication required")

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    export_acting_user_id = resolve_acting_user_id(current_user)
    if not await is_org_member(export_acting_user_id, org.id, db_session):
        raise HTTPException(status_code=403, detail="You must be a member of this organization")

    # Only admins/maintainers can export organization members
    from src.security.superadmin import is_user_superadmin
    if not await is_user_superadmin(export_acting_user_id, db_session):
        from src.security.org_auth import is_org_admin
        if not await is_org_admin(export_acting_user_id, org.id, db_session):
            raise HTTPException(
                status_code=403,
                detail="Only administrators and maintainers can export organization members",
            )

    base_statement = (
        select(User)
        .join(UserOrganization)
        .join(Organization)
        .where(Organization.id == org_id)
    )

    if search:
        search_pattern = build_like_pattern(search)
        base_statement = base_statement.where(
            (User.first_name.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
            | (User.last_name.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
            | (User.username.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
            | (User.email.ilike(search_pattern, escape=LIKE_ESCAPE_CHAR))
        )

    if role_id is not None:
        base_statement = base_statement.where(UserOrganization.role_id == role_id)

    if status == "verified":
        base_statement = base_statement.where(User.email_verified == True)  # noqa: E712
    elif status == "unverified":
        base_statement = base_statement.where(User.email_verified == False)  # noqa: E712

    if usergroup_id is not None and usergroup_filter:
        membership_exists = (
            select(UserGroupUser.id)
            .where(
                UserGroupUser.user_id == User.id,
                UserGroupUser.usergroup_id == usergroup_id,
                UserGroupUser.org_id == org_id,
            )
            .exists()
        )
        if usergroup_filter == "in_group":
            base_statement = base_statement.where(membership_exists)
        elif usergroup_filter == "not_in_group":
            base_statement = base_statement.where(~membership_exists)

    if sort_order == "asc":
        base_statement = base_statement.order_by(UserOrganization.id.asc())
    else:
        base_statement = base_statement.order_by(UserOrganization.id.desc())

    users = (await db_session.execute(base_statement)).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "姓名",
        "用戶名",
        "電郵",
        "班級/群組",
        "角色",
        "加入日期",
        "電郵已驗證",
        "註冊方式",
        "最後登入",
    ])

    if users:
        user_ids = [user.id for user in users]

        user_orgs = (await db_session.execute(
            select(UserOrganization).where(
                UserOrganization.user_id.in_(user_ids),  # type: ignore
                UserOrganization.org_id == org_id,
            )
        )).scalars().all()
        user_org_map = {uo.user_id: uo for uo in user_orgs}

        role_ids = list({uo.role_id for uo in user_orgs if uo.role_id is not None})
        role_map = {}
        if role_ids:
            roles = (await db_session.execute(select(Role).where(Role.id.in_(role_ids)))).scalars().all()  # type: ignore
            role_map = {role.id: role for role in roles}

        usergroup_results = (await db_session.execute(
            select(UserGroupUser, UserGroup)
            .join(UserGroup, UserGroupUser.usergroup_id == UserGroup.id)  # type: ignore
            .where(
                UserGroupUser.user_id.in_(user_ids),  # type: ignore
                UserGroupUser.org_id == org_id,
            )
        )).all()
        user_usergroups_map: dict[int, list[str]] = {}
        for ugu, ug in usergroup_results:
            groups = user_usergroups_map.setdefault(ugu.user_id, [])
            if ug.name not in groups:
                groups.append(ug.name)

        def fmt_date(d):
            if not d:
                return ""
            try:
                dt = datetime.fromisoformat(str(d)) if not isinstance(d, datetime) else d
                return dt.strftime("%Y-%m-%d")
            except Exception:
                return str(d)

        for user in users:
            user_org = user_org_map.get(user.id)
            if not user_org:
                continue
            role = role_map.get(user_org.role_id)
            groups = "; ".join(user_usergroups_map.get(user.id, []))

            writer.writerow([
                f"{user.first_name or ''} {user.last_name or ''}".strip(),
                user.username or "",
                user.email or "",
                groups,
                role.name if role else "",
                fmt_date(user_org.creation_date),
                "是" if user.email_verified else "否",
                user.signup_method or "",
                fmt_date(user.last_login_at) if hasattr(user, "last_login_at") else "",
            ])

    output.seek(0)
    return StreamingResponse(
        io.StringIO("\ufeff" + output.getvalue()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users-export.csv"},
    )


async def get_bulk_user_import_template_csv(
    request: Request,
    org_id: int,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
):
    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await rbac_check(request, org.org_uuid, current_user, "create", db_session)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["電郵", "用戶名", "名字", "姓氏", "密碼", "角色", "班級/群組", "電郵已驗證"])
    writer.writerow([
        "student001@example.com",
        "student001",
        "小明",
        "陳",
        "12345678",
        "student",
        "小四A班",
        "true",
    ])
    writer.writerow([
        "teacher001@example.com",
        "teacher001",
        "老師",
        "王",
        "12345678",
        "teacher",
        "小四A班;小五B班",
        "true",
    ])
    output.seek(0)
    return StreamingResponse(
        io.StringIO("\ufeff" + output.getvalue()),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=learnhouse-users-import-template.csv"
        },
    )


async def bulk_create_organization_users_from_csv(
    request: Request,
    org_id: int,
    file: UploadFile,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
):
    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await rbac_check(request, org.org_uuid, current_user, "create", db_session)

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="請上傳 CSV 檔案。")

    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV 檔案太大，請控制在 2MB 以內。")

    try:
        csv_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV 必須使用 UTF-8 編碼。")

    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV 是空檔案，請先下載模板後再填寫。")

    normalized_headers = [_csv_header_name(header) for header in reader.fieldnames]
    missing_headers = [
        column for column in BULK_USER_REQUIRED_CSV_COLUMNS if column not in normalized_headers
    ]
    if missing_headers:
        missing_header_labels = [_csv_column_label(column) for column in missing_headers]
        raise HTTPException(
            status_code=400,
            detail=f"CSV 缺少必填欄位：{', '.join(missing_header_labels)}。請下載最新模板後再匯入。",
        )

    numbered_rows = [
        (index, row)
        for index, row in enumerate(reader, start=2)
        if any(str(value or "").strip() for value in row.values())
    ]
    rows = [row for _, row in numbered_rows]
    if len(rows) > 500:
        raise HTTPException(status_code=400, detail="CSV 一次最多匯入 500 個帳號，請分批上傳。")

    seen_emails: set[str] = set()
    seen_usernames: set[str] = set()
    results = []
    created_count = 0
    failed_count = 0
    role_cache: dict[str, int | None] = {}

    usergroup_cache: dict[str, UserGroup] = {}
    usergroups_loaded = False

    async def resolve_role_id(role_uuid: str | None) -> tuple[int | None, str | None]:
        role_uuid = _school_role_value(role_uuid)
        if not role_uuid:
            return DEFAULT_USER_ROLE_ID, None
        if role_uuid == "role_global_user":
            return DEFAULT_USER_ROLE_ID, None

        if role_uuid in role_cache:
            role_id = role_cache[role_uuid]
            return role_id, None if role_id else "找不到角色"

        role_statement = select(Role).where(Role.role_uuid == role_uuid)
        role = (await db_session.execute(role_statement)).scalars().first()
        if not role or role.id is None or (
            role.org_id is not None and role.org_id != org.id
        ):
            role_cache[role_uuid] = None
            return None, "找不到角色"

        role_cache[role_uuid] = role.id
        return role.id, None

    async def get_or_create_usergroup(name: str) -> UserGroup:
        nonlocal usergroups_loaded
        if not usergroups_loaded:
            existing_usergroups = (
                await db_session.execute(
                    select(UserGroup).where(UserGroup.org_id == org.id)
                )
            ).scalars().all()
            for existing_usergroup in existing_usergroups:
                usergroup_cache.setdefault(
                    _csv_usergroup_key(existing_usergroup.name),
                    existing_usergroup,
                )
            usergroups_loaded = True

        cache_key = _csv_usergroup_key(name)
        if cache_key in usergroup_cache:
            return usergroup_cache[cache_key]

        now = str(datetime.now())
        usergroup = UserGroup(
            org_id=org.id,
            name=name,
            description="CSV 批量匯入自動建立的班級/群組",
            usergroup_uuid=f"usergroup_{uuid.uuid4()}",
            creation_date=now,
            update_date=now,
        )
        db_session.add(usergroup)
        await db_session.flush()

        usergroup_cache[cache_key] = usergroup
        return usergroup

    for index, row in numbered_rows:
        normalized_row = {
            _csv_header_name(key): (value.strip() if isinstance(value, str) else value)
            for key, value in row.items()
        }
        email_raw = normalized_row.get("email") or ""
        username = normalized_row.get("username") or ""
        first_name = normalized_row.get("first_name") or ""
        last_name = normalized_row.get("last_name") or ""
        password = normalized_row.get("password") or ""
        role_uuid = normalized_row.get("role_uuid") or ""
        usergroup_names = _csv_usergroup_names(normalized_row.get("usergroups"))
        email_verified = _csv_bool(normalized_row.get("email_verified"), True)
        resolved_role_uuid = _school_role_value(role_uuid)
        role_summary_key, role_summary_label = _school_role_summary(resolved_role_uuid)

        row_errors = []

        if resolved_role_uuid in {"", "role_global_user"} and not usergroup_names:
            row_errors.append("學生帳號必須填寫班級/群組")

        try:
            email = str(_email_adapter.validate_python(email_raw)).lower()
        except Exception:
            email = email_raw.lower()
            row_errors.append("電郵格式不正確")

        if not username:
            username = _username_from_email(email)

        if not password:
            row_errors.append("密碼必填")
        else:
            password_error = _validate_csv_initial_password(password)
            if password_error:
                row_errors.append(password_error)

        email_key = email.lower()
        username_key = username.lower()
        if email_key in seen_emails:
            row_errors.append("CSV 內有重複電郵")
        if username_key in seen_usernames:
            row_errors.append("CSV 內有重複用戶名")

        if not row_errors:
            conflict = (await db_session.execute(
                select(User).where(
                    (User.email == email) | (User.username == username)
                )
            )).scalars().first()
            if conflict:
                row_errors.append("電郵或用戶名已被使用")

        role_id = DEFAULT_USER_ROLE_ID
        if not row_errors:
            resolved_role_id, role_error = await resolve_role_id(resolved_role_uuid)
            if role_error or resolved_role_id is None:
                row_errors.append(role_error or "找不到角色")
            else:
                role_id = resolved_role_id

        if not row_errors:
            try:
                await check_limits_with_usage("members", org.id, db_session)
            except HTTPException as exc:
                row_errors.append(str(exc.detail))

        if row_errors:
            failed_count += 1
            results.append({
                "line": index,
                "email": email_raw,
                "username": username,
                "status": "failed",
                "errors": row_errors,
                "role": role_summary_key,
                "role_label": role_summary_label,
                "usergroups": usergroup_names,
            })
            seen_emails.add(email_key)
            seen_usernames.add(username_key)
            continue

        now = datetime.now()
        user = User(
            username=username,
            first_name=first_name,
            last_name=last_name,
            email=email,
            password=security_hash_password(password),
            user_uuid=f"user_{uuid.uuid4()}",
            email_verified=email_verified,
            email_verified_at=(
                datetime.now().isoformat() if email_verified else None
            ),
            signup_method="csv_import",
            creation_date=str(now),
            update_date=str(now),
        )

        try:
            db_session.add(user)
            await db_session.flush()

            user_org = UserOrganization(
                user_id=user.id if user.id else 0,
                org_id=org.id,
                role_id=role_id,
                creation_date=str(now),
                update_date=str(now),
            )
            db_session.add(user_org)

            added_usergroups = []
            for usergroup_name in usergroup_names:
                usergroup = await get_or_create_usergroup(usergroup_name)
                if usergroup.id is None:
                    continue
                db_session.add(
                    UserGroupUser(
                        usergroup_id=usergroup.id,
                        user_id=user.id if user.id else 0,
                        org_id=org.id,
                        creation_date=str(datetime.now()),
                        update_date=str(datetime.now()),
                    )
                )
                added_usergroups.append(usergroup.name)

            await db_session.commit()
            try:
                await increase_feature_usage("members", org.id, db_session)
            except Exception:
                logger.warning(
                    "Failed to increase member usage after CSV import for org_id=%s user_id=%s",
                    org.id,
                    user.id,
                    exc_info=True,
                )

            created_count += 1
            results.append({
                "line": index,
                "email": email,
                "username": username,
                "status": "created",
                "errors": [],
                "role": role_summary_key,
                "role_label": role_summary_label,
                "usergroups": added_usergroups,
            })
        except Exception:
            await db_session.rollback()
            usergroup_cache.clear()
            usergroups_loaded = False
            failed_count += 1
            logger.exception("Failed to import CSV user on line %s", index)
            results.append({
                "line": index,
                "email": email,
                "username": username,
                "status": "failed",
                "errors": ["建立帳號失敗，請檢查資料後再試"],
                "role": role_summary_key,
                "role_label": role_summary_label,
                "usergroups": usergroup_names,
            })

        seen_emails.add(email_key)
        seen_usernames.add(username_key)

    return {
        "detail": "CSV import completed",
        "summary": {
            "total": len(rows),
            "created": created_count,
            "failed": failed_count,
        },
        "results": results,
    }


async def remove_user_from_org(
    request: Request,
    org_id: int,
    user_id: int,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
):
    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    # RBAC check
    await rbac_check(request, org.org_uuid, current_user, "delete", db_session)

    statement = select(UserOrganization).where(
        UserOrganization.user_id == user_id, UserOrganization.org_id == org.id
    )
    user_org = (await db_session.execute(statement)).scalars().first()

    if not user_org:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    # Check if user is the last admin
    statement = select(UserOrganization).where(
        UserOrganization.org_id == org.id, UserOrganization.role_id == ADMIN_ROLE_ID
    )
    admins = (await db_session.execute(statement)).scalars().all()

    if len(admins) == 1 and admins[0].user_id == user_id:
        raise HTTPException(
            status_code=400,
            detail="You can't remove the last admin of the organization",
        )

    usergroup_links = (
        await db_session.execute(
            select(UserGroupUser).where(
                UserGroupUser.user_id == user_id,
                UserGroupUser.org_id == org.id,
            )
        )
    ).scalars().all()
    for usergroup_link in usergroup_links:
        await db_session.delete(usergroup_link)

    await db_session.delete(user_org)
    await db_session.commit()

    from src.routers.users import _invalidate_session_cache
    _invalidate_session_cache(user_id)

    await decrease_feature_usage("members", org_id, db_session)

    await dispatch_webhooks(
        event_name="user_removed_from_org",
        org_id=org_id,
        data={"user_id": user_id, "org_id": org_id},
    )

    return {"detail": "User removed from org"}


async def remove_batch_users_from_org(
    request: Request,
    org_id: int,
    user_ids: list[int],
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
):
    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    # RBAC check
    await rbac_check(request, org.org_uuid, current_user, "delete", db_session)

    # Get all admins for last-admin protection
    admin_statement = select(UserOrganization).where(
        UserOrganization.org_id == org.id, UserOrganization.role_id == ADMIN_ROLE_ID
    )
    admins = (await db_session.execute(admin_statement)).scalars().all()
    admin_ids = {a.user_id for a in admins}

    # Check if removing these users would remove all admins
    remaining_admins = admin_ids - set(user_ids)
    if len(admin_ids) > 0 and len(remaining_admins) == 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove all admins from the organization",
        )

    if user_ids:
        remove_stmt = select(UserOrganization).where(
            UserOrganization.user_id.in_(user_ids),
            UserOrganization.org_id == org.id,
        )
        user_orgs_to_remove = (await db_session.execute(remove_stmt)).scalars().all()
    else:
        user_orgs_to_remove = []

    removed_user_ids = [int(user_org.user_id) for user_org in user_orgs_to_remove]
    if removed_user_ids:
        usergroup_links = (
            await db_session.execute(
                select(UserGroupUser).where(
                    UserGroupUser.user_id.in_(removed_user_ids),
                    UserGroupUser.org_id == org.id,
                )
            )
        ).scalars().all()
        for usergroup_link in usergroup_links:
            await db_session.delete(usergroup_link)

    removed_count = len(user_orgs_to_remove)
    for user_org in user_orgs_to_remove:
        await db_session.delete(user_org)

    await db_session.commit()

    from src.routers.users import _invalidate_session_cache
    for uid in user_ids:
        _invalidate_session_cache(uid)

    for _ in range(removed_count):
        await decrease_feature_usage("members", org_id, db_session)

    return {"detail": f"{removed_count} user(s) removed from org"}


async def update_user_role(
    request: Request,
    org_id: int,
    user_id: int,
    role_uuid: str,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
):
    # find role
    statement = select(Role).where(Role.role_uuid == role_uuid)
    role = (await db_session.execute(statement)).scalars().first()

    if not role:
        raise HTTPException(
            status_code=404,
            detail="Role not found",
        )

    role_id = role.id

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    # RBAC check
    await rbac_check(request, org.org_uuid, current_user, "update", db_session)

    # Check if user is the last admin and if the new role is not admin
    statement = select(UserOrganization).where(
        UserOrganization.org_id == org.id, UserOrganization.role_id == ADMIN_ROLE_ID
    )
    admins = (await db_session.execute(statement)).scalars().all()

    if not admins:
        raise HTTPException(
            status_code=400,
            detail="There is no admin in the organization",
        )

    if (
        len(admins) == 1
        and admins[0].user_id == user_id
        and str(role_uuid) != "role_global_admin"
    ):
        raise HTTPException(
            status_code=400,
            detail="Organization must have at least one admin",
        )

    statement = select(UserOrganization).where(
        UserOrganization.user_id == user_id, UserOrganization.org_id == org.id
    )
    user_org = (await db_session.execute(statement)).scalars().first()

    if not user_org:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if role_id is not None:
        user_org.role_id = role_id

    db_session.add(user_org)
    await db_session.commit()
    await db_session.refresh(user_org)

    from src.routers.users import _invalidate_session_cache
    _invalidate_session_cache(user_org.user_id)

    await dispatch_webhooks(
        event_name="user_role_changed",
        org_id=org_id,
        data={
            "user_id": user_id,
            "org_id": org_id,
            "new_role_uuid": role_uuid,
        },
    )

    # Send role change notification email
    try:
        user_stmt = select(User).where(User.id == user_id)
        user = (await db_session.execute(user_stmt)).scalars().first()
        if user and user.email:
            org_config_stmt = select(OrganizationConfig).where(OrganizationConfig.org_id == org.id)
            org_config = (await db_session.execute(org_config_stmt)).scalars().first()
            send_role_changed_email(
                email=user.email,
                username=user.username,
                org_name=org.name,
                new_role_name=role.name,
                lang=get_org_default_language(org_config),
            )
    except Exception:
        logger.warning("Failed to send role change email to user %s", user_id)

    return {"detail": "User role updated"}


async def update_batch_user_roles(
    request: Request,
    org_id: int,
    user_ids: list[int],
    role_uuid: str,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
):
    if not user_ids:
        raise HTTPException(
            status_code=400,
            detail="No users selected",
        )

    unique_user_ids = list(dict.fromkeys(user_ids))

    statement = select(Role).where(Role.role_uuid == role_uuid)
    role = (await db_session.execute(statement)).scalars().first()

    if not role:
        raise HTTPException(
            status_code=404,
            detail="Role not found",
        )

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    await rbac_check(request, org.org_uuid, current_user, "update", db_session)

    admin_statement = select(UserOrganization).where(
        UserOrganization.org_id == org.id, UserOrganization.role_id == ADMIN_ROLE_ID
    )
    admins = (await db_session.execute(admin_statement)).scalars().all()

    if not admins:
        raise HTTPException(
            status_code=400,
            detail="There is no admin in the organization",
        )

    admin_ids = {admin.user_id for admin in admins}
    if str(role_uuid) != "role_global_admin":
        remaining_admin_ids = admin_ids - set(unique_user_ids)
        if len(remaining_admin_ids) == 0:
            raise HTTPException(
                status_code=400,
                detail="Organization must have at least one admin",
            )

    user_org_statement = select(UserOrganization).where(
        UserOrganization.user_id.in_(unique_user_ids),
        UserOrganization.org_id == org.id,
    )
    user_orgs = (await db_session.execute(user_org_statement)).scalars().all()

    if not user_orgs:
        raise HTTPException(
            status_code=404,
            detail="No selected users were found in this organization",
        )

    updated_user_ids = []
    for user_org in user_orgs:
        user_org.role_id = role.id
        db_session.add(user_org)
        updated_user_ids.append(user_org.user_id)

    await db_session.commit()

    from src.routers.users import _invalidate_session_cache

    for user_id in updated_user_ids:
        _invalidate_session_cache(user_id)
        await dispatch_webhooks(
            event_name="user_role_changed",
            org_id=org_id,
            data={
                "user_id": user_id,
                "org_id": org_id,
                "new_role_uuid": role_uuid,
            },
        )

    updated_user_id_set = set(updated_user_ids)
    skipped_user_ids = [
        user_id for user_id in unique_user_ids if user_id not in updated_user_id_set
    ]

    return {
        "detail": f"{len(updated_user_ids)} user role(s) updated",
        "updated_count": len(updated_user_ids),
        "updated_user_ids": updated_user_ids,
        "skipped_user_ids": skipped_user_ids,
    }


async def invite_batch_users(
    request: Request,
    org_id: int,
    emails: str,
    invite_code_uuid: Optional[str],
    role_uuid: Optional[str],
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
):
    # Redis init
    LH_CONFIG = get_learnhouse_config()
    redis_conn_string = LH_CONFIG.redis_config.redis_connection_string

    if not redis_conn_string:
        raise HTTPException(
            status_code=500,
            detail="Redis connection string not found",
        )

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    invite_role = None
    if role_uuid:
        role_statement = select(Role).where(Role.role_uuid == role_uuid)
        invite_role = (await db_session.execute(role_statement)).scalars().first()

        if not invite_role or (
            invite_role.org_id is not None and invite_role.org_id != org.id
        ):
            raise HTTPException(
                status_code=404,
                detail="Role not found",
            )

    # get User sender
    statement = select(User).where(User.id == current_user.id)
    user = (await db_session.execute(statement)).scalars().first()

    # RBAC check
    await rbac_check(request, org.org_uuid, current_user, "create", db_session)

    # Connect to Redis
    r = redis.Redis.from_url(redis_conn_string)

    if not r:
        raise HTTPException(
            status_code=500,
            detail="Could not connect to Redis",
        )

    invite_list = emails.split(",")

    # invitations expire after 60 days
    ttl = int(timedelta(days=60).total_seconds())

    results = []

    for email in invite_list:
        email = email.strip()
        if not email:
            continue

        # Check if user is already invited
        invited_user = r.get(f"invited_user:{email}:org:{org.org_uuid}")

        if invited_user:
            results.append({"email": email, "status": "already_invited"})
            continue

        org = OrganizationRead.model_validate(org)
        user = UserRead.model_validate(user)

        isEmailSent = await send_invite_email(
            org,
            invite_code_uuid,
            user,
            email,
            request,
            db_session=db_session,
        )

        invited_user_object = {
            "email": email,
            "org_id": org.id,
            "invite_code_uuid": invite_code_uuid,
            "pending": True,
            "email_sent": isEmailSent,
            "expires": ttl,
            "created_at": datetime.now().isoformat(),
            "created_by": current_user.user_uuid,
        }

        if invite_role:
            invited_user_object["role_uuid"] = invite_role.role_uuid
            invited_user_object["role_name"] = invite_role.name

        r.set(
            f"invited_user:{email}:org:{org.org_uuid}",
            json.dumps(invited_user_object),
            ex=ttl,
        )

        results.append({
            "email": email,
            "status": "sent" if isEmailSent else "email_failed",
        })

    sent = sum(1 for r in results if r["status"] == "sent")
    failed = sum(1 for r in results if r["status"] == "email_failed")
    skipped = sum(1 for r in results if r["status"] == "already_invited")

    await dispatch_webhooks(
        event_name="user_invited_to_org",
        org_id=org_id,
        data={
            "org_id": org_id,
            "emails": invite_list,
            "invite_code_uuid": invite_code_uuid,
            "role_uuid": role_uuid,
            "invited_by": current_user.user_uuid,
        },
    )

    return {
        "detail": "Users invited",
        "results": results,
        "summary": {
            "total": len(results),
            "sent": sent,
            "failed": failed,
            "already_invited": skipped,
        },
    }


async def get_list_of_invited_users(
    request: Request,
    org_id: int,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
):
    # Redis init
    LH_CONFIG = get_learnhouse_config()
    redis_conn_string = LH_CONFIG.redis_config.redis_connection_string

    if not redis_conn_string:
        raise HTTPException(
            status_code=500,
            detail="Redis connection string not found",
        )

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    # RBAC check
    await rbac_check(request, org.org_uuid, current_user, "read", db_session)

    # Connect to Redis
    r = redis.Redis.from_url(redis_conn_string)

    if not r:
        raise HTTPException(
            status_code=500,
            detail="Could not connect to Redis",
        )

    # Use scan_iter instead of keys() to avoid blocking Redis
    invited_users = list(r.scan_iter(match=f"invited_user:*:org:{org.org_uuid}", count=100))

    invited_users_list = []

    for user in invited_users:
        invited_user = r.get(user)
        if invited_user:
            invited_user = json.loads(invited_user.decode("utf-8"))
            invited_users_list.append(invited_user)

    return invited_users_list


async def remove_invited_user(
    request: Request,
    org_id: int,
    email: str,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
):
    # Redis init
    LH_CONFIG = get_learnhouse_config()
    redis_conn_string = LH_CONFIG.redis_config.redis_connection_string

    if not redis_conn_string:
        raise HTTPException(
            status_code=500,
            detail="Redis connection string not found",
        )

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    # RBAC check
    await rbac_check(request, org.org_uuid, current_user, "delete", db_session)

    # Connect to Redis
    r = redis.Redis.from_url(redis_conn_string)

    if not r:
        raise HTTPException(
            status_code=500,
            detail="Could not connect to Redis",
        )

    invited_user = r.get(f"invited_user:{email}:org:{org.org_uuid}")

    if not invited_user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    r.delete(f"invited_user:{email}:org:{org.org_uuid}")

    return {"detail": "User removed"}
