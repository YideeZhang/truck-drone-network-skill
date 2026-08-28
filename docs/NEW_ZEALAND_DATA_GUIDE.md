# 新西兰真实数据获取与选区说明

开发/共享环境：`C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`；解释器：`C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv/Scripts/python.exe`。队友在其他机器使用自己已确认的 Python 3.12 环境，记录绝对解释器路径；不要照搬作者的机器路径或静默安装。

## 1. 选整个行政区，先比较，不强称代表性

武定基准取自项目 `parameters/wuding_county_amap_master_v1.json`：2020 年七普乡镇汇总人口 **239,059**；11 个乡镇研究边界 dissolve 后面积 **2,943.303294 km²**。面积是该研究数据的测量结果，**不是官方精确县域面积声明**。本仓库不附带原始县边界或第三方商业路线。

优先比较新西兰 **Territorial Authority（区/市级行政区）**，而非一个城市 POI 的中心城区。建议至少比较 Dunedin City、Whangārei District、Rotorua District，再视数据加入 Christchurch City/其他候选。比较表应同时给出：行政代码/边界版本、面积与是否含水域、人口口径/年份、相对武定的两个比值、DEM 起伏和坡度分位数、城乡居民点分布、道路密度/等级、已知自然灾害证据和数据可用性。

**Dunedin 只是优先候选，不是预先确认的等价案例。** Dunedin 市政府 2016 pre-election report 给出 land area 3,340 km²，2023 Census 通常居住人口为 128,901（应在 Stats NZ 对应 TA 页及下载表复核）。按这些口径初筛，面积约为武定研究边界的 1.13 倍，人口约 0.54 倍；人口差异明显。不要写成“大小、人口基本相同”。Dunedin 包括中心城区与较广的乡村地带；地形与山区交通适用性必须通过实际 DEM/路网检验。

