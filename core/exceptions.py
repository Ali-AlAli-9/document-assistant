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

class ChatError(AppError):
    pass