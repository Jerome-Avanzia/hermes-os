"""Conductor — routes chat requests through profiles to the LLM provider.

The Conductor sits between the gateway and the provider. It resolves the
active profile, prepends the system prompt, and delegates to the provider.

Routing is deterministic: the caller selects the profile explicitly, or
the default profile is used.
"""

import logging
from collections.abc import Iterator

from hermes.kernel.profile_loader import ProfileLoader
from hermes.models.profile import Profile
from hermes.providers.ollama_provider import ChatMessage, OllamaProvider

logger = logging.getLogger(__name__)


class Conductor:
    def __init__(
        self,
        provider: OllamaProvider,
        profile_loader: ProfileLoader | None = None,
    ) -> None:
        self._provider = provider
        self._profile_loader = profile_loader or ProfileLoader()

    def stream_chat(
        self,
        messages: list[ChatMessage],
        profile_id: str | None = None,
    ) -> Iterator[str]:
        """Stream a chat completion with the resolved profile's system prompt.

        If *profile_id* is given, that profile is loaded. Otherwise the
        default profile is used.
        """
        profile = self._resolve_profile(profile_id)
        full_messages = self._prepend_system_prompt(messages, profile)

        logger.info(
            "Conductor routing: profile=%s model=%s messages=%d",
            profile.id,
            profile.model or "(provider default)",
            len(full_messages),
        )

        provider = self._resolve_provider(profile)
        return provider.stream_chat(full_messages)

    def chat(
        self,
        messages: list[ChatMessage],
        profile_id: str | None = None,
    ) -> str:
        """Non-streaming chat with the resolved profile."""
        return "".join(self.stream_chat(messages, profile_id))

    @property
    def profile_loader(self) -> ProfileLoader:
        return self._profile_loader

    def _resolve_profile(self, profile_id: str | None) -> Profile:
        if profile_id:
            return self._profile_loader.get(profile_id)
        return self._profile_loader.get_default()

    def _prepend_system_prompt(
        self,
        messages: list[ChatMessage],
        profile: Profile,
    ) -> list[ChatMessage]:
        if not profile.system_prompt:
            return messages

        # Replace any existing system message, or prepend a new one.
        if messages and messages[0].role == "system":
            return [
                ChatMessage(role="system", content=profile.system_prompt),
                *messages[1:],
            ]
        return [
            ChatMessage(role="system", content=profile.system_prompt),
            *messages,
        ]

    def _resolve_provider(self, profile: Profile) -> OllamaProvider:
        if profile.model and profile.model != self._provider._model:
            return OllamaProvider(
                model=profile.model,
                base_url=self._provider._base_url,
                timeout=self._provider._timeout,
            )
        return self._provider
