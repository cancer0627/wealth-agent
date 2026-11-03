"""HTTP（超文本传输协议）适配器集合。"""

from wealth_agent.adapters.http.app import create_http_app
from wealth_agent.adapters.http.dependencies import HttpDependencies

__all__ = ["HttpDependencies", "create_http_app"]
