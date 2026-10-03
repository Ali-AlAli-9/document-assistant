class AppError(Exception):
    pass

class DocumentError(AppError):
    pass

class IngestionError(DocumentError):
    pass

class FileNotAllowedError(DocumentError):
    pass

class VectorStoreError(AppError):
    pass

class LLMError(AppError):
    pass

# The one user-facing sentence for "you have not supplied your own key". It lives
# here, beside the exception, rather than in api/endpoints/chat.py because three
# layers must never disagree about it: the endpoint's early 401, the factory's
# fail-closed backstop (whose message can surface through a generic `except
# LLMError` handler), and the log line. Duplicating the string across those is
# how the wording drifts and the user gets two different answers to one state.
#
# Deliberately free of the substring "غير صحيحة": frontend/src/api/errors.js has
# a broad login rule keyed on it, and being caught by that would tell a user with
# a perfectly valid session that their username or password is wrong.
NO_PERSONAL_KEY_DETAIL = (
    'لم تُدخل مفتاح Gemini بعد — أضفه من الإعدادات. '
    'كل محادثة تستخدم مفتاحك أنت وحده.'
)

class MissingLLMCredentials(LLMError):
    """A provider was asked to work without a credential the ACCOUNT must own.

    Subclasses LLMError so every existing handler still catches it, but nothing
    upstream failed and nothing is retryable: the account simply has not
    supplied a key. Endpoints must answer it as account state — 401 plus the
    next step — never as a 5xx, and must never substitute a server-wide key to
    make it disappear.

    The message is user-facing Arabic rather than an internal English note, for
    the reason given above: a handler that catches LLMError and renders str(e)
    would otherwise put an untranslated string in front of the user.
    """
    def __init__(self, provider: str = 'gemini'):
        self.provider = provider
        super().__init__(NO_PERSONAL_KEY_DETAIL)

class ChatError(AppError):
    pass