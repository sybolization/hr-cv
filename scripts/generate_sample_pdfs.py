"""生成 10 份内容更丰富、可跨多页的"扫描版"PDF 简历（图片型、无文字层），用于测试 OCR 模块。

用法：uv run python scripts/generate_sample_pdfs.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path("sample_resumes")
FONT = "C:/Windows/Fonts/msyh.ttc"

# 图片分辨率（A4 / 150dpi 轮廓）
W, H = 1240, 1754
MARGIN = 100


def _fonts():
    return {
        "title": ImageFont.truetype(FONT, 44),
        "sub": ImageFont.truetype(FONT, 30),
        "head": ImageFont.truetype(FONT, 40),
        "body": ImageFont.truetype(FONT, 28),
    }


class Canvas:
    """简单的文本排版画布，逐行绘制，按需自动分页。"""

    def __init__(self):
        self.pages = [Image.new("RGB", (W, H), "white")]
        self.draw = ImageDraw.Draw(self.pages[0])
        self.y = MARGIN
        self.fonts = _fonts()

    def new_page(self):
        self.pages.append(Image.new("RGB", (W, H), "white"))
        self.draw = ImageDraw.Draw(self.pages[-1])
        self.y = MARGIN

    def space(self, px):
        self.y += px
        if self.y > H - MARGIN:
            self.new_page()

    def line(self, text, font_key="body", color="black"):
        f = self.fonts[font_key]
        for row in self._wrap(text, f):
            if self.y + f.size > H - MARGIN:
                self.new_page()
            self.draw.text((MARGIN, self.y), row, font=f, fill=color)
            self.y += f.size + 14
        self.y += 6

    def rule(self):
        if self.y > H - MARGIN:
            self.new_page()
        self.draw.line((MARGIN, self.y, W - MARGIN, self.y), fill="gray", width=2)
        self.y += 26

    def _wrap(self, text, font):
        rows, cur = [], ""
        max_w = W - 2 * MARGIN
        for ch in text:
            if self.draw.textlength(cur + ch, font=font) <= max_w:
                cur += ch
            else:
                rows.append(cur)
                cur = ch
        if cur:
            rows.append(cur)
        return rows


def render(c: dict, canv: Canvas):
    # 头部
    canv.line(c["name"], "title")
    canv.line(c["title"], "sub", color=(60, 60, 60))
    canv.line(c["contact"], "body", color=(60, 60, 60))
    canv.rule()

    # 个人简介
    canv.line("▍个人简介", "head")
    for s in c["overview"]:
        canv.line("· " + s, "body")
    canv.space(16)

    # 核心技能
    canv.line("▍核心技能", "head")
    for s in c["skills"]:
        canv.line("· " + s, "body")
    canv.space(16)

    # 工作经历
    canv.line("▍工作经历", "head")
    for e in c["experiences"]:
        canv.line("◆ " + e["company"] + " ｜ " + e["role"] + "（" + e["period"] + "）", "sub")
        for b in e["bullets"]:
            canv.line("   - " + b, "body")
    canv.space(16)

    # 项目经历
    if c.get("projects"):
        canv.line("▍项目经历", "head")
        for p in c["projects"]:
            canv.line("◆ " + p["title"] + "（" + p["tech"] + "）", "sub")
            for d in p["details"]:
                canv.line("   - " + d, "body")
        canv.space(16)

    # 教育背景
    canv.line("▍教育背景", "head")
    for e in c["education"]:
        canv.line("· " + e, "body")
    canv.space(16)

    # 证书 / 语言
    if c.get("certificates"):
        canv.line("▍证书 / 语言", "head")
        for s in c["certificates"]:
            canv.line("· " + s, "body")
    canv.space(10)


CANDIDATES = [
    {
        "name": "王小明", "title": "资深 Python 后端工程师", "years": 8,
        "contact": "电话: 138-0000-0001 ｜ 邮箱: wangxm@example.com ｜ 期望城市: 杭州 · 上海",
        "overview": [
            "8 年 Python 后端研发经验，深耕高并发、高可用系统架构与数据平台建设。",
            "主导过交易中台、消息链路、风险防控等多类核心系统，具备从 0 到 1 的架构设计能力。",
            "擅长性能优化、微服务治理与团队技术管理，带过 4-6 人小团队。",
        ],
        "skills": [
            "语言: Python(精通)、Golang(熟练)、SQL、Shell",
            "Web 框架: FastAPI、Django、Flask、gRPC、GraphQL",
            "中间件: Kafka、RabbitMQ、Redis、Elasticsearch、Nginx",
            "存储: PostgreSQL、MySQL、TiDB、ClickHouse、InfluxDB",
            "云原生: Docker、Kubernetes、Helm、Terraform、AWS/GCP",
            "工程化: Git、CI/CD(Jenkins/GitLab)、Prometheus、Grafana、链路追踪",
        ],
        "experiences": [
            {"company": "蚂蚁金服", "role": "技术专家", "period": "2018 - 至今",
             "bullets": [
                 "主导交易中台重构，支撑日均亿元级交易量，接口 P99 延迟下降 40%。",
                 "设计统一消息总线，接入 60+ 业务系统，峰值吞吐 120 万 TPS。",
                 "搭建全链路压测与可观测体系，故障定位时间从小时级降至分钟级。",
                 "推动团队从单体向微服务演进，服务拆分 30 个模块，容器化覆盖 100%。",
             ]},
            {"company": "字节跳动", "role": "高级后端工程师", "period": "2015 - 2018",
             "bullets": [
                 "负责广告计费系统，实现毫秒级实时扣费与对账，资损趋近于零。",
                 "开发分布式限流与熔断组件，保障大促期间系统稳定。",
                 "参与数据中台建设，支撑报表与实时大屏。",
             ]},
        ],
        "projects": [
            {"title": "高并发秒杀系统", "tech": "Python · FastAPI · Redis · Kafka",
             "details": ["支撑 10 万 QPS 的秒杀场景，通过分层限流与削峰填谷保障稳定。", "核心链路压测无宕机，订单一致性 100%。"]},
            {"title": "实时风控平台", "tech": "ClickHouse · Flink · Python",
             "details": ["实现毫秒级规则引擎，日均决策 2 亿次，拦截欺诈交易过亿元。"]},
        ],
        "education": [
            "某大学 计算机科学与技术 本科（2011 - 2015），GPA 3.8/4.0",
        ],
        "certificates": [
            "ACL 深度学习方向一等奖（团队）", "CKA 认证 Kubernetes 管理员", "英语：CET-6",
        ],
    },
    {
        "name": "陈芳", "title": "高级 Java 工程师", "years": 6,
        "contact": "电话: 139-0000-0002 ｜ 邮箱: chenfang@example.com ｜ 期望城市: 北京",
        "overview": [
            "6 年 Java 服务端开发经验，专注于微服务架构、分布式系统与高并发场景。",
            "具备较强的系统设计能力与代码洁癖，主导多家公司核心系统上线。",
            "善于跨团队协作，能独立承担复杂模块的技术方案落地。",
        ],
        "skills": [
            "语言: Java(精通)、Spring Cloud Alibaba、MyBatis、Netty",
            "中间件: RocketMQ、Kafka、Redis、Apollo 配置中心、ZooKeeper",
            "存储: MySQL(主从/分库分表)、ES、HBase、MongoDB",
            "框架/工具: Dubbo、Sentinel、Seata、XXL-Job、JVM 调优",
            "其他: Maven、Git、Docker、K8s、Jenkins",
        ],
        "experiences": [
            {"company": "美团", "role": "高级后端工程师", "period": "2017 - 2022",
             "bullets": [
                 "负责外卖调度核心服务，单机 QPS 8 万，通过缓存改造与连接池优化提升 30%。",
                 "主导订单状态机重构，消除分布式事务隐忧，数据一致性显著提升。",
                 "搭建监控告警与容量预估体系，大促节点 0 事故上线。",
             ]},
            {"company": "京东", "role": "Java 工程师", "period": "2015 - 2017",
             "bullets": [
                 "参与商品中心研发，完成多级缓存与异步任务架构升级。",
                 "负责库存超卖防护方案设计，线上超卖率降为 0。",
             ]},
        ],
        "projects": [
            {"title": "外卖实时订单监控系统", "tech": "Spring Boot · Redis · RocketMQ",
             "details": ["实时采集百万级订单事件，异常实时告警并自动补偿。", "故障自愈率提升 70%。"]},
        ],
        "education": ["某大学 软件工程 本科（2011 - 2015），校级优秀毕业生"],
        "certificates": ["Oracle 官方 Java 认证（OCP）", "英语：CET-6"],
    },
    {
        "name": "赵子龙", "title": "机器学习算法工程师", "years": 5,
        "contact": "电话: 137-0000-0003 ｜ 邮箱: zhaozl@example.com ｜ 期望城市: 深圳 · 北京",
        "overview": [
            "5 年机器学习与推荐系统算法经验，曾在头部互联网公司负责亿级用户的个性化推荐。",
            "熟悉主流深度学习框架与大规模分布式训练，具备算法落地到线上一整套能力。",
            "发表多篇顶会论文，热爱技术研究与实践结合。",
        ],
        "skills": [
            "语言/框架: Python、PyTorch、TensorFlow、Pyspark、CUDA",
            "算法: 推荐系统(召回/粗排/精排/重排)、CTR 预估、多模态、图神经网络",
            "特征/数据: 特征工程、Embedding、实时特征平台、A/B 实验体系",
            "工程: MLflow、Kubeflow、Ray、Flink、Spark、MySQL、Redis",
        ],
        "experiences": [
            {"company": "字节跳动", "role": "高级算法工程师", "period": "2019 - 至今",
             "bullets": [
                 "负责短视频推荐精排模型，CTR 提升 15%，人均时长增长 8%。",
                 "落地双塔召回架构，召回覆盖率显著提升并降低算力成本。",
                 "搭建在线学习链路，模型每小时自动更新，效果持续增益。",
             ]},
            {"company": "百度", "role": "算法工程师", "period": "2017 - 2019",
             "bullets": [
                 "负责搜索排序特征优化，长尾查询体验明显改善。",
                 "参与大规模稀疏特征分布式训练平台建设。",
             ]},
        ],
        "projects": [
            {"title": "多模态商品推荐", "tech": "PyTorch · 多模态预训练 · Faiss",
             "details": ["融合图文视频多模态特征，冷启场景转化率提升 22%。"]},
        ],
        "education": ["某大学 计算机科学与技术 硕士（2015 - 2017）", "某大学 数学与应用数学 本科（2011 - 2015）"],
        "certificates": ["KDD / RecSys 会议论文 2 篇", "英语：CET-6"],
    },
    {
        "name": "刘思思", "title": "高级数据分析师", "years": 4,
        "contact": "电话: 136-0000-0004 ｜ 邮箱: liusisi@example.com ｜ 期望城市: 广州 · 深圳",
        "overview": [
            "4 年互联网数据分析经验，擅长业务指标体系搭建、埋点设计与 A/B 实验分析。",
            "精通 SQL 与 Python，能独立从海量数据中挖掘洞察并驱动业务决策。",
            "沟通能力强，多次主导跨部门数据专项支持。",
        ],
        "skills": [
            "分析工具: SQL、Python(pandas/numpy)、Tableau、PowerBI、Looker",
            "数据工程: 数据仓库建模、ETL、Hive、Spark、Flink",
            "分析方法: A/B 实验、漏斗分析、留存分析、用户分层、归因模型",
            "其他: 指标体系建设、数据可视化、报告撰写",
        ],
        "experiences": [
            {"company": "携程", "role": "数据分析师", "period": "2020 - 至今",
             "bullets": [
                 "搭建住宿业务核心指标看板，覆盖 200+ 业务指标，支撑跨部门决策。",
                 "主导新客转化 A/B 实验 40+ 场，累计提升转化率 12%。",
                 "撰写季度经营分析报告，为公司资源分配提供依据。",
             ]},
            {"company": "唯品会", "role": "数据分析专员", "period": "2018 - 2020",
             "bullets": ["建立用户流失预警模型，流失召回率提升 20%。"]},
        ],
        "projects": [
            {"title": "增长实验平台", "tech": "SQL · Python · Tableau",
             "details": ["统一实验指标口径，实验周期从 2 周缩短至 3 天。"]},
        ],
        "education": ["某财经大学 统计学 本科（2014 - 2018）"],
        "certificates": ["Tableau Desktop Specialist", "英语：CET-6"],
    },
    {
        "name": "李强", "title": "前端架构师", "years": 9,
        "contact": "电话: 135-0000-0005 ｜ 邮箱: liqiang@example.com ｜ 期望城市: 杭州",
        "overview": [
            "9 年前端研发与管理经验，专注中大型前端工程化、组件库与性能优化。",
            "主导过集团级前端基建，服务数百条业务线，积累了丰富的高复杂度前端架构经验。",
            "熟悉微前端、可视化、低代码等方向，执行力与团队影响力兼备。",
        ],
        "skills": [
            "框架: React、Vue3、Next.js、TypeScript、Node.js",
            "工程化: Webpack、Vite、Monorepo、PNPM、CI/CD、灰度发布",
            "架构: 微前端(qiankun)、组件库、低代码平台、SSR、边缘渲染",
            "可视化: ECharts、Three.js、WebGL、AntV",
            "性能: 首屏优化、资源加载、Web Vitals、兼容性治理",
        ],
        "experiences": [
            {"company": "阿里巴巴", "role": "前端专家/架构师", "period": "2015 - 至今",
             "bullets": [
                 "主导中后台体系组件库与设计规范，服务 800+ 业务线，减少重复开发 50%。",
                 "设计并落地微前端方案，多团队独立发布 0 冲突。",
                 "推动全集团性能治理，核心页面 LCP 中位数下降 35%。",
             ]},
            {"company": "网易", "role": "前端工程师", "period": "2012 - 2015",
             "bullets": ["负责电商活动页引擎研发，支撑万级营销活动快速上线。"]},
        ],
        "projects": [
            {"title": "可视化搭建平台", "tech": "Vue3 · Vite · WebGL",
             "details": ["搭建低代码可视化平台，运营自助搭建页面上千个。"]},
        ],
        "education": ["某大学 计算机科学与技术 本科（2008 - 2012）"],
        "certificates": ["英语：CET-6", "前端技术大会分享嘉宾"],
    },
    {
        "name": "孙悦", "title": "DevOps / SRE 工程师", "years": 5,
        "contact": "电话: 134-0000-0006 ｜ 邮箱: sunyue@example.com ｜ 期望城市: 上海",
        "overview": [
            "5 年云原生与运维研发经验，专注 CI/CD、容器化平台建设与 SRE 稳定性保障。",
            "具备大型集群管理与资源成本优化能力，主导过多套发布与监控系统落地。",
            "自动化与可观测性导向，信奉代码化基础设施。",
        ],
        "skills": [
            "容器/K8s: Docker、Kubernetes、Helm、K3s、OpenKruise",
            "CI/CD: Jenkins、GitLab CI、Argo CD、Tekton、GitOps",
            "IaC: Terraform、Ansible、Pulumi、CloudFormation",
            "可观测: Prometheus、Grafana、Loki、Jaeger、Elastic Stack、OpenTelemetry",
            "云平台: 阿里云、腾讯云、AWS、GCP、多云架构",
        ],
        "experiences": [
            {"company": "腾讯云", "role": "运维开发工程师", "period": "2019 - 至今",
             "bullets": [
                 "搭建多集群发布平台，结合 GitOps 使部署耗时缩短 70%。",
                 "负责数十个 Kubernetes 集群稳定性，年均可用性 99.99%。",
                 "制定资源成本优化策略，年节省云资源费用约 30%。",
             ]},
            {"company": "网易", "role": "运维工程师", "period": "2017 - 2019",
             "bullets": ["建设监控告警体系，覆盖 2000+ 主机与应用，告警噪声降低 60%。"]},
        ],
        "projects": [
            {"title": "企业级发布平台", "tech": "K8s · Argo CD · Terraform",
             "details": ["实现一键灰度/回滚，支持多环境一致部署，发布成功率提升至 99%。"]},
        ],
        "education": ["某大学 网络工程 本科（2013 - 2017）"],
        "certificates": ["CKA 认证 Kubernetes 管理员", "AWS Solutions Architect", "英语：CET-6"],
    },
    {
        "name": "周雨桐", "title": "高级产品经理", "years": 6,
        "contact": "电话: 133-0000-0007 ｜ 邮箱: zhouyt@example.com ｜ 期望城市: 北京 · 上海",
        "overview": [
            "6 年 B 端/出行产品经验，擅长需求洞察、产品规划与商业化落地。",
            "从 0 到 1 主导多款产品，具备较强的数据驱动与跨团队资源协调能力。",
            "重视用户体验与商业价值的平衡，亲历业务规模化增长。",
        ],
        "skills": [
            "产品方法: 需求分析、竞品调研、PRD、MVP、路线图规划",
            "工具: Axure、Figma、Xmind、SQL(基础)、数据分析平台",
            "领域: B 端 SaaS、运力管理、增长、商业化、开放平台",
        ],
        "experiences": [
            {"company": "滴滴", "role": "高级产品经理", "period": "2018 - 至今",
             "bullets": [
                 "负责企业出行产品线，GMV 年增长 50%，覆盖企业客户 2 万家。",
                 "打造开放平台，接入 100+ 生态伙伴，扩展服务边界。",
                 "建立商业化定价体系，收入结构从单一增长到多元化。",
             ]},
            {"company": "美团", "role": "产品经理", "period": "2016 - 2018",
             "bullets": ["负责到店营销工具，提升商户投放 ROI，覆盖商户 10 万+。"]},
        ],
        "projects": [
            {"title": "企业差旅管理平台", "tech": "数据驱动 · SaaS",
             "details": ["从 0 构建差旅预订与服务闭环，服务 2 万+ 企业客户。"]},
        ],
        "education": ["某大学 工商管理 本科（2012 - 2016）"],
        "certificates": ["NPDP 产品经理国际认证", "英语：CET-6"],
    },
    {
        "name": "吴朗", "title": "高级安全工程师", "years": 7,
        "contact": "电话: 132-0000-0008 ｜ 邮箱: wulang@example.com ｜ 期望城市: 北京",
        "overview": [
            "7 年网络安全攻防经验，专注红蓝对抗、漏洞挖掘与安全体系建设。",
            "多次在国家级漏洞平台提交高危漏洞，具备安全合规与 DevSecOps 实践能力。",
            "可为公司搭建从研发到上线的全链路安全防线。",
        ],
        "skills": [
            "攻防: 渗透测试、Web 安全、内网渗透、红队评估、代码审计",
            "平台/工具: BurpSuite、Nessus、Metasploit、AWVS、IDA、Ghidra",
            "安全体系建设: DevSecOps、SDL、等保 2.0、ISO 27001、威胁建模",
            "开发: Python、Go(安全工具开发)、Linux",
        ],
        "experiences": [
            {"company": "奇安信", "role": "高级安全研究员", "period": "2017 - 至今",
             "bullets": [
                 "连续两年向 SRC 提交高危漏洞，多次获年度最佳研究员。",
                 "搭建企业安全运营中心(SOC)，告警处置效率提升 60%。",
                 "主导红蓝对抗演练，推动 20 项重大安全隐患整改。",
             ]},
            {"company": "绿盟科技", "role": "安全工程师", "period": "2015 - 2017",
             "bullets": ["负责攻防平台与应急响应，参与多次国家级重保任务。"]},
        ],
        "projects": [
            {"title": "自动化漏洞扫描平台", "tech": "Python · Go · Elastic Stack",
             "details": ["联动 CI/CD 实现代码与依赖漏洞实时检测，上线前拦截率 95%。"]},
        ],
        "education": ["某大学 信息安全 硕士（2013 - 2015）", "某大学 网络工程 本科（2009 - 2013）"],
        "certificates": ["CISP 注册信息安全专业人员", "OSCP 国际渗透测试认证", "英语：CET-6"],
    },
    {
        "name": "郑晓彤", "title": "测试开发工程师", "years": 4,
        "contact": "电话: 131-0000-0009 ｜ 邮箱: zhengxt@example.com ｜ 期望城市: 武汉 · 长沙",
        "overview": [
            "4 年后端测试与测试开发经验，专注自动化测试框架搭建与质量体系建设。",
            "能编写高效的自动化用例与工具，推动团队从手工测试向自动化转型。",
            "具备接口、性能、前端 E2E 等多层测试能力。",
        ],
        "skills": [
            "自动化: Pytest、Selenium、Playwright、Appium、Allure、Requests",
            "接口/性能: Postman、JMeter、Locust、gRPC 测试",
            "CI/CD: Jenkins、GitLab CI、Docker、SonarQube、覆盖率治理",
            "其他: 测试平台建设、埋点验证、兼容性测试",
        ],
        "experiences": [
            {"company": "百度", "role": "测试开发工程师", "period": "2020 - 至今",
             "bullets": [
                 "搭建全链路自动化回归体系，用例数量 3000+，回归耗时从 2 天缩短至 2 小时。",
                 "建设质量平台，联动缺陷跟踪与发布门禁，线上故障下降 40%。",
                 "主导性能压测专项，识别并推动优化多个瓶颈接口。",
             ]},
        ],
        "projects": [
            {"title": "接口自动化平台", "tech": "Python · Pytest · Jenkins",
             "details": ["支持可视化用例编排与定时执行，覆盖核心链路 80%。"]},
        ],
        "education": ["某大学 软件工程 本科（2016 - 2020）"],
        "certificates": ["ISTQB 基础级认证", "英语：CET-4"],
    },
    {
        "name": "韩磊", "title": "Go 后端工程师", "years": 3,
        "contact": "电话: 130-0000-0010 ｜ 邮箱: hanlei@example.com ｜ 期望城市: 上海 · 杭州",
        "overview": [
            "3 年 Go 后端开发经验，专注高并发实时系统与基础组件开发。",
            "习惯用简洁可靠的代码解决问题，具备良好的工程与协作习惯。",
            "对数据一致性与容灾方案有较好的实践认知。",
        ],
        "skills": [
            "语言/框架: Go、Gin、gRPC、Micro、RESTful",
            "存储/中间件: MySQL、Redis、Kafka、etcd、NSQ",
            "工程: 微服务、服务编排、Linux 运维、Docker、Prometheus",
            "其他: Redis 分布式锁、限流、幂等、熔断降级",
        ],
        "experiences": [
            {"company": "哔哩哔哩", "role": "后端开发工程师", "period": "2021 - 至今",
             "bullets": [
                 "参与弹幕实时系统研发，支撑百万级实时在线连接。",
                 "负责网关与连接管理组件，优化内存与心跳机制，资源占用下降 40%。",
                 "接入可观测体系，故障定位从分钟级缩短至秒级。",
             ]},
        ],
        "projects": [
            {"title": "实时消息推送网关", "tech": "Go · gRPC · Redis · Kafka",
             "details": ["自研高可用网关，单节点支撑 10 万连接，支持水平扩容。"]},
        ],
        "education": ["某大学 计算机科学与技术 本科（2017 - 2021）"],
        "certificates": ["Go 开源项目 Contributor", "英语：CET-4"],
    },
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for c in CANDIDATES:
        canv = Canvas()
        render(c, canv)
        first, rest = canv.pages[0], canv.pages[1:]
        path = OUT_DIR / f"OCR_{c['name']}.pdf"
        first.save(path, "PDF", resolution=150.0, save_all=True, append_images=rest)
        print(f"generated {path}  ({len(canv.pages)} 页)")
    print("done:", len(CANDIDATES), "PDFs")


if __name__ == "__main__":
    main()