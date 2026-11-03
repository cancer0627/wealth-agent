"""Kimi model adapter."""

from wealth_agent.adapters.model.openai_compatible import (
    HttpSender,
    OpenAiCompatibleGateway,
    default_http_sender,
)

KIMI_CHAT_COMPLETIONS_URL = "https://api.moonshot.cn/v1/chat/completions"


class KimiGateway(OpenAiCompatibleGateway):
    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = KIMI_CHAT_COMPLETIONS_URL,
        sender: HttpSender = default_http_sender,
    ) -> None:
        super().__init__(
            api_key,
            endpoint=endpoint,
            display_name="Kimi",
            api_key_name="MOONSHOT_API_KEY",
            sender=sender,
        )
