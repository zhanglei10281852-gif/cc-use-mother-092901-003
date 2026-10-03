# 自动驾驶场景核证后端

把测试场景组织成**可复用但有版本**的定义，并把每次测试运行的软件版本、输入摘要、
结果证据与评审结论完整记录，使管理层能够确认一项能力究竟在哪个场景版本、
哪个软件版本上被验证。

## 领域模型

- **场景定义**（`ScenarioDefinition`，不可变、按版本组织）由五个方面组成：
  道路环境（`RoadEnvironment`）、交通参与者（`TrafficParticipant`）、
  能见度与路面附着（`AmbientConditions`）、故障注入（`FaultInjection`）、
  期望行为（`ExpectedBehavior`，指标 + 比较符 + 阈值）。
  变更通过 `fork_scenario` 产生新版本，旧版本原样保留；内容摘要
  （`content_digest`）可发现不同团队重复命名的相同场景。
- **测试运行**（`TestRunRecord`）记录软件版本、输入摘要、结果与证据，
  只增不改：重复上传幂等去重，失败重跑以新记录链接（`rerun_of`），
  绝不覆盖原始结果。
- **能力声明**（`CapabilityClaim`）钉住精确的场景版本集合与软件版本；
  签发时每个钉住版本都必须存在被评审接受的通过运行。
- **发布批次**（`ReleaseBatch`）支持冻结、退回局部场景与撤销结论。

## 核心规则

1. **场景变更影响分析**：`fork_scenario` 返回 `ImpactReport`，钉住该场景的
   已签发声明被确定地标记为 `AFFECTED`，必须重新签发才能恢复。
2. **覆盖去重**：覆盖率按钉住版本上的合格运行集合计算，同一运行重复上传
   不会增加覆盖率。
3. **撤销传播**：撤销评审结论后，失去支撑证据的已签发声明被确定地标记为
   `AFFECTED`；撤销声明结论在任何批次状态下都允许（安全动作）。
4. **冻结语义**：批次冻结后不能加入新声明、不能退回场景；撤销与影响传播
   仍然生效。
5. **双向追溯**：`trace_claim` / `trace_scenario` / `trace_run` /
   `trace_evidence` 提供能力 ↔ 场景 ↔ 运行 ↔ 证据的双向查询，
   所有输出按标识排序，结果确定。

## 目录结构

```
src/scenario_certification/
  contracts.py   # 基础契约（运行结果枚举等）
  models.py      # 领域模型：场景五方面、运行、声明、批次、报告
  backend.py     # 核证后端：命令与追溯查询
  errors.py      # 领域错误
tests/           # 版本分叉、运行录入、覆盖去重、撤销传播、批次、追溯、确定性
run_cli.py       # 端到端冒烟演示
```

运行测试：`python -m unittest discover -s tests -v`

编译检查：`python -m compileall -q src tests run_cli.py`

命令行冒烟：`python run_cli.py`
