"""让 `python -m app.api` 可执行。"""

import sys

from app.api.server import main

if __name__ == "__main__":
    sys.exit(main())
