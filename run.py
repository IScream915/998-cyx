import os

# 兼容 macOS 下 OpenMP 重复加载导致的进程中止问题
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from zmq_service import main


if __name__ == '__main__':
    main()
