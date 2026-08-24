"""vllm-xcheck 客户端负载引擎（组 C）。

C1  canonical request case 清单生成（src/client/cases.py）
C2  Poisson(seed=0) 到达序列生成器 vllm-xcheck-poisson-v1（src/client/arrival.py）
C3  请求执行器 + 逐请求回执（src/client/executor.py）
C4  SLO 判定模型（src/client/slo.py）
C5  A4 租户调度器：混合到达 + 双重并发 + B0 扫描/正式负载 + 公平性（src/client/scheduler.py，
    配合 src/client/fairness.py 的份额/Jain 判定）
C6  A3 窗口分析器：closed-loop 串行执行 + 窗口切分 + 窗口指标判定（src/client/window.py，
    CV≤5% / TTFT/TPOT 漂移 / 0 失败 / token 门禁）
C7  A1 闭环算力测量：按轮 batch 并发执行固定形状（src/client/closedloop.py）+ 逐算子
    effective FLOPs / MFU（src/client/mfu.py，A1-2 v1，分母 ascend-dmi 真机项）

入口：
  python src/client/generate.py     # C1+C2 合同生成 / 自测
  python src/client/run.py          # C3+C4+C5+C6+C7 执行 + SLO/窗口/MFU 判定 / 离线自测
"""
