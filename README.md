# 自动驾驶场景核证库

本项目保存自动驾驶测试场景与运行证据的领域契约。场景版本和测试运行相互独立，便于后续构建覆盖计算、评审签发以及变更影响追踪。

运行测试：\`python -m unittest discover -s tests -v\`

编译检查：\`python -m compileall -q src tests run_cli.py\`

命令行冒烟：\`python run_cli.py\`
