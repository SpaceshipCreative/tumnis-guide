"""knowledge MCP tools; thin calls into api.py.

The knowledge tools arrive in P2-17. Until then this registers the project brief reader
that `get_project_context` (projects, P2-01) answers with: projects cannot import
knowledge, which already imports projects.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.versioning import NotFound
from tumnis.modules.knowledge import api
from tumnis.modules.projects import api as projects


async def _brief(s: AsyncSession, project_id: UUID) -> str | None:
    """The project's brief text; None before the `project.created` subscriber wrote it."""
    try:
        found = await api.get_brief(project_id, session=s)
    except NotFound:
        return None
    return found.body_md


projects.register_brief_source(_brief)
