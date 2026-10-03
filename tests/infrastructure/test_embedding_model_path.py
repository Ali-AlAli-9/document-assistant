"""The model must be found on disk whichever form EMBEDDING_MODEL takes.

Why this file exists (first Render build, step 8/11, failed):

    ``Dockerfile`` baked the snapshot from the HuggingFace repo
    ``sentence-transformers/all-MiniLM-L6-v2`` into
    ``$EMBEDDING_MODEL_DIR/all-MiniLM-L6-v2`` — while ``render.yaml`` set
    ``EMBEDDING_MODEL=all-MiniLM-L6-v2`` and Render feeds a service's env vars
    into the BUILD environment. So ``ARG EMBEDDING_MODEL`` was overwritten by a
    bare name, ``snapshot_download`` was asked for
    ``huggingface.co/api/models/all-MiniLM-L6-v2``, and the build died with
    ``401 RepositoryNotFoundError``.

    The two roles shared one variable name, so this file pins the invariant that
    makes the collision harmless: the on-disk folder is the LAST SEGMENT of
    whatever form arrives.

    The repo id has since changed to ``intfloat/multilingual-e5-small`` (the
    English-only model above was replaced for Arabic), so the constants below are
    the CURRENT pair from ``render.yaml`` / ``Dockerfile``. The invariant, not the
    particular name, is what is being protected: had the two forms drifted apart
    again, this would fail here rather than on Render.
"""

import os

import pytest

from infrastructure.embedding.sentence_transformer_adapter import (
    resolve_local_model_path,
)

REPO_ID = 'intfloat/multilingual-e5-small'
BARE = 'multilingual-e5-small'


class TestPathResolution:
    @pytest.mark.parametrize('model_name', [REPO_ID, BARE])
    def test_both_name_forms_resolve_to_the_same_folder(self, model_name):
        # The invariant. If these two ever diverge, the adapter silently falls
        # through to an offline cache that was never populated and the app
        # starts up with a broken embedder instead of failing the build.
        assert resolve_local_model_path('/app/models', model_name) == (
            os.path.join('/app/models', BARE)
        )

    def test_the_configured_production_value_resolves_into_the_image(self):
        """The exact pair from render.yaml / Dockerfile, so a change to either
        that breaks the pairing fails here instead of on Render.

        Compared through os.path.join because the separator is the host's: the
        image runs on Linux ('/'), this test runs on Windows ('\\'), and the
        invariant is the pairing of the two segments, not the separator.
        """
        assert resolve_local_model_path('/app/models', REPO_ID) == os.path.join(
            '/app/models', BARE
        )

    def test_a_trailing_slash_does_not_create_an_empty_segment(self):
        assert resolve_local_model_path('models', 'intfloat/multilingual-e5-small/') == (
            os.path.join('models', BARE)
        )

    def test_an_org_prefix_with_a_nested_path_uses_the_last_segment(self):
        assert resolve_local_model_path('models', 'a/b/c/model-x') == os.path.join('models', 'model-x')

    def test_a_local_absolute_path_is_not_swallowed(self):
        """If someone points EMBEDDING_MODEL at an absolute path already, the
        basename lookup must still land on that very folder rather than on a
        relative one."""
        assert resolve_local_model_path('models', '/opt/models/' + BARE) == os.path.join(
            'models', BARE
        )

    @pytest.mark.parametrize('model_name', [REPO_ID, BARE])
    def test_resolved_path_is_what_the_adapter_probes(self, model_name, monkeypatch):
        """_load_model must probe exactly this path, not os.path.join(dir, name):
        that join was the original defect."""
        from infrastructure.embedding import sentence_transformer_adapter as mod

        probed = []

        def _fake_probe(path):
            probed.append(path)
            return True

        monkeypatch.setattr(mod.os.path, 'exists', _fake_probe)

        class _Recorder:
            def __init__(self, *args, **kwargs):
                probed.append(('constructed', args))

        monkeypatch.setattr(mod, 'SentenceTransformer', _Recorder)

        mod.SentenceTransformerAdapter._load_model(model_name, '/app/models')

        assert probed[0] == os.path.join('/app/models', BARE)
