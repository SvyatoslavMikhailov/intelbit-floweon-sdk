from enum import StrEnum

from pydantic import BaseModel


class PluginType(StrEnum):
    CONNECTOR = "connector"
    TRANSFORMER = "transformer"
    NOTIFIER = "notifier"


class PluginManifest(BaseModel):
    id: str
    version: str
    api_version: str = "1"
    plugin_type: PluginType
    name: str
    description: str = ""
    author: str = ""
    license: str = "Apache-2.0"
