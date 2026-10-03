import time
import logging
import threading
from core.config import settings
from django.core.cache import cache
from django.http import JsonResponse

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT = 60
DEFAULT_WINDOW = 60


class RateLimitMiddleware:
    _locks: dict[str, threading.Lock] = {}
    _locks_lock = threading.Lock()
    _MAX_LOCKS = 1000

    def __init__(self, get_response):
        self.get_response = get_response
        self.rate_limits = {
            '/api/chat/ask': (20, 60),
            '/api/chat/stream': (20, 60),
            '/api/documents/upload': (10, 60),
            '/api/auth/register': (5, 60),
            '/api/auth/login': (10, 60),
        }

    def __call__(self, request):
        if not request.path.startswith('/api/'):
            return self.get_response(request)

        if hasattr(request, '_rate_limit_exempt'):
            return self.get_response(request)

        ip = self._get_client_ip(request)
        path = request.path
        rate, window = self.rate_limits.get(path, (DEFAULT_RATE_LIMIT, DEFAULT_WINDOW))

        cache_key = f'ratelimit:{path}:{ip}'

        with self._get_lock(cache_key):
            request_data = cache.get(cache_key)
            now = time.time()

            if request_data is None or now - request_data['window_start'] > window:
                request_data = {'count': 0, 'window_start': now}

            request_data['count'] += 1
            cache.set(cache_key, request_data, timeout=window + 10)

        if request_data['count'] > rate:
            logger.warning(f'Rate limit exceeded for {ip} on {path}')
            return JsonResponse(
                {'detail': 'Too many requests. Please slow down.'},
                status=429,
            )

        response = self.get_response(request)
        response['X-RateLimit-Limit'] = str(rate)
        response['X-RateLimit-Remaining'] = str(max(0, rate - request_data['count']))
        return response

    def _get_client_ip(self, request):
        # Behind the hosting platform's reverse proxy REMOTE_ADDR is the proxy,
        # so the real visitor only exists in X-Forwarded-For.
        #
        # The LAST entry is the trustworthy one: the platform edge appends the
        # address it actually saw, and a client can only control the entries
        # that come BEFORE it. Reading the first entry — the intuitive choice —
        # is a rate-limit bypass: a caller sends `X-Forwarded-For: <random>` on
        # every request and gets a brand-new bucket each time, which disables
        # the limiter completely (5 registrations/minute, 20 questions/minute).
        # Taking the last hop errs toward sharing a bucket, which is safe; taking
        # the first hop errs toward unlimited access, which is not.
        if settings.TRUST_PROXY_HEADERS:
            forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
            if forwarded_for:
                client_ip = forwarded_for.rsplit(',', 1)[-1].strip()
                # A blank/garbage header must fall through: returning '' would
                # collapse every affected visitor into a single shared bucket,
                # which is exactly the bug this lookup exists to prevent.
                if client_ip:
                    return client_ip
        return request.META.get('REMOTE_ADDR', '0.0.0.0')

    @classmethod
    def _get_lock(cls, key: str) -> threading.Lock:
        if key not in cls._locks:
            with cls._locks_lock:
                if key not in cls._locks:
                    if len(cls._locks) >= cls._MAX_LOCKS:
                        oldest_key = next(iter(cls._locks))
                        del cls._locks[oldest_key]
                    cls._locks[key] = threading.Lock()
        return cls._locks[key]
