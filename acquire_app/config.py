import sys
from pathlib import Path

# 运行模式:
#   源码 (开发): ROOT_DIR = 仓库根
#   PyInstaller 打包: ROOT_DIR = exe 所在目录 (images/ 和 logs/ 落在用户看得见的地方)
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).parent.parent

IMAGES_DIR = ROOT_DIR / "images"
LOGS_DIR = ROOT_DIR / "logs"

IMAGES_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

APP_NAME = "LumoAcq"
APP_VERSION = "0.1.0"

# 默认采集参数
DEFAULT_PIXEL_FORMAT = "Mono12"
DEFAULT_EXPOSURE_US = 20000.0
DEFAULT_GAIN_DB = 0.0
DEFAULT_FRAME_RATE_HZ = 15.0
DEFAULT_BINNING = 1

# 预览
PREVIEW_MAX_SIDE = 1024
PREVIEW_FPS = 30   # UI 刷新上限 (超过人眼需求意义不大, 留余量防超量率 GUI 卡顿)

# TIFF 压缩
TIFF_COMPRESSION = "zlib"
TIFF_COMPRESSION_LEVEL = 6
