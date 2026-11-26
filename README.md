# 实验指标分析与归因评估平台

支撑AB实验分组、指标口径计算、显著性检验、归因分析与报告分发的实验评估平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8022/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5202/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8022`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 事件接入 | `event_stream` | 事件定义 | 事件编码、事件名称、属性字段 |
| 实验设计 | `exp_design` | 实验方案 | 实验编码、实验名称、分组策略 |
| 分组分配器 | `variant_assign` | 分组结果 | 分配批次、用户标识、实验编码 |
| 指标口径中心 | `metric_catalog` | 指标定义 | 指标编码、指标名称、计算口径 |
| 指标计算作业 | `metric_compute` | 计算作业 | 作业名称、指标编码、计算窗口 |
| 显著性检验 | `significance_test` | 检验结果 | 检验批次、实验编码、指标编码 |
| 归因分析器 | `attr_analysis` | 归因结果 | 归因批次、实验编码、贡献因子 |
| 人群圈选器 | `cohort_builder` | 人群包 | 人群编码、人群名称、圈选条件 |
| 报告生成器 | `report_gen` | 分析报告 | 报告编码、实验编码、报告模板 |
| 报告分发 | `report_dist` | 分发记录 | 分发批次、报告编码、接收方 |
| 窗口调度器 | `window_scheduler` | 调度计划 | 计划名称、指标编码、窗口粒度 |
| 数据时效监控 | `data_freshness` | 时效记录 | 监控对象、数据域、最新水位 |
| 指标异常检测 | `anomaly_metric` | 异常事件 | 异常编号、指标编码、基线区间 |
| 实验归档库 | `exp_archive` | 归档实验 | 实验编码、结束时间、结论摘要 |
| 样本量核对 | `sample_ratio` | 核对结果 | 核对批次、实验编码、计划比例 |
| AA预检器 | `aa_check` | AA检验 | 检验编码、实验编码、检验窗口 |
| 切片分析器 | `segment_slice` | 切片结果 | 切片批次、实验编码、切片维度 |
| 曝光日志接入 | `exposure_log` | 曝光记录 | 日志批次、实验编码、用户标识 |
| 结论评审队列 | `conclusion_review` | 评审记录 | 记录编号、实验编码、评审结论 |
| 指标查询服务 | `metric_api` | 查询接口 | 接口编码、指标编码、服务对象 |

## 曝光补传、回执与窗口修订

曝光日志接入页在原列表/导出之外提供“CSV 补传、预览与导入回执”面板：

1. 选择 CSV 后立即按文件行预览，服务端 `/api/exposure_log/imports/preview` 给出权威校验。
2. 提交到 `/api/exposure_log/imports`，返回带 `import_no` 的逐行回执；数据行号从 CSV 第 2 行开始，拒收行保留 `file_row` 与中文 `reason`。
3. 可在页面用导入编号重开回执，或调用 `GET /api/exposure_log/imports/{import_no}`；`/download` 下载含拒收原因、原回执行关联、影响作业和重算状态的 CSV。
4. 回执中的受影响作业会跳到 `/metric_compute?jobs=...&receipt=...`。作业面板展示当前分母引用的导入回执、有效 `file_row/source_id`，以及可追踪的 `metric_result_revisions` 修订链。

CSV 支持中文或常见英文字段名：`来源编号/source_id`、`实验编码/experiment_code`、
`用户标识/user_id`、`分组名称/group_name`、`日志批次/batch_id`、`曝光时间/occurred_at`。

核对口径：

- `(来源编号, 实验编码)` 标识同一源记录；相同来源编号在不同实验下分别核计。
- 已接受过的原样内容（包括已被更正取代的历史版本）重传只记为 `duplicate`，不会再次计入曝光，也不会把时间回滚到旧窗口。
- 时间更正会把旧曝光行置为 `valid=false/status=已更正`，并插入新的当前有效行；因此 X7 这类记录最终只属于更正后的一个日期窗口。
- 指标作业通过 `POST /api/metric_compute/{id}/recalculate` 读取当前有效曝光。分母快照保存在 `metric_denominators`，其中 `receipt_refs` 冻结该次计算实际引用的回执和有效行。
- 更正跨越两个窗口时，两个已完成作业都会标记为 `待重算`；重算后旧结果保留为 `superseded` 修订，新修订通过 `revises` 指向旧结果。
- `POST /api/metric_compute/{id}/claim` 与 `/commit` 提供分母领取凭证；领取后若更正修改了分母，旧凭证提交会返回 409，避免旧窗口出账覆盖更正。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
