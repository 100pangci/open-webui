import logging

from open_webui.models.access_grants import AccessGrants
from open_webui.models.chats import Chats
from open_webui.models.files import Files
from open_webui.models.folders import FolderModel
from open_webui.models.groups import Groups
from open_webui.models.models import Models
from open_webui.models.users import UserModel, Users
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

FOLDER_FILE_TYPES = {'file'}


async def has_access_to_file(
    file_id: str | None,
    access_type: str,
    user: UserModel,
    db: AsyncSession | None = None,
    user_group_ids: set[str] | None = None,
) -> bool:
    """
    Check if a user has the specified access to a file through any of:
    - Shared workspace models that attach the file directly
    - Shared chats

    NOTE: This does NOT check direct file ownership — callers should check
    file.user_id == user.id separately before calling this.
    """
    file = await Files.get_file_by_id(file_id, db=db)
    log.debug('Checking if user has %s access to file', access_type)
    if not file:
        return False

    # Direct ownership
    if file.user_id == user.id:
        return True

    if user_group_ids is None:
        user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, db=db)}

    # Check if the file is associated with any chats the user has access to
    shared_chat_ids = await Chats.get_shared_chat_ids_by_file_id(file_id, db=db)
    if access_type == 'read' and shared_chat_ids:
        accessible_ids = await AccessGrants.get_accessible_resource_ids(
            user_id=user.id,
            resource_type='shared_chat',
            resource_ids=shared_chat_ids,
            permission='read',
            user_group_ids=user_group_ids,
            db=db,
        )
        if accessible_ids:
            return True

    # Check if the file is directly attached to a shared workspace model (per the ownership
    # note above, model write is conferred only for files the model owner owns).
    model_owners = await Models.get_model_owners_attaching_file(file.id, db=db)
    if access_type != 'read':
        model_owners = {model_id: owner_id for model_id, owner_id in model_owners.items() if owner_id == file.user_id}
    if user.id in model_owners.values():
        return True

    return bool(
        await AccessGrants.get_accessible_resource_ids(
            user_id=user.id,
            resource_type='model',
            resource_ids=list(model_owners),
            permission=access_type,
            user_group_ids=user_group_ids,
            db=db,
        )
    )


async def get_accessible_folder_files(
    entries: list[dict] | None,
    user: UserModel,
    db: AsyncSession | None = None,
    user_group_ids: set[str] | None = None,
) -> list[dict]:
    """Filter folder.data['files'] entries to those the caller can read.

    Entries carry a 'type' ('file') and 'id'. Entries of any other
    shape are dropped because they cannot be access-checked.
    """
    if not isinstance(entries, list):
        return []
    entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get('type') in FOLDER_FILE_TYPES and entry.get('id')
    ]
    if user.role == 'admin':
        return entries

    if user_group_ids is None:
        user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, db=db)}

    accessible: list[dict] = []
    for entry in entries:
        if await has_access_to_file(entry.get('id'), 'read', user, db=db, user_group_ids=user_group_ids):
            accessible.append(entry)
    return accessible


async def can_read_all_folder_files(
    entries: list[dict] | None,
    user: UserModel,
    db: AsyncSession | None = None,
) -> bool:
    if entries is None:
        return True
    if not isinstance(entries, list):
        return False
    if not entries:
        return True

    return len(await get_accessible_folder_files(entries, user, db=db)) == len(entries)


async def get_owner_accessible_folder_files(folder: FolderModel, db: AsyncSession | None = None) -> list[dict]:
    """Return the folder entries its owner can still delegate."""
    files = (folder.data or {}).get('files') or []
    if not files:
        return []

    owner = await Users.get_user_by_id(folder.user_id, db=db)
    if not owner:
        return []

    return await get_accessible_folder_files(files, owner, db=db)
