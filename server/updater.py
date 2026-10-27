# updater.py — 从 GitHub 拉取更新
#
# 安装方式决定能不能自动更新：git clone 的可以（走 git），下载 zip 解压的不行。
# 更新只有一条路径：`git fetch` 看落后多少 → `git pull --ff-only` 快进。
# 本地有未提交改动时 fast-forward 会失败——那就如实告诉用户去 commit/stash，
# 绝不用 reset --hard 覆盖别人的修改。
import os
import sys
import shutil
import logging
import asyncio
import threading
import subprocess

log = logging.getLogger("alaya.updater")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GIT_TIMEOUT = 180


def _git(*args: str, timeout: int = _GIT_TIMEOUT) -> tuple[int, str, str]:
    r = subprocess.run(["git", "-C", ROOT, *args], capture_output=True,
                       text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def is_git_repo() -> bool:
    return bool(shutil.which("git")) and os.path.isdir(os.path.join(ROOT, ".git"))


def version() -> str:
    if not is_git_repo():
        return "unknown"
    rc, out, _ = _git("describe", "--tags", "--always", "--dirty")
    return out if rc == 0 and out else "unknown"


def _check_sync() -> dict:
    if not is_git_repo():
        return {"ok": False, "version": version(),
                "error": "当前目录不是 git 仓库（zip 解压安装的版本无法自动更新，请改用 git clone）"}

    rc, url, _ = _git("remote", "get-url", "origin")
    if rc != 0:
        return {"ok": False, "version": version(), "error": "没有配置 origin 远程仓库"}

    rc, upstream, err = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc != 0:
        return {"ok": False, "version": version(),
                "error": f"当前分支没有跟踪上游（{err or '未设置 upstream'}）"}

    rc, _, err = _git("fetch", "--prune", "origin")
    if rc != 0:
        return {"ok": False, "version": version(), "error": f"拉取远程失败：{err}"}

    rc, local, _ = _git("rev-parse", "HEAD")
    rc2, remote, _ = _git("rev-parse", upstream)
    if rc != 0 or rc2 != 0:
        return {"ok": False, "version": version(), "error": "无法解析本地/远程提交"}

    _, behind_s, _ = _git("rev-list", "--count", f"HEAD..{upstream}")
    behind = int(behind_s or 0)
    commits: list[dict] = []
    if behind:
        _, log_s, _ = _git("log", "--oneline", "--no-decorate", "-n", "30", f"HEAD..{upstream}")
        for line in log_s.splitlines():
            sha, _, subject = line.partition(" ")
            commits.append({"sha": sha, "subject": subject})

    _, dirty_s, _ = _git("status", "--porcelain")
    return {
        "ok": True,
        "version": version(),
        "branch": upstream,
        "repo": url,
        "local": local[:7],
        "remote": remote[:7],
        "behind": behind,
        "commits": commits,
        "dirty": bool(dirty_s),
    }


def _apply_sync() -> dict:
    if not is_git_repo():
        return {"ok": False, "error": "当前目录不是 git 仓库，无法自动更新"}

    _, dirty_s, _ = _git("status", "--porcelain")
    if dirty_s:
        return {"ok": False,
                "error": "本地有未提交的改动，已停止更新。请先 git stash 或 git commit，再重试"}

    before = version()
    rc, out, err = _git("pull", "--ff-only")
    if rc != 0:
        return {"ok": False, "error": f"更新失败：{err or out}"}
    return {"ok": True, "before": before, "after": version(), "output": out}


async def check() -> dict:
    return await asyncio.to_thread(_check_sync)


async def apply() -> dict:
    return await asyncio.to_thread(_apply_sync)


def schedule_restart(delay: float = 1.0) -> None:
    """把当前进程原地替换成新代码（同一个 python、同样的启动参数、同一个端口）。

    收到 /api/update/apply 的响应后才触发：先让 HTTP 响应发出去，再 execv。
    """
    argv = [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]

    def _exec():
        try:
            log.warning("[updater] 代码已更新，进程原地重启")
            os.execv(sys.executable, argv)
        except Exception as e:
            log.error("[updater] 自动重启失败，请手动重启服务：%s", e)

    threading.Timer(delay, _exec).start()
