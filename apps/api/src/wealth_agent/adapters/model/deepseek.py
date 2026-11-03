"""DeepSeek model adapter."""

from wealth_agent.adapters.model.openai_compatible import (
    HttpSender,
    OpenAiCompatibleGateway,
    default_http_sender,
)

DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"


class DeepSeekGateway(OpenAiCompatibleGateway):
    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = DEEPSEEK_CHAT_COMPLETIONS_URL,
        sender: HttpSender = default_http_sender,
    ) -> None:
        super().__init__(
            api_key,
            endpoint=endpoint,
            display_name="DeepSeek",
            api_key_name="DEEPSEEK_API_KEY",
            extra_payload={"thinking": {"type": "disabled"}},
            sender=sender,
        )
