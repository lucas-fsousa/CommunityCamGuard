"""Stdlib-only resolver child. No application imports, credentials or logging."""

import json
import socket
import sys


def main() -> int:
    try:
        records = socket.getaddrinfo(sys.argv[1], int(sys.argv[2]),
                                     socket.AF_INET, socket.SOCK_DGRAM)
        hosts = sorted({str(item[4][0]) for item in records})
        if not 1 <= len(hosts) <= 16:
            return 1
        sys.stdout.write(json.dumps(hosts))
        return 0
    except (OSError, ValueError, IndexError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
