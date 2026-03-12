import tempfile
from pathlib import Path

INPUT_FILE = "vless_list.txt"
DEFAULT_TEST_URL = "https://httpbin.org/ip"
# DEFAULT_TEST_URL = "https://www.gstatic.com/generate_204"
LOG_DIR = Path(tempfile.gettempdir()) / "xray_vless_tester_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_PROXY_BASE_PORT = 10808
DEFAULT_MAX_CONCURRENT_CHECKS = 5
DEFAULT_CURL_TIMEOUT_SECONDS = 20.0
DEFAULT_DNS_RETRIES = 1
DEFAULT_DNS_RETRY_INTERVAL_MS = 250
