"""app/version.py —— 应用版本号的唯一来源。

运行期(源码 / exe)与发布脚本都从这里取值:

* 侧栏显示当前版本;
* 「检查更新」拿它和 GitHub 最新 tag 比对;
* tools/make_release.py 用它命名源码包。

发版只改这一处,再同步 git tag 与根 README 的「版本演进」。
不读 git tag:打出来的 exe 里没有 .git,源码运行也不该依赖 git 命令。
"""

from __future__ import annotations

__version__ = "1.5.1"

REPO_OWNER = "DonkeyHeng"
REPO_NAME = "Casebook"
RELEASES_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases"
LATEST_URL = f"{RELEASES_URL}/latest"
TAG_URL_PREFIX = f"{RELEASES_URL}/tag/"
