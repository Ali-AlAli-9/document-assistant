from sentence_transformers import SentenceTransformer
from domain.ports.embedding import EmbeddingPort
import os
import logging

logger = logging.getLogger(__name__)

_OFFLINE_HINT = (
    'Could not load embedding model from local directory. '
    'Ensure the model files exist in the models/ directory.'
)

class SentenceTransformerAdapter(EmbeddingPort):
    def __init__(self, model_name: str = 'all-MiniLM-L6-v2', model_dir: str = 'models'):
        self._model = self._load_model(model_name, model_dir)
        self._dimension = self._model.get_embedding_dimension()
        logger.info(f'Embedding model loaded from local: {model_name} ({self._dimension}d)')

    @staticmethod
    def _load_model(model_name: str, model_dir: str) -> SentenceTransformer:
        local_path = os.path.join(model_dir, model_name)
        if os.path.exists(local_path):
            logger.info('Loading model from local path: %s', local_path)
            return SentenceTransformer(local_path)
        
        logger.warning('Local model not found at %s, trying HuggingFace cache', local_path)
        try:
            return SentenceTransformer(model_name, local_files_only=True)
        except Exception as exc:
            raise RuntimeError(_OFFLINE_HINT) from exc

    def encode(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, show_progress_bar=False).tolist()

    def encode_query(self, text: str) -> list[float]:
        return self._model.encode([text], show_progress_bar=False).tolist()[0]

    @property
    def dimension(self) -> int:
        return self._dimension