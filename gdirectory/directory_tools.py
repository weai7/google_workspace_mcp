"""
Google Workspace Domain Directory MCP Tools

This module provides MCP tools for looking up people and groups in a Google
Workspace domain directory:

- People search/list uses the People API domain directory
  (scope: directory.readonly) and works for any user in the domain, provided
  the admin has enabled Directory sharing (Admin console > Directory settings).
- Group and membership listing uses the Admin SDK Directory API
  (read-only scopes) and requires the authenticated user to have admin
  privileges (super admin, or a delegated admin role with Groups read).
"""

import asyncio
import logging
from typing import Optional

from mcp import Resource
from mcp.types import ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import UserInputError, handle_http_errors

logger = logging.getLogger(__name__)

DIRECTORY_PERSON_FIELDS = "names,emailAddresses,organizations,phoneNumbers"

DIRECTORY_SOURCES = [
    "DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE",
    "DIRECTORY_SOURCE_TYPE_DOMAIN_CONTACT",
]


def _format_directory_person(person: dict) -> str:
    names = person.get("names", [])
    display_name = names[0].get("displayName", "(no name)") if names else "(no name)"

    emails = [e.get("value") for e in person.get("emailAddresses", []) if e.get("value")]
    email_str = ", ".join(emails) if emails else "(no email)"

    lines = [f"- {display_name} <{email_str}>"]

    orgs = person.get("organizations", [])
    if orgs:
        org = orgs[0]
        org_bits = [org.get(k) for k in ("department", "title") if org.get(k)]
        if org_bits:
            lines.append(f"  {' / '.join(org_bits)}")

    phones = [p.get("value") for p in person.get("phoneNumbers", []) if p.get("value")]
    if phones:
        lines.append(f"  Phone: {', '.join(phones)}")

    return "\n".join(lines)


# =============================================================================
# People API domain directory (any user in the domain)
# =============================================================================


@server.tool(
    title="Search Directory People",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("people", "directory_read")
@handle_http_errors("search_directory_people", service_type="people")
async def search_directory_people(
    service: Resource,
    user_google_email: str,
    query: str,
    page_size: int = 30,
) -> str:
    """
    Search the organization's directory for people by name, email, or other
    profile fields. Use this to resolve a colleague's email address from their
    name (e.g. before adding them to a calendar event).

    Args:
        user_google_email (str): The user's Google email address. Required.
        query (str): Search query (name, email prefix, etc.).
        page_size (int): Maximum number of results (default: 30, max: 500).

    Returns:
        str: Matching people with name, email, and organization info.
    """
    logger.info(
        f"[search_directory_people] Invoked. Email: '{user_google_email}', Query: '{query}'"
    )

    if not query.strip():
        raise UserInputError("query must not be empty")
    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 500)

    result = await asyncio.to_thread(
        service.people()
        .searchDirectoryPeople(
            query=query,
            readMask=DIRECTORY_PERSON_FIELDS,
            sources=DIRECTORY_SOURCES,
            pageSize=page_size,
        )
        .execute
    )

    people = result.get("people", [])
    if not people:
        return (
            f"No directory people found for '{query}'. "
            "If this is unexpected, the domain may not have Directory sharing enabled."
        )

    response = f"Directory people matching '{query}' ({len(people)}):\n\n"
    response += "\n\n".join(_format_directory_person(p) for p in people)

    logger.info(f"Found {len(people)} directory people for query '{query}'")
    return response


@server.tool(
    title="List Directory People",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("people", "directory_read")
@handle_http_errors("list_directory_people", service_type="people")
async def list_directory_people(
    service: Resource,
    user_google_email: str,
    page_size: int = 100,
    page_token: Optional[str] = None,
) -> str:
    """
    List people in the organization's directory.

    Args:
        user_google_email (str): The user's Google email address. Required.
        page_size (int): Maximum number of results (default: 100, max: 1000).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: Directory people with name, email, and organization info.
    """
    logger.info(f"[list_directory_people] Invoked. Email: '{user_google_email}'")

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 1000)

    params = {
        "readMask": DIRECTORY_PERSON_FIELDS,
        "sources": DIRECTORY_SOURCES,
        "pageSize": page_size,
    }
    if page_token:
        params["pageToken"] = page_token

    result = await asyncio.to_thread(
        service.people().listDirectoryPeople(**params).execute
    )

    people = result.get("people", [])
    next_page_token = result.get("nextPageToken")

    if not people:
        return (
            "No directory people found. "
            "If this is unexpected, the domain may not have Directory sharing enabled."
        )

    response = f"Directory people ({len(people)}):\n\n"
    response += "\n\n".join(_format_directory_person(p) for p in people)
    if next_page_token:
        response += f"\n\nNext page token: {next_page_token}"

    logger.info(f"Listed {len(people)} directory people")
    return response


