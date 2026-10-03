import pytest
from django.test import TestCase
from documents.models import Document
from django.core.files.base import ContentFile


class DocumentModelTest(TestCase):
    def test_create_document(self):
        doc = Document.objects.create(title='test.pdf', status='pending')
        assert doc.title == 'test.pdf'
        assert doc.status == 'pending'
        assert doc.chunk_count == 0
        assert not doc.file

    def test_str(self):
        doc = Document.objects.create(title='mydoc.pdf')
        assert str(doc) == 'mydoc.pdf'

    def test_default_status(self):
        doc = Document.objects.create(title='default.pdf')
        assert doc.status == 'pending'

    def test_status_choices(self):
        for status in ['pending', 'processing', 'ready', 'failed']:
            doc = Document.objects.create(title=f'{status}.pdf', status=status)
            assert doc.status == status

    def test_chunk_count_update(self):
        doc = Document.objects.create(title='test.pdf')
        doc.chunk_count = 10
        doc.save()
        doc.refresh_from_db()
        assert doc.chunk_count == 10

    def test_ordering(self):
        Document.objects.create(title='first.pdf')
        Document.objects.create(title='second.pdf')
        docs = Document.objects.all()
        assert docs[0].title == 'second.pdf'

    def test_file_field(self):
        doc = Document(title='file_test.txt')
        doc.file.save('test.txt', ContentFile(b'hello world'))
        doc.save()
        doc.refresh_from_db()
        assert doc.file.name is not None
        assert doc.file.name.endswith('.txt')
        doc.file.delete()

    def test_timestamps_set(self):
        doc = Document.objects.create(title='time_test.pdf')
        assert doc.uploaded_at is not None
        assert doc.updated_at is not None
