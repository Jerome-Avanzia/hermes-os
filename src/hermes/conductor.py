"""Conductor — renders conversation context and delegates to the LLM provider.

The Conductor sits between assembled context and the provider. It composes
the system prompt from profile, organization, and knowledge, then delegates
to the provider.

Routing is deterministic: the caller selects the profile explicitly, or
the default profile is used.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING

from hermes.kernel.profile_loader import ProfileLoader
from hermes.models.profile import Profile
from hermes.providers.ollama_provider import ChatMessage, OllamaProvider

if TYPE_CHECKING:
    from hermes.models.context import Context

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

    def stream_chat_with_context(
        self,
        messages: list[ChatMessage],
        context: Context,
    ) -> Iterator[str]:
        """Stream a chat using pre-assembled context.

        The Context Engine has already resolved workspace, organization,
        profile, and knowledge. The Conductor composes the system prompt
        and invokes the provider.
        """
        profile = context.profile or self._profile_loader.get_default()
        system_prompt = self._compose_system_prompt(context, profile)

        if system_prompt:
            full_messages = self._prepend_system_prompt(
                messages,
                Profile(
                    id=profile.id,
                    name=profile.name,
                    system_prompt=system_prompt,
                    model=profile.model,
                ),
            )
        else:
            full_messages = messages

        logger.info(
            "Conductor rendering: profile=%s model=%s messages=%d",
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

    def _compose_system_prompt(self, context: Context, profile: Profile) -> str:
        """Compose a system prompt from profile, organization, and knowledge."""
        parts: list[str] = []

        if profile.system_prompt:
            parts.append(profile.system_prompt.rstrip())

        org = context.workspace.workspace.organization
        if org:
            org_parts: list[str] = []
            if org.purpose:
                org_parts.append(f"Purpose:\n{org.purpose.strip()}")
            if org.mission:
                org_parts.append(f"Mission:\n{org.mission.strip()}")
            if org.vision:
                org_parts.append(f"Vision:\n{org.vision.strip()}")
            if org.positioning:
                org_parts.append(f"Positioning:\n{org.positioning.strip()}")
            if org.services:
                org_parts.append(f"Services:\n{org.services.strip()}")
            if org.brand:
                org_parts.append(f"Brand:\n{org.brand.strip()}")
            if org.values:
                org_parts.append(f"Values:\n{org.values.strip()}")
            if org.target_customers:
                org_parts.append(f"Target Customers:\n{org.target_customers.strip()}")
            if org.tone_of_voice:
                org_parts.append(f"Tone of Voice:\n{org.tone_of_voice.strip()}")
            if org.visual_identity:
                org_parts.append(f"Visual Identity:\n{org.visual_identity.strip()}")
            if org.site_map:
                org_parts.append(f"Site Map:\n{org.site_map.strip()}")
            if org.homepage_copy:
                org_parts.append(f"Homepage Copy:\n{org.homepage_copy.strip()}")
            if org_parts:
                parts.append("## Organization\n\n" + "\n\n".join(org_parts))

        if context.knowledge and context.knowledge.documents:
            knowledge_parts = [
                f"## {doc.title}\n\n{doc.content.strip()}"
                for doc in context.knowledge.documents
            ]
            if knowledge_parts:
                parts.append("\n\n".join(knowledge_parts))

        return "\n\n".join(parts)

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

