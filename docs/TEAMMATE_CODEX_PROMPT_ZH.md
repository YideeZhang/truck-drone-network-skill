# 可直接交给队友 Codex 的完整执行 Prompt

开发/共享环境基线：`C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`；解释器 `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv/Scripts/python.exe`。这是作者机器上的环境。你若在另一台机器，必须使用并记录当地用户已确认的 Python 3.12 解释器，可以考虑补充所需要的库。

以下内容可整体作为一条 Codex 用户消息发送。仓库已经公开可见。

---

你是本项目的 Parameter and Network Agent。请使用 `$build-truck-drone-network`，从真实来源数据构建一个新西兰完整行政区的可追溯 truck–drone network，并按当前 Wuding 的 base/networks/scenarios 组织方法打包，保留后续切割小网络所需的完整母图。

## A. 获取代码并确认可运行性

1. 如果尚未 clone，执行 `git clone https://github.com/YideeZhang/truck-drone-network-skill.git` 到用户允许的短路径，例如 `C:/tdn`。如果仓库已存在，不覆盖本地改动；确认 origin、当前 commit 和工作区状态。若 404/认证失败，报告“需要私有仓库读取权限”，不要另找同名第三方代码。
2. 在克隆仓库中完整阅读 `README.md`、`AGENTS.md`、`.agents/skills/build-truck-drone-network/SKILL.md` 及其必读 references，还有 `docs/NEW_ZEALAND_DATA_GUIDE.md`。使用仓库中的 portable v2，不调用作者私有项目的脚本或已生成 Wuding 数据。
3. 审计本机 Python/依赖，运行小型合成 forward test。作者共享环境为 `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`；其他机器选择已有合适解释器并记录。缺包时列明必要版本/用途，取得安装许可后仅在该解释器安装。不要自动创建 venv；不要跑 Gurobi、Model、训练或修改论文。
4. 使用短 workspace，比如 clone 内 `workspaces/nz`；原始数据和生成结果不得提交进源码 Git。后续实际命令要替换占位符，不要把 `<profile>` 作为真实路径运行。

## B. 用证据选一个合理的新西兰完整行政区

武定比较基准：项目研究边界面积 **2,943.303294 km²**、2020 年官方人口汇总 **239,059**。明确该边界来自既有研究数据，不能称为测绘级官方边界。

请联网调查至少三个实际 Territorial Authority，优先比较 Dunedin City、Whangārei District、Rotorua District，也可加入其他更合适候选。必须从 Stats NZ/council 等主来源获取行政代码、年份/人口口径和边界，从 DEM/道路数据验证地形与交通。不要用 POI 中心城区冒充整个行政区。

先输出 `area_selection.csv` 和简短说明：面积、人口、两者相对武定的比值、地形起伏/坡度分位数、居民点分布、道路结构/数据覆盖、选择理由与显著差异。Dunedin 可作为优先候选，但不能硬选或声称人口相同；官方历史面积约3340 km²、2023通常居住人口128901只是待复核的初筛数。

如果不存在面积和人口均接近的合理 TA，明确说明。可以提出“面积更接近、人口约半数”的折中案例供我确认，不要为了凑相似性修改真实数据、拼成无正式行政含义的区域或删减需求点。没有证据不直接写“具有代表性”。

## C. 获取并保留真实 raw data

至少准备：行政边界、实际道路、人口 counts 栅格、DEM、官方人口表、当地居民用电/户均人口依据、明确的 Depot 坐标。可选下级行政/统计分区用于未来切小图。

按 `docs/NEW_ZEALAND_DATA_GUIDE.md` 使用真实来源：

