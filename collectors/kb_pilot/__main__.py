import os
import sys

if os.environ.get("KB_MODE") == "probe-cloudflare":
    from .probe import cloudflare
    sys.exit(cloudflare())

if os.environ.get("KB_MODE") == "probe":
    from .probe import main
    sys.exit(main())

from .pilot import main

sys.exit(main())
