from dataclasses import dataclass, field
from datetime import datetime, timezone

@dataclass
class Message:
    role: str
    content: str
    sources: list[dict] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class Conversation:
    id: str
    user_id: str | None = None
    messages: list[Message] = field(default_factory=list)
    max_history: int = 6
    max_context_chars: int = 4000

    def add_message(self, role: str, content: str, sources: list[dict] | None = None) -> None:
        self.messages.append(Message(role=role, content=content, sources=sources))

    @property
    def history(self) -> str:
        recent = self.messages[-self.max_history:]
        lines = [
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
            for m in recent
        ]
        text = '\n'.join(lines)
        if len(text) > self.max_context_chars:
            text = text[-self.max_context_chars:]
            cut = text.find('\n')
            if cut > 0:
                text = text[cut + 1:]
        return text