- Stats NZ Geographic Data Service：Territorial Authority polygon（精确代码/年份）及可用的 ward/community board/经批准SA2分区；GeoPackage或完整SHP文件。
- Geofabrik New Zealand：原始 OSM `.osm.pbf`（优先）或 GPKG/SHP，保留原 way ID、highway、oneway、access、bridge/tunnel/layer、name/ref 等。不要用截图、地图瓦片或人工画的连接线代替实际道路。
- WorldPop：NZL、所选年份、约100m **population counts/people per cell** GeoTIFF，带产品版本和许可；不能用密度图、RGB图或只有一个行政区总人口的 API 响应替代栅格。
- LINZ：覆盖全行政区、道路buffer及飞行走廊外接范围的 bare-earth DEM/DTM GeoTIFF，保留原始tiles与水平/垂直基准、NoData、年代/分辨率。不是DSM/hillshade。登录/导出受限时让我手动下载。
- Stats NZ 官方人口/家庭数或户均人口；MBIE或当地官方居民用电表。把 kWh/connection 换为 kWh/household 时必须有证据和清晰计算；不能静默套用中国户均人口和用电量。

如果任何数据需要我自行下载，请当场明确列出：**网站链接、精确数据集/年份、所需区域/buffer、格式/类型、单位、CRS/垂直基准、保存绝对路径、应一并保存的许可元数据，以及缺失会阻塞哪一步**。不要只说“缺DEM”或编造一个未验证的下载链接。

原始文件放 `data/raw/<region>/`，格式转换/提取结果放 `data/prepared/<region>/`；不覆盖原片。所有实际使用的文件都写进 profile.inputs，记录来源/时间/许可/hash及是否允许再分发。无需为了 route 反复调用商业API：truck route 由真实道路图计算，保留真实道路edge序列和几何；不得把它称为GPS实测轨迹。NZ禁止使用AMap。

## D. 参数与空间质量门

以 `assets/profiles/nz_template.json` 建立独立区域配置。区域水平计算坐标通常用 EPSG:2193，交换图层用EPSG:4326；检查数据自身CRS后再转换，不能只改标签。DEM垂直基准必须单独确认。零米/负海拔不自动当NoData。

1. 人口100m、8邻域、加权质心；原网格约100m则保留，否则sum重采样并验总量；官方总人口比例校准要写明跨年问题。Depot所在人口分量未受灾，在该network中没有goods/energy需求或人口评分。
2. 若正人口阈值0导致整个行政区连成一个分量，停止报告分量数/人口分布，讨论合理阈值或聚合定义；不能默默调阈值/裁成20点。新聚合尺度必须审批且守恒/披露剔除量。
3. OSM优先，需求分母不含Depot；距可用道路>200m或无法双向连接Depot均不可服务，恰200m为覆盖；不可服务比例>=20%则回退许可明确的LINZ/当地道路源。Depot本身不能失败。回退还失败就停下来列明未解点，不造路、不删除需求。
4. truck路网仅secondary/residential两类；明确NZ当地等级到两类的映射，不机械套用武定secondary定义。短夹段修正规则必须在profile；保留原始等级/依据。未知重卡限重/宽度/转向限制不代表允许通行。
5. 区分服务/customer原始质心与实际道路truck anchor。相同anchor不能产生零时间self arc，也不能悄悄合并两笔人口/货物。无人机customer仍用原质心。
6. 卡车/无人机可以先用仓库注明的同平台研究代理参数作为可比实验方案，但先给我一张参数与来源表确认；不能称为NZ实测值。NZ户均人口、居民用电必须另找依据。12h/35%、2kg/person/24h、45min且10km、速度、效率、飞行clearance和reserve都是需要显式批准的研究配置。

## E. 先全行政区母图，再生成全区网络和切片

先运行 `network_pipeline.py --stage mother`，保存完整选定源道路几何、节点、真实edge/source lineage、一次DEM采样的physical profiles、directed nominal time/distance/energy、全区稳定population terminal master与客户坐标。

母图不等于一个已完成服务网络。检查合格后，在 profile.scopes 中加入 **覆盖全部行政区需求的 full scope**，明确一个真实、已批准的Depot。运行 `--stage full --mother-root <mother-release>` 生成完整逻辑/能耗/微网/无人机网络。

需要小网络时，按真实下级分区的相邻关系配置1/2/3/4/5分区组合（实际分区数量不足则如实说明），每个已批准的分区中心Depot候选可形成一个network变体，但每个network里只有一个active Depot。不要为了迁就预设数量随机裁点。暂未决定切片时，至少完成全区scope与可切割母图、分区身份映射和后续切片示例。

