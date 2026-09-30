"""github SQLAlchemy tables owned by this module (mirror of revision github_0001)."""

from datetime import datetime

from sqlalchemy.orm import Mapped

from tumnis.core.base import Base, TenantBase


class WebhookDelivery(TenantBase, Base):
    """One accepted webhook delivery (`X-GitHub-Delivery`): a repeated id is refused."""

    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str]
    received_at: Mapped[datetime]