主来源：[Stats NZ place summaries](https://tools.summaries.stats.govt.nz/places/TA/dunedin-city)、[Dunedin official pre-election report (2016, land area)](https://www.dunedin.govt.nz/__data/assets/pdf_file/0006/334680/Pre-Election-Report-2016.pdf)、[Dunedin 2025 pre-election report](https://www.dunedin.govt.nz/resources/documents/council/pre-election-2013/dcc-pre-election-report-2025.pdf)。若网页/文件更新或无法访问，应回到官方站点找同一统计口径，不能编造下载成功。明确区分 2023 Census 与 2024/2025 resident population estimate。

不得为了匹配人口任意拼接/裁减行政区或删减需求节点。如果不存在同时满足预先约定面积/人口容差的 TA，报告候选权衡，请用户确认折中案例；不要伪造“相似性”。

## 2. 原始数据清单

| 数据 | 官方/开放入口 | 必须取得的内容 | 本地建议位置 / 人工步骤 |
|---|---|---|---|
| 行政边界 | [Stats NZ Geographic Data Service](https://datafinder.stats.govt.nz/)；其官方 ArcGIS 服务可作为备选 | 对应年份 Territorial Authority 全分辨率 polygon、TA code/name、CRS、许可元数据 | `data/raw/<region>/boundary.gpkg`。网站可能需要账号/手工选择下载；下载 GeoPackage 或完整 SHP 配套文件，不能拿屏幕截图。 |
| 下级划分 | 同一 Stats NZ 平台 | ward/community board 等真实划分；或经批准使用 SA2 分区，保留代码/年份 | `units.gpkg`；用作后续相邻小图切割，不把 SA2 直接称为乡镇。可先完成无下级划分的全区图。 |
| 实际道路 | [Geofabrik New Zealand](https://download.geofabrik.de/australia-oceania/new-zealand.html) | 原始 OSM `.osm.pbf` 优先，保留真实 way ID、highway、access、oneway、bridge/tunnel/layer、name/ref；也提供 GPKG/SHP | `new-zealand-<date>.osm.pbf`；保留原下载快照/hash。大文件不进源码 Git。运行 `prepare_osm.py` 提取选区和 buffer 到 `data/prepared/<region>/osm_roads.gpkg`。 |
| OSM 回退道路 | [LINZ Data Service](https://data.linz.govt.nz/) 或道路主管机关 | `NZ Road Centre Line` / licensed local roads 矢量及 ID/等级/方向/许可，**核对实际图层版本** | OSM 不可服务比例 >=20% 才启用。门户账号/导出可能需队友操作；无授权不得爬取商业底图或描图当真实路。 |
| 人口栅格 | [WorldPop Global2 portal](https://hub.worldpop.org/project/categories?id=3)、[WorldPop SDI](https://sdi.worldpop.org/) | NZL、所选年份、约100m、**people per pixel/counts** GeoTIFF，product release/constrained 状态/许可 | `population_counts_100m.tif`。不要下载 density、截图、RGB渲染或仅行政区总数。API 的 polygon population total 不能替代空间栅格。 |
| 官方人口/户数 | [Stats NZ census](https://www.stats.govt.nz/topics/census/)、[place summaries](https://tools.summaries.stats.govt.nz/) | 同 TA code 的 usually resident population 与适用 household size/count，注明年份/统计口径 | CSV/原始表及来源 URL。提供官方总人口用于比例校准；没有时 factor=1，明确为栅格估计。 |
| DEM | [LINZ elevation layers](https://data.linz.govt.nz/) | bare-earth DTM/DEM GeoTIFF tiles、水平/垂直基准、分辨率、采集日期、NoData。1m 数据过大可选适用的国家8m或已批准降采样方案 | `dem_tiles/`保留原片；`dem.tif`或 prepared mosaic。可以查询 [NZ 8m DEM (2012)](https://data.linz.govt.nz/layer/51768-nz-8m-digital-elevation-model-2012/)，但是否采用取决于覆盖/年代/基准。不要用 DSM、hillshade 或地图颜色。 |
| 居民用电 | [MBIE electricity statistics](https://www.mbie.govt.nz/building-and-energy/energy-and-natural-resources/energy-statistics-and-modelling/energy-statistics/electricity-statistics)；Stats NZ 或当地官方能源报告 | 年居民用电、同口径家庭/用户数、kWh 和年份。connection 不必等于 household | 保存原表。不能把武定 2.90 人/户、2187.06 kWh/户年直接用于 NZ。需要解释从原表推算的方式。 |
| 路线 route | 本项目的真实道路有向图 | 严格路径的 ordered physical arc/edge/node sequence 与真实路径几何 | 由 `fixed_path_lineage.csv` / `truck_strict_paths.geojson` 生成。它是计算路线，不是 GPS 实测轨迹；不需要额外反复请求商业 OD API。 |
| 灾害记录（可选） | 当地 council、[GNS Science](https://www.gns.cri.nz/) 等权威来源 | 有日期、类型、位置、来源许可的真实记录 | 不影响当前能耗构建的必备输入；坡度生成情景必须标记 generated，不包装成历史道路中断。 |

网站入口于 2026-08-28 检查；部分门户依赖登录或动态页面。本仓库提供获取指引，**不声称已为 NZ 下载这些数据**。图层 ID、下载 URL、许可证和实际数据日期必须在队友运行时再次核对。

## 3. 空间处理中的必要检查

- NZ 主岛区域通常可使用 **NZGD2000 / New Zealand Transverse Mercator 2000, EPSG:2193**。读取文件自带 CRS 后再投影；不要用 Define Projection 强行改标签。WGS84 GeoJSON 仅作交换/显示。
- DEM 的垂直基准不能仅凭水平 EPSG 推断。不同 LiDAR tile、NZVD2016、历史 local vertical datum 不可混用后忽略；查元数据。零米和负海拔可以真实存在，不能统统作 NoData。所选无人机 altitude limit 与 DEM 高程基准的近似关系必须说明。
- 路网需涵盖行政区外的必要绕行 buffer。DEM 必须覆盖所有真实道路段和任意客户/锚点直线飞行走廊及双线性插值边缘；裁成不规则县界会令穿越县外的飞行采样缺失，优先保留外接矩形/所需 envelope 的原片。
- 人口重采样是 `sum`，DEM 重采样/拼接是连续高程处理；不要混淆。原始DEM片保留，预处理文件和操作配置也登记进 inputs。
- 默认人口阈值为0。若 WorldPop 在整个区域存在连续微小正值导致一个巨型分量，**停止并报告**。是否改阈值/聚合定义需要用户确认，不能偷偷删人口来生成更漂亮的村庄点。
- 坐标相近不代表道路同一；不要把两条平行车道或跨层道路平均成一条。用源道路属性/图层分隔 noding，审计不连通、悬挂和边界截断。

## 4. 让 Codex 明确请求手工下载

当无法自动获得数据时，输出一条可操作请求：

> 请在 `<官方页面>` 搜索 `<精确数据集名称和年份>`，选择 `<TA code/name + buffer>`，导出 `<GPKG / counts GeoTIFF / DEM GeoTIFF>`；同时下载许可和CRS/垂直基准元数据，保存到 `<绝对路径>`。当前缺失会阻止 `<需求/道路/高程>` 阶段；不需要你预先裁到小乡镇。

不要只写“需要人口/DEM”，不要要求下载来路不明的百度云网盘，不在报告中保存密钥、登录cookie或签名下载URL。

## 5. 数据打包

`package_dataset.py --include-permitted-raw` 只会纳入 inputs 中 `redistribution_permitted=true` 的匹配源文件；需先完成真实许可审核。所有其余源都会进入 `DATASET_BUNDLE_MANIFEST.json` 的 omitted_inputs，附网站/数据类型/时间/校验值。未获许可的 AMap 响应始终不在公共可迁移包中。最终ZIP需要包含母图、全行政区 network、小图（如已批准生成）、车辆与无人机成本、路线/高程证据、配置、脚本和获取说明；大数据通过团队存储/允许的发布附件传递，而不是强推普通 Git。
