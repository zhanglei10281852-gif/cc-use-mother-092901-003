# 自动驾驶场景核证库

面向自动驾驶安全核证的场景后端：把道路环境、交通参与者、能见度与路面附着、故障注入、期望行为组织成**可复用但有版本**的场景定义，并记录测试运行的软件版本、输入摘要、结果证据与评审结论，支持能力到证据的双向追溯。

## 架构

```
src/scenario_certification/
├── contracts.py   # 既有最小契约（保留兼容）
├── model.py       # 五要素场景内容、不可变场景版本、运行记录、能力声明、评审结论、发布批次
├── store.py       # 仅追加存储 + 全局逻辑序号 + 事件日志（不依赖墙钟，保证确定性）
├── services.py    # 场景注册/运行收录/声明与覆盖/评审/影响分析/双向追溯
└── backend.py     # CertificationBackend 门面与全量快照
```

## 核心规则

- **版本分叉**：场景版本不可变，`publish_revision` 基于任一已存在版本发布后继；同一父版本可分出多支，`lineage`/`heads` 推导确定。
- **变更影响**：发布新版本时，`backend.publish_revision(...)` 返回受影响的已签发能力声明（引用了被超越祖先版本的声明）。
- **运行收录**：`run_id` 按内容寻址——重复上传同一运行幂等去重、不增加覆盖率；重跑（输入相同、结果或证据不同）生成新记录并递增 `attempt`，失败重跑不会覆盖原始结果。
- **评审**：`freeze_batch` 冻结发布批次成员；`return_scenarios` 退回声明中的局部场景（声明转为待补证，补跑通过后重新评审签发）；`revoke_conclusion` 撤销结论并确定性地传播到以它为签发依据的声明。
- **双向追溯**：`trace.capability_trace`（能力 → 声明 → 场景版本 → 运行 → 证据）与 `trace.run_trace` / `scenario_trace` / `evidence_trace`（反向）。

## 快速开始

```python
from scenario_certification import CertificationBackend, ScenarioContent, ...

backend = CertificationBackend()
backend.scenarios.register_scenario("SCN-AEB-001", content, author="scenario-team")
ingestion = backend.runs.record_run("SCN-AEB-001", 1, "sw-2026.10", "input-digest", RunOutcome.PASSED, evidence=(...))
backend.claims.register_capability("CAP-AEB", "行人横穿自动紧急制动", ("SCN-AEB-001",))
claim = backend.claims.draft_claim("CAP-AEB", (ingestion.run.run_id,))
backend.review.submit_conclusion(claim.claim_id, "approve", "reviewer-li", "证据充分")
impact = backend.publish_revision("SCN-AEB-001", 1, new_content, "scenario-team", "收紧阈值")
print(impact.impacted_claim_ids)   # 受影响的已签发声明
```

## 常用命令

运行测试：`python -m unittest discover -s tests -v`

编译检查：`python -m compileall -q src tests run_cli.py`

命令行冒烟：`python run_cli.py`
