from django.db import models

class Document(models.Model):
    title = models.CharField(max_length=512)
    file = models.FileField(upload_to='uploads/', blank=True, null=True)
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
