# vllm-xcheck acceptance config layout
# 把 V4.1 测试方案的表附-1~19 的「软件级参数」泛化为一套可复用、可实例化的配置。
# 分层视图：common（共同）= 表附-2 服务端 + 表附-4 env + 表附-5 采样；
#          precision/ = 精度 overlay（FP16 / W8A8）；cells/<cell>.yaml = 任务专属覆盖。
# 读取/展开/校验由 src/acceptance.py 负责（--dry-run 打印 argv/env/effective）；
# 后续 scripts/acceptance.sh（L5 harness）与 scripts/prepare.sh 消费同一套配置。

# 命名与引用（隐私边界约定）：本目录只放泛化字段与占位符，不出现机器名/仓库 URL；
# 具体模型 revision、CANN、vllm core/plugin SHA、容器 digest 由实例化方写入 config/（.gitignore）。
#
# 结构：
#   schema.yaml        字段契约（人读；acceptance.py 用其中的必填集做校验）
#   common.yaml        表附-2 共同服务端 + 表附-4 env + 表附-5 统一采样（含 L1 software 占位）
#   precision/         fp16.yaml / w8a8.yaml：dtype、quantization、served_name、工件 manifest
#   cells/             a1-fp16, a2-dialogue/tool/reason/struct/long, a3-32k, a4-mt
#
# 每个 cell 都是一份「任务配置」，共同基线 + cell 覆盖 + precision overlay 合并成
# 15 个正式 profile 实例（V4.1 附录）。用 acceptance.py 展开即得实际启动命令与生效值。