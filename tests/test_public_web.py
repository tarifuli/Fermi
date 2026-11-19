import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch
from public_web import classify_question, public_web_answer, safe_source
from fermi_ai import AdviceUnavailable
import public_web


class RoutingPrivacyTests(unittest.TestCase):
    def setUp(self):
        public_web._last = 0

    def test_business_and_mixed_questions_stay_local(self):
        for question in ['What were my sales today?', 'Who is NVIDIA CEO and compare my revenue?',
                         'আমার স্টক কত', 'What is the password?', 'Who is secret@example.com?',
                         'What is 01712345678?', 'What does our best customer buy?']:
            self.assertEqual(classify_question(question), 'business', question)

    def test_public_and_ambiguous(self):
        self.assertEqual(classify_question('Who is the CEO of NVIDIA now?'), 'public')
        self.assertEqual(classify_question('Explain photosynthesis'), 'public')
        self.assertEqual(classify_question('Compare these'), 'clarify')
        self.assertEqual(classify_question('Do it'), 'clarify')

    def test_no_external_call_without_consent_or_for_private_input(self):
        with patch('public_web.urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
            with self.assertRaises(ValueError):
                public_web_answer('Who is NVIDIA CEO?')
            with self.assertRaises(ValueError):
                public_web_answer('Who is my top customer?', consent=True)

    def test_missing_search_key_does_not_guess(self):
        with patch.dict(os.environ, {'TAVILY_API_KEY': '', 'GEMINI_API_KEY': 'test'}):
            with self.assertRaises(AdviceUnavailable):
                public_web_answer('Who is NVIDIA CEO?', consent=True)

    def test_sources_are_retrieval_only_and_payload_has_no_private_context(self):
        search = {'results': [{'title': 'Official page', 'url': 'https://www.nvidia.com/about/',
                               'content': 'A public company leadership page.'},
                              {'title': 'Unsafe', 'url': 'javascript:alert(1)', 'content': 'Unsafe'}]}
        generated = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'A public answer [1].'}]}}]}
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'search-secret', 'GEMINI_API_KEY': 'gem-secret'}):
            with patch('public_web.urllib.request.urlopen', side_effect=[
                    BytesIO(json.dumps(search).encode()), BytesIO(json.dumps(generated).encode())]) as send:
                result = public_web_answer('Who is the CEO of NVIDIA now?', consent=True)
                self.assertEqual(len(result['sources']), 1)
                self.assertEqual(result['sources'][0]['url'], 'https://www.nvidia.com/about/')
                first = json.loads(send.call_args_list[0].args[0].data)
                self.assertEqual(set(first), {'query', 'search_depth', 'max_results', 'include_answer', 'include_raw_content', 'auto_parameters'})
                self.assertEqual(first['search_depth'], 'basic')
                second = json.loads(send.call_args_list[1].args[0].data)
                context = json.loads(second['contents'][0]['parts'][0]['text'])
                self.assertEqual(set(context), {'question', 'searched_at_utc', 'sources'})
                self.assertNotIn('tools', second)

    def test_no_search_result_prevents_model_call(self):
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'test', 'GEMINI_API_KEY': 'test'}):
            with patch('public_web.urllib.request.urlopen', return_value=BytesIO(b'{"results": []}')) as send:
                with self.assertRaises(AdviceUnavailable):
                    public_web_answer('Who is NVIDIA CEO?', consent=True)
                self.assertEqual(send.call_count, 1)

    def test_links_reject_unsafe_destinations(self):
        for url in ['javascript:alert(1)', 'http://example.com', 'https://127.0.0.1/',
                    'https://localhost/', 'https://user:password@example.com/']:
            self.assertFalse(safe_source(url))
        self.assertTrue(safe_source('https://www.nvidia.com/about/'))
