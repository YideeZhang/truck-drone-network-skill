# Portable v2 验证范围与结果

开发/共享环境：`C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`；本轮解释器：`C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv/Scripts/python.exe`。日期：2026-08-28。未安装或升级依赖，未新建venv。

本报告针对**代码可移植性与合成数据科学接口**，不是新西兰实地数据验收，也不是历史武定全部生产结果的重新回归。

## 执行验证

- Skill 官方 `quick_validate.py`：PASS。
- Python 测试：20 passed，0 failed，0 errors，0 skipped；原始记录见 `validation/pytest_results.xml`。
- 独立母图/整范围/子范围生成：PASS；严格路径和两载荷能耗、人口/能源守恒、客户质心/道路锚点分离、可选G2情景和可移交ZIP均实际执行。
- 隔离重复运行：82 个非路径型科学 CSV 哈希一致；发布目录不同导致registry路径不同是预期，不比较GPKG/ZIP二进制时间戳。
- 母图复用：核心物理边、方向成本、高程profile和terminal master哈希一致；未重新抓路、未重新计算母图道路DEM。
- 源/数据保护：无AMap调用、无Model/Gurobi/训练；无原始武定数据、既有网络、布局或模型修改。没有创建/提升公开仓库权限。

## 合成前向样例（不是新西兰真实数据）

| 项目 | 完整合成范围 | 西侧子范围 |
|---|---:|---:|
| terminals（含1 Depot） | 9 | 6 |
| goods customers | 8 | 5 |
| 独立energy sites | 1 | 1 |
| strict directed truck arcs | 24 | 16 |
| role-aware raw drone legs（2N²） | 162 | 72 |
| centroid-to-centroid two-payload arcs（2N(N−1)） | 144 | 60 |
| generated research scenarios | 6 | 6 |

合成母图保留24条物理边、48条有向物理弧和9个客户分量。每个scope的path/backbone closure是其中12条边；保留母图中的其他真实源形状片段用于后续切片，不把closure误称为全域道路。

## 测试覆盖的错误类别

恰好2/10不可服务触发回退、1/10不触发；Depot不入分母但必须接入；恰200m/超过200m；道路虽近但不连通；8邻域对角合并；50m→100m人口sum守恒；官方校准总量与Depot排除；严格邻接与切片blocker改变；canonical平局；有向环路；方向爬升能耗；三点道路等级补缝；edge内部坡度而非跨edge拼接；固定CDF midrank；DEM零高程有效、NoData/越界阻断；单飞可行不等于往返可行；缺本地用电参数停止；NZ OSM失败安全停止、许可道路回退；公开下载人工清单/凭据URL阻止；重复输出不覆盖；bundle确含母图、许可允许的合成raw和脚本。

## 明确尚未验证的内容

没有下载/构建真实新西兰network；没有跑完整新西兰PBF规模性能测试；没有认证重卡通行、转向限制、供电容量、航路许可；没有证明同平台官方测试点能精确描述NZ山地飞行。没有运行原项目的production Model loader。Windows/Python3.12之外的平台需要队友自行forward test。

原 Wuding 代码/参数来源的必要hash见 `validation/source_origin.json`，只是移植来源证据，不产生对作者私有原路径的运行依赖。未来真实选区和输入质量仍按NZ指南逐项验收；不能用本报告的PASS代替那些检查。