# =============================================================================
# Admin SDK Directory API (requires admin privileges)
# =============================================================================


@server.tool(
    title="List Directory Groups",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("admin_directory", "admin_group_read")
@handle_http_errors("list_directory_groups", service_type="admin_directory")
async def list_directory_groups(
    service: Resource,
    user_google_email: str,
    member_email: Optional[str] = None,
    page_size: int = 100,
    page_token: Optional[str] = None,
) -> str:
    """
    List groups in the Google Workspace domain (e.g. team or role mailing
    lists). Requires the authenticated user to have admin privileges.

    Args:
        user_google_email (str): The user's Google email address. Required.
        member_email (Optional[str]): If set, only list groups this user/group is a member of.
        page_size (int): Maximum number of groups to return (default: 100, max: 200).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: Groups with name, email, and description.
    """
    logger.info(
        f"[list_directory_groups] Invoked. Email: '{user_google_email}', Member: '{member_email}'"
    )

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 200)

    params = {"maxResults": page_size}
    if member_email:
        params["userKey"] = member_email
    else:
        params["customer"] = "my_customer"
    if page_token:
        params["pageToken"] = page_token

    result = await asyncio.to_thread(service.groups().list(**params).execute)

    groups = result.get("groups", [])
    next_page_token = result.get("nextPageToken")

    if not groups:
        return "No groups found."

    response = f"Groups ({len(groups)}):\n\n"
    for group in groups:
        line = f"- {group.get('name', '(no name)')} <{group.get('email', '')}>"
        if group.get("directMembersCount"):
            line += f" — {group['directMembersCount']} members"
        response += line + "\n"
        if group.get("description"):
            response += f"  {group['description']}\n"

    if next_page_token:
        response += f"\nNext page token: {next_page_token}"

    logger.info(f"Listed {len(groups)} groups")
    return response


@server.tool(
    title="List Group Members",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("admin_directory", "admin_group_member_read")
@handle_http_errors("list_group_members", service_type="admin_directory")
async def list_group_members(
    service: Resource,
    user_google_email: str,
    group_email: str,
    include_derived: bool = False,
    page_size: int = 200,
    page_token: Optional[str] = None,
) -> str:
    """
    List members of a Google Workspace group (e.g. resolve everyone in
    team@yourdomain.com to invite them to a meeting). Requires the
    authenticated user to have admin privileges.

    Args:
        user_google_email (str): The user's Google email address. Required.
        group_email (str): Email address (or unique id) of the group.
        include_derived (bool): Include indirect members from nested groups (default: False).
        page_size (int): Maximum number of members to return (default: 200, max: 200).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: Group members with email, role, and type.
    """
    logger.info(
        f"[list_group_members] Invoked. Email: '{user_google_email}', Group: '{group_email}'"
    )

    if not group_email.strip():
        raise UserInputError("group_email must not be empty")
    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 200)

    params = {
        "groupKey": group_email,
        "maxResults": page_size,
        "includeDerivedMembership": include_derived,
    }
    if page_token:
        params["pageToken"] = page_token

    result = await asyncio.to_thread(service.members().list(**params).execute)

    members = result.get("members", [])
    next_page_token = result.get("nextPageToken")

    if not members:
        return f"No members found in group '{group_email}'."

    response = f"Members of {group_email} ({len(members)}):\n\n"
    for member in members:
        line = f"- {member.get('email', member.get('id', '(unknown)'))}"
        details = [d for d in (member.get("role"), member.get("type")) if d]
        if details:
            line += f" ({', '.join(d.lower() for d in details)})"
        response += line + "\n"

    if next_page_token:
        response += f"\nNext page token: {next_page_token}"

    logger.info(f"Listed {len(members)} members of group '{group_email}'")
    return response
