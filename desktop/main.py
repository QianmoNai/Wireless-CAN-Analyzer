import sys
# Bleak's WinRT backend runs in an asyncio worker, requiring MTA rather than STA.
if sys.platform == 'win32':
    sys.coinit_flags = 0
from can_scope.app import main
if __name__ == '__main__':
    raise SystemExit(main())
