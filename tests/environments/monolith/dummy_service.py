import time
import signal
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleService(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Hello from Monolith")

def run_server():
    server = HTTPServer(('localhost', 8080), SimpleService)
    print("Monolith Service running on port 8080...")
    server.serve_forever()

if __name__ == '__main__':
    # Handle signals gracefully
    def handler(signum, frame):
        print(f"Received signal {signum}, shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    
    run_server()



