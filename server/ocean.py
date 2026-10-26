# ocean.py — 意识海：习气网络所处的多维空间
#
# 唯识：阿赖耶识亦称「藏识海」——《楞伽经》「藏识海常住，境界风所动，种种诸识浪，腾跃而转生」。
#   种子 = 海中的点；关联边 = 点与点之间的引力；感官输入 = 风；主念涌现 = 海面起的浪。
#
# 一本体一片海：每个 AI 本体的全部种子（它自己的阿赖耶识所藏）构成属于它的一片意识海。
#   用户换一个 AI 本体对话，就是进入另一片海——两片海各自独立，互不渗透。
#
# 多维：种子在海中的位置不由力导向临时摆出，而由它的**多维指纹**决定。
#   海是同一片海，**观察维度决定怎么排布**——切一个维度，就换一种看海的坐标：
#     semantic 语义：按语义主成分铺开（意近者相邻）
#     time     时间：按距今天数分层（同期记忆沉在同一水层）
#     emotion  情绪：按情绪分簇（同一情绪的记忆某公司一团）
#     person   人物：按人物分簇（围绕同一人的记忆某公司一团）
#     scene    场景：按场景分簇（家庭/职场/社交/医疗/出行/其他）
from __future__ import annotations

import numpy as np

from . import retrieval

VIEWS = ("semantic", "time", "emotion", "person", "scene")


def _radius(n: int) -> float:
    """海半径：随种子规模增长（沿用既有视觉尺度，避免切换视角时画面突变）"""
    return 300.0 + (n ** 0.5) * 62.0


def _pca3(mat: np.ndarray) -> np.ndarray:
    """语义向量 → 海中前三个主方向（中心化后 SVD，不足三列补零）"""
    n = mat.shape[0]
    out = np.zeros((n, 3), dtype=np.float32)
    if n < 2 or mat.shape[1] < 1:
        return out
    x = mat - mat.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    k = min(3, vt.shape[0])
    out[:, :k] = x @ vt[:k].T
    return out


def _spread(p: np.ndarray, scale: float) -> np.ndarray:
    """把散布按各轴最大绝对值归一到 ±scale（确定性，不引入随机）"""
    if p.size == 0:
        return p
    m = np.abs(p).max(axis=0)
    m[m < 1e-6] = 1.0
    return (p / m * scale).astype(np.float32)


def _sphere_centers(k: int, radius: float) -> np.ndarray:
    """黄金角螺旋：把 k 个簇心均布在球面上（各簇互不重叠）"""
    out = np.zeros((k, 3), dtype=np.float32)
    if k < 2:
        return out
    ga = np.pi * (3 - np.sqrt(5))
    for i in range(k):
        y = 1 - 2 * (i + 0.5) / k
        r = np.sqrt(max(0.0, 1 - y * y))
        a = i * ga
        out[i] = (radius * r * np.cos(a), radius * y, radius * r * np.sin(a))
    return out


def _bucket_time(seed_date) -> str:
    """时间维度分层：越久远沉得越深（相对天数分桶）"""
    from .seeds import rel_days
    d = rel_days(seed_date)
    if d is None:
        return "未定"
    if d <= 7:
        return "一周内"
    if d <= 30:
        return "一月内"
    if d <= 90:
        return "三月内"
    if d <= 365:
        return "一年内"
    return "一年前"


def ocean_coords(rows: list[dict], view: str = "semantic") -> dict[int, list[float]]:
    """种子 → 意识海三维坐标（观察维度决定排布方式）

    rows: [{seed_id, seed_type, seed_date, person_id, emotion_word, scene}, ...]
    view: semantic | time | emotion | person | scene

    语义散布是所有视角的"基底"——无论按哪个维度分层/分簇，簇内仍按语义散开，
    这样"意近者相邻"始终成立，切维度只是换了一层更大的结构。
    """
    n = len(rows)
    if n == 0:
        return {}
    R = _radius(n)
    ids = [int(r["seed_id"]) for r in rows]

    vecs = retrieval._vecs
    dim = next((len(v) for v in vecs.values()), 0)
    mat = np.zeros((n, max(dim, 1)), dtype=np.float32)
    if dim:
        for i, sid in enumerate(ids):
            v = vecs.get(sid)
            if v is not None:
                mat[i] = v
    if view == "semantic" or view not in VIEWS:
        P = _spread(_pca3(mat), R * 0.8)
        return {ids[i]: [float(P[i][0]), float(P[i][1]), float(P[i][2])] for i in range(n)}

    P = _spread(_pca3(mat), R * 0.16)      # 簇内散布
    if view == "time":
        key_fn = lambda r: _bucket_time(r.get("seed_date"))
    elif view == "emotion":
        key_fn = lambda r: (str(r.get("emotion_word") or "").strip() or "平静")
    elif view == "person":
        key_fn = lambda r: str(int(r.get("person_id") or 0))
    else:                                   # scene
        key_fn = lambda r: (str(r.get("scene") or "").strip() or "其他")

    groups: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(key_fn(r), []).append(i)
    keys = sorted(groups.keys())
    centers = _sphere_centers(len(keys), R * 0.66)
    out: dict[int, list[float]] = {}
    for ci, k in enumerate(keys):
        if len(keys) == 1:                  # 只有一个簇：直接用语义散布，不必强拉到一个点
            cx = cy = cz = 0.0
        else:
            cx, cy, cz = centers[ci]
        for i in groups[k]:
            out[ids[i]] = [float(cx + P[i][0]), float(cy + P[i][1]), float(cz + P[i][2])]
    return out
