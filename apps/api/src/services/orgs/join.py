from datetime import datetime
import json
from typing import Optional, Union
import redis
from fastapi import HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from config.config import get_learnhouse_config
from src.db.organizations import Organization
from src.db.roles import Role
from src.db.user_organizations import UserOrganization
from src.db.users import AnonymousUser, InternalUser, PublicUser, User
from src.security.features_utils.usage import (
    check_limits_with_usage,
    increase_feature_usage,
)
from src.services.orgs.invites import get_invite_code
from src.services.orgs.orgs import get_org_join_mechanism
from src.services.users.usergroups import add_users_to_usergroup

DEFAULT_USER_ROLE_ID = 4


class JoinOrg(BaseModel):
    org_id: int
    user_id: Union[str, int]
    invite_code: Optional[str] = None

    @field_validator("user_id", mode="before")
    @classmethod
    def coerce_user_id_to_str(cls, v: Union[str, int]) -> str:
        return str(v)


async def _get_pending_invite_role_id(
    db_session: AsyncSession,
    org: Organization,
    email: str | None,
) -> int:
    if not email:
        return DEFAULT_USER_ROLE_ID

    try:
        redis_conn_string = get_learnhouse_config().redis_config.redis_connection_string
        if not redis_conn_string:
            return DEFAULT_USER_ROLE_ID

        r = redis.Redis.from_url(redis_conn_string)
        invited_data = r.get(f"invited_user:{email}:org:{org.org_uuid}")
        if not invited_data:
            return DEFAULT_USER_ROLE_ID

        invite_record = json.loads(invited_data)
        role_uuid = invite_record.get("role_uuid")
        if not role_uuid:
            return DEFAULT_USER_ROLE_ID

        role_statement = select(Role).where(Role.role_uuid == role_uuid)
        role = (await db_session.execute(role_statement)).scalars().first()
        if not role or role.id is None:
            return DEFAULT_USER_ROLE_ID

        if role.org_id is not None and role.org_id != org.id:
            return DEFAULT_USER_ROLE_ID

        return role.id
    except Exception:
        return DEFAULT_USER_ROLE_ID


async def join_org(
    request: Request,
    args: JoinOrg,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
):
    statement = select(Organization).where(Organization.id == args.org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org or org.id is None:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    await check_limits_with_usage("members", org.id, db_session)

    join_method = await get_org_join_mechanism(
        request, args.org_id, current_user, db_session
    )

    # Get User by UUID or numeric ID
    user_id_str = str(args.user_id)
    if user_id_str.isdigit():
        statement = select(User).where(
            (User.user_uuid == user_id_str) | (User.id == int(user_id_str))
        )
    else:
        statement = select(User).where(User.user_uuid == user_id_str)
    user = (await db_session.execute(statement)).scalars().first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    # Check if user's email is verified
    if not user.email_verified:
        raise HTTPException(
            status_code=403,
            detail="Please verify your email address before joining an organization.",
        )

    # Check if User isn't already part of the org
    statement = select(UserOrganization).where(
        UserOrganization.user_id == user.id, UserOrganization.org_id == args.org_id
    )
    userorg = (await db_session.execute(statement)).scalars().first()

    if userorg:
        raise HTTPException(
            status_code=400, detail="User is already part of that organization"
        )

    if join_method == "inviteOnly" and user and org and args.invite_code:
        if user.id is not None and org.id is not None:

            # Check if invite code exists
            inviteCode = await get_invite_code(
                request, org.id, args.invite_code, current_user, db_session
            )

            if not inviteCode:
                raise HTTPException(
                    status_code=400,
                    detail="Invite code is incorrect",
            )

            role_id = await _get_pending_invite_role_id(db_session, org, user.email)

            # Link user and organization
            user_organization = UserOrganization(
                user_id=user.id,
                org_id=org.id,
                role_id=role_id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )

            db_session.add(user_organization)
            await db_session.commit()

            from src.routers.users import _invalidate_session_cache
            _invalidate_session_cache(user.id)

            # Add user to UserGroup if invite code is linked to one
            if inviteCode.get("usergroup_id"):
                await add_users_to_usergroup(
                    request,
                    db_session,
                    InternalUser(id=0),
                    int(inviteCode.get("usergroup_id")),
                    str(user.id),
                )

            await increase_feature_usage("members", org.id, db_session)

            return "Great, You're part of the Organization"

        else:
            raise HTTPException(
                status_code=403,
                detail="Something wrong, try later.",
            )

    if join_method == "open" and user and org:
        if user.id is not None and org.id is not None:
            role_id = await _get_pending_invite_role_id(db_session, org, user.email)

            # Link user and organization
            user_organization = UserOrganization(
                user_id=user.id,
                org_id=org.id,
                role_id=role_id,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )

            db_session.add(user_organization)
            await db_session.commit()

            from src.routers.users import _invalidate_session_cache
            _invalidate_session_cache(user.id)

            await increase_feature_usage("members", org.id, db_session)

            return "Great, You're part of the Organization"

        else:
            raise HTTPException(
                status_code=403,
                detail="Something wrong, try later.",
            )

    else:
        raise HTTPException(
            status_code=403,
            detail="Something wrong, try later.",
        )
