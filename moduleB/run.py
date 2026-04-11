import os
import sys
from pathlib import Path

# 兼容 macOS 下 OpenMP 重复加载导致的进程中止问题
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# 兼容两种启动方式:
# 1) python3 moduleB/run.py
# 2) python3 -m moduleB.run
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moduleB.zmq_service import main


if __name__ == "__main__":
    main()
