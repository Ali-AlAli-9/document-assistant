from django.conf import settings
from django.db import models

class Document(models.Model):
    title = models.CharField(max_length=512)
    file = models.FileField(upload_to='uploads/', blank=True, null=True)
    # Tenant isolation: every row belongs to the account that uploaded it.
    # Without this, any logged-in user could list, read (through chat source
    # excerpts), and DELETE every other user's documents — the collection in
    # ChromaDB is shared, so retrieval leaked other people's file contents too.
    # Null only so rows written before this field existed migrate cleanly; such
    # rows belong to nobody and are therefore visible to nobody.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='documents',
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('processing', 'Processing'),
            ('ready', 'Ready'),
            ('failed', 'Failed'),
        ],
        default='pending',
    )
    chunk_count = models.IntegerField(default=0)
    file_size = models.BigIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.title
