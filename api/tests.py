from unittest.mock import patch, MagicMock
from django.test import TestCase


class TestSettingsAPI(TestCase):

    @patch('api.endpoints.settings.get_llm')
    def test_validate_api_key_empty(self, mock_get_llm):
        response = self.client.post(
            '/api/settings/validate-key',
            data='{"api_key": "   "}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['valid'])
        mock_get_llm.assert_not_called()

    @patch('api.endpoints.settings.get_llm')
    def test_validate_api_key_valid(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.generate.return_value = 'ok'
        mock_get_llm.return_value = mock_llm
        response = self.client.post(
            '/api/settings/validate-key',
            data='{"api_key": "test-key-123"}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['valid'])

    @patch('api.endpoints.settings.get_llm')
    def test_validate_api_key_invalid(self, mock_get_llm):
        from core.exceptions import LLMError
        mock_get_llm.side_effect = LLMError('Invalid key')
        response = self.client.post(
            '/api/settings/validate-key',
            data='{"api_key": "bad-key"}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['valid'])
