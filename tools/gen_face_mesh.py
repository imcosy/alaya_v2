"""生成右上角"线框人头"用的稀疏网格文件（public/face_mesh.mesh）。

为什么要有这个脚本：网格是**离线预计算**的产物，前端只做投影渲染（不引 Three.js）。
换头模时用同一套参数重跑即可，不要在别处手工造数据。

用法：
    python3 tools/gen_face_mesh.py <head.obj> public/face_mesh.mesh

当前用的头模（three.js 示例里的开源头部，面朝 -x；0° 视角即正脸）：
    https://raw.githubusercontent.com/mrdoob/three.js/dev/examples/models/obj/walt/WaltHead.obj

三个关键参数（都是踩出来的）：
  Q=30   顶点聚类量化格数。不聚（8146 顶点 / 21017 边）糊成一团；聚到 13³（425 节点）/ 22³（1130 节点）
        太粗——格子边长 1/22≈0.045，而眼睛宽约 0.06，**不到 1.5 格，五官直接被抹平**（这正是
        "正脸了但没五官"的原因）；30³ 能留住眼窝/鼻/嘴，边数也不至于糊（约 1900 节点 / 5800 边）。
  法线   离线算好顶点法线一起存进文件——前端靠它做**光照**（光从左上前方来）。
         注意前端还要对光照值取 gamma（^0.65）抬中间调，否则五官全落在最暗那一档、等于看不见。
  ⚠️ 输出文件名用 .mesh 而不是 .json：静态服务器/反代常有针对 .json/.txt/.md 的
     全局规则，会把请求指到别的目录（前端 404 拿到 HTML）。

已否决的两条路（别再走）：
  · 按曲率挑边（top-N 稀疏化）→ 选中的多是模型接缝噪声，糊成斑块；
  · 只按景深做明暗 → 正脸时五官起伏沿视线方向，几乎不产生明暗差，读不出脸。
"""
import json
import math
import sys

Q = 30                    # 顶点聚类量化格数（22 太粗会把五官抹平，见文件头说明）
DEC = 4                   # 坐标保留小数位


def main(src: str, dst: str) -> None:
    V, F = [], []
    for line in open(src):
        if line[:2] == 'v ':
            p = line.split()
            V.append([float(p[1]), float(p[2]), float(p[3])])
        elif line[:2] == 'f ':
            ix = [int(t.split('/')[0]) - 1 for t in line.split()[1:]]
            for k in range(1, len(ix) - 1):
                F.append((ix[0], ix[k], ix[k + 1]))

    # 归一化到 ±0.5（渲染端只管比例，不关心真实尺度）
    xs = [p[0] for p in V]; ys = [p[1] for p in V]; zs = [p[2] for p in V]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    cz = (min(zs) + max(zs)) / 2
    sc = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    V = [[(p[0] - cx) / sc, (p[1] - cy) / sc, (p[2] - cz) / sc] for p in V]

    # 顶点法线 = 相邻三角面法线之和归一化
    NV = [[0.0, 0.0, 0.0] for _ in V]
    for a, b, c in F:
        ax, ay, az = V[a]; bx, by, bz = V[b]; dx, dy, dz = V[c]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = dx - ax, dy - ay, dz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for i in (a, b, c):
            NV[i][0] += nx; NV[i][1] += ny; NV[i][2] += nz
    for n in NV:
        L = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1.0
        n[0] /= L; n[1] /= L; n[2] /= L

    # 顶点聚类：量化到 Q³ 格，格内取均值（得到稀疏"有机"网格）
    acc = {}
    for i, p in enumerate(V):
        acc.setdefault((round(p[0] * Q), round(p[1] * Q), round(p[2] * Q)), []).append(i)
    k2n, nodes, nrm = {}, [], []
    for k, ids in sorted(acc.items()):
        k2n[k] = len(nodes)
        nodes.append([round(sum(V[i][j] for i in ids) / len(ids), DEC) for j in range(3)])
        nx = sum(NV[i][0] for i in ids); ny = sum(NV[i][1] for i in ids); nz = sum(NV[i][2] for i in ids)
        L = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nrm.append([round(nx / L, 3), round(ny / L, 3), round(nz / L, 3)])

    # 边：面的三条边映射到聚类后的节点，去重（自环丢弃）
    es = set()
    for a, b, c in F:
        na = [k2n[(round(V[i][0] * Q), round(V[i][1] * Q), round(V[i][2] * Q))] for i in (a, b, c)]
        for m in ((0, 1), (1, 2), (2, 0)):
            i, j = na[m[0]], na[m[1]]
            if i != j:
                es.add((min(i, j), max(i, j)))
    es = sorted(es)

    out = {
        'v': [x for n in nodes for x in n],
        'n': [x for n in nrm for x in n],
        'e': [x for e in es for x in e],
    }
    with open(dst, 'w') as f:
        json.dump(out, f, separators=(',', ':'))
    print("节点 %d / 边 %d → %s" % (len(nodes), len(es), dst))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
