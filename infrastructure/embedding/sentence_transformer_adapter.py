from sentence_transformers import SentenceTransformer
from domain.ports.embedding import EmbeddingPort
import os
import logging

logger = logging.getLogger(__name__)

_OFFLINE_HINT = (
    'Could not load embedding model from local directory. '
    'Ensure the model files exist in the models/ directory.'
)


def resolve_local_model_path(model_dir: str, model_name: str) -> str:
    """The on-disk folder for a model, given either name form.

    ``EMBEDDING_MODEL`` reaches this adapter in two shapes, and both must land
    on the SAME folder:

    * ``multilingual-e5-small``              — a bare model name
    * ``intfloat/multilingual-e5-small``     — a HuggingFace repo id

    The Docker image bakes the snapshot into ``<EMBEDDING_MODEL_DIR>/<basename>``
    (basename of the repo id), so a full repo id would otherwise produce
    ``models/intfloat/multilingual-e5-small``, miss, and fall through to
    the offline cache — where nothing was ever downloaded. That mismatch is
    exactly what broke the first Render build.
    """
    return os.path.join(model_dir, model_name.rstrip('/').split('/')[-1])


class SentenceTransformerAdapter(EmbeddingPort):
    def __init__(
        self,
        model_name: str = 'intfloat/multilingual-e5-small',
        model_dir: str = 'models',
    ):
        self._model = self._load_model(model_name, model_dir)
        self._dimension = self._model.get_embedding_dimension()
        logger.info(f'Embedding model loaded from local: {model_name} ({self._dimension}d)')

    @staticmethod
    def _load_model(model_name: str, model_dir: str) -> SentenceTransformer:
        local_path = resolve_local_model_path(model_dir, model_name)
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