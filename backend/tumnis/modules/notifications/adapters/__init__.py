"""notifications adapters: one file per outside dependency, each with a fake.

`notifications.webpush` sends browser pushes (`webpush/client.py`, captured by
`webpush/fake.py`)."""

from tumnis.core.adapters.registry import register_adapter
from tumnis.modules.notifications.adapters.port import WebPushAdapter
from tumnis.modules.notifications.adapters.webpush.client import WebPushClient
from tumnis.modules.notifications.adapters.webpush.fake import FakeWebPush

register_adapter("notifications.webpush", port=WebPushAdapter, real=WebPushClient, fake=FakeWebPush)