切片必须复用物理母图，然后**按当前切片的service/truck-anchor集合重新运行strict-direct Dijkstra**：其他当前服务锚点不能当内部节点，普通物理道路节点可以；最小时间→最小距离→有向物理arc-ID序列字典序。不能直接裁剪大图/C2最终logical arcs，因为服务blocker改变后可行路径会变。

## F. 完成能耗、服务与可选情景

卡车：按道路类别速度算时间；DEM按物理edge采样并三点平滑；能耗=距离项+累计正爬升势能/效率，不使用端点净高差替代累计爬升。反向复用同profile逆序，不计下坡回收。保留距离/时间/爬升/下降/能耗及有序lineage。

能源服务：对每个Depot变体重算microgrid候选、最少site数→人口加权服务时间→稳定ID平局；同一canonical路径同时满足时间和道路长度上限，每个有效需求唯一分配。保留local goods population与energy catchment population的区别、守恒和incident backbone。覆盖是道路服务聚类，不是跨村物理输电或容量可行证明。

无人机：使用简化固定巡航高度三阶段方法，不添加温度/风/湿度/空气密度等新假设。从全需求表生成空载/满载raw时间与能耗，含卡车延期点。明确两个接口：

- 角色化原始飞行腿 `2*N*N`：满载road anchor→customer centroid，空载customer centroid→同anchor；
- 原始customer质心两两候选 `2*N*(N-1)`：两方向×两载荷，用于地图和直接飞行矩阵。

单趟20%预留的必要能量门限与满载去/空载回的往返总预算分开，raw leg不扣reserve。不生成Gurobi任务槽/assignment，不声称航路获批。

若我批准生成情景，再启用 `scenarios.enabled=true` 和相应review状态，使用固定全行政区F_ref、edge内部坡度、connection级随机量，生成mild/moderate/severe×2共6个**实验**情景。否则保持scenario registry零行，不伪造真实灾害记录，也不自行启动Model或训练。

## G. 最终结构、验证和交付

输出必须以 `network/processed/<region>/<release>/registry/` 统一发现，含完整 `mother_network/`，并按 `<k>_town/<scope>/base/`、`networks/<network_id>/`、`scenarios/portable_g2_v2/<network_id>/` 组织。不要将NZ输出命名成wuding或伪造原Wuding IDs。

完整交付：真实输入清单与许可、参数快照、母图与可切割身份映射、全行政区network、客户质心/锚点、道路profile和严格路径、卡车时间能耗矩阵、goods/energy/microgrid表、无人机两载荷原始成本与单程/往返门限、可用ArcGIS的GPKG/GeoJSON与总览图、环境和运行manifest、数据字典/局限性。

运行独立验证与隔离重复测试：人口/货物/能源守恒；唯一Depot；全部主外键闭合；所有truck logical arc正距离/正时间；strict路径不穿越其他当前anchors；cost=sum(physical costs)；reverse证据真实；DEMprofile数组有限且距离递增；raw drone恰2N²并端点角色/坐标准确；单程不误当往返；所有runtime path指向本release，不依赖作者电脑、archive或staging。生成的科学CSV重复hash必须一致。超过规模/时间上限时报告，不暗改算法或删除节点。

用 `package_dataset.py` 打出新ZIP，保留相对目录结构。将所有许可允许的真实raw及必要prepared文件纳入，商业受限/过大不宜传的数据必须在manifest中列明原网站、精确产品/类型、时间/hash、保存路径和补下载方法。验证ZIP包含全行政区母图，不只是几张CSV或截图。最终给我包路径、各阶段PASS/FAIL、关键计数、地图及未解决事项。整个过程不更改MILP、求解算法或论文。

不要只提交泛泛计划；从代码/环境与数据可用性检查开始，有明确权限/数据/研究选择缺口时给出具体阻塞和我需要做的操作，其余步骤在授权范围内连续完成。
