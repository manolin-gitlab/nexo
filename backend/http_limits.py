"""Bound request threads. Reject overload without blocking the accept loop."""
import threading
from http.server import ThreadingHTTPServer


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    max_requests = 32

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(self.max_requests)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(.2)
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 2\r\nConnection: close\r\n\r\n')
            except OSError: pass
            self.shutdown_request(request)
            return
        try: super().process_request(request, client_address)
        except Exception:
            self.slots.release(); raise

    def process_request_thread(self, request, client_address):
        try: super().process_request_thread(request, client_address)
        finally: self.slots.release()
