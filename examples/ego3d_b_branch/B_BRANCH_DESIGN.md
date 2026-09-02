# B 支线：从“头部在光心”到可辨识、可证伪的 Root/Wrist 修正

版本：`B-20260902-v1`  
输入级别：Level 1（完整方案）  
代码基线：`mythrise/ego3d_wm_global` main `cca50bc9683962232db95fd4b7624967b1007a6c`（只读快照）

## 0. 结论先行

B 支线不直接再堆一个更大的 `t` 网络。现有数据已经给出两个强信号：

1. C6/C7 的 root-relative 和 PA 几乎不变，而 world 与 Root ATE 有差异，主要矛盾在相机到人体根节点的全局变换；
2. G11–G13 的手部增强没有稳定收益，说明在修手指以前，腕部的“可观测性、body wrist/hand wrist 接缝和全局 root 误差”没有被隔离。

因此顺序固定为：`复现与折差审计 → 刚性头戴外参 → t-only → R-only → joint SE(3) → wrist seam → fingers`。每一步只在前一步通过门禁后启动。

## 1. 已知输入与不允许偷换的边界

用户提供的最新汇总：

| 指标 | C6 MediaPipe | C7 RTMW |
|---|---:|---:|
| Full59 World MPJPE | 292.25 ± 44.00 mm | 275.57 ± 5.86 mm |
| Full59 Root-relative MPJPE | 136.93 ± 3.71 mm | 137.60 ± 3.28 mm |
| Full59 PA-MPJPE | 76.15 ± 1.22 mm | 76.29 ± 1.16 mm |
| Root ATE | 0.1161 ± 0.0093 m | 0.1126 ± 0.0014 m |
| Root SO(3) | 55.06 ± 5.90° | 51.31 ± 2.08° |

冻结约束：prefix-8 输入；第 9 帧/未来帧不可读；take-level grouped OOF；官方 val GT 只能在预测封存后进入评测；C7 的 detector、checkpoint、预处理和左右手映射必须写入 manifest。B 分支不得把 G9/G14/G15 所需的外部 SLAM/teacher 伪装成已存在输入。

代码审计确认，当前 G4 的解析式已经实现

\[
\mathbf t_{C\leftarrow R}=\mathbf p_h^C-
\mathbf R_{C\leftarrow R}(s\mathbf p_h^R),
\]

并要求 calibration identity；G5 是重力对齐 4DoF，G6 是右乘 6DoF residual；G11 会变换 hand tree，但 body wrist 与 hand wrist 是不同节点。B 分支是在这些合同上补“可辨识的头戴外参”和“共享腕部接缝”，不是重新命名 G4/G6/G11。

## 2. 为什么把头部位置设为相机光心不成立

采用仓库约定：`T_A_B` 把 B 坐标映射到 A。任一 root-local 关节满足

\[
\mathbf p_j^W=
\mathbf T_{W\leftarrow C}
\mathbf T_{C\leftarrow R}
\bar{\mathbf p}_j^R.
\]

若强设 \(\mathbf p_h^C=\mathbf 0\)，等价于声称相机光学中心与定义人体 head joint 的三维点完全重合。真实头戴设备存在固定杆臂、相机俯仰/滚转、佩戴偏移；而 skeleton 的“头部”又可能由 ears/eyes/nose 构造，不是光心。这个误设会把固定外参误差塞给每帧 root translation 网络，并且旋转误差通过杆臂继续放大为平移误差。

定义 skeleton 构造的头坐标系 \(H\)，则正确分解为

\[
\mathbf T_{C\leftarrow R}^{(0)}=
(\mathbf T_{H\leftarrow C}^{\mathrm{rig}})^{-1}
(\mathbf T_{R\leftarrow H}^{\mathrm{pose}})^{-1}.
\]

其中相邻矩阵表示严格按从右到左的矩阵乘法；等价地写为

\[
\boxed{\mathbf T_{C\leftarrow R}^{(0)}=
(\mathbf T_{H\leftarrow C}^{\mathrm{rig}})^{-1}
\mathbf T_{H\leftarrow R}^{\mathrm{pose}}}.
\]

训练帧可从 GT 得到逐帧候选：

\[
\mathbf T_{H\leftarrow C}^{(i)}=
(\mathbf T_{R\leftarrow H}^{(i)})^{-1}
(\mathbf T_{C\leftarrow R}^{(i)})^{-1}.
\]

平移取逐维中位数，旋转取 SO(3) geodesic medoid；每个 target fold 只用其余四折拟合。不得在 official val 上按 take 再拟合外参，除非产品部署时确实提供独立标定序列，且该协议在跑实验前冻结。

## 3. B 分支矩阵

### B-DIAG：先解释 fold 4，不训练

对每个 `(branch, seed, fold, take)` 输出 window-micro 和 take-macro 两套指标，至少关联：wearer、take、动作类别、有效关节比例、2D detector 置信度、相机角速度/线速度、根深度、head-rig MAD、遮挡率。计算：

- fold 与总体的 standardized mean difference；
- 连续协变量 KS 距离；
- 每 take 误差分位数和 paired delta；
- 同一 predictions 在 micro/macro 聚合下的排名变化；
- manifest UID、preprocessing digest、checkpoint digest、ensemble 成员完全性。

若 fold 4 存在 take/窗口重叠、fold 专属预处理、GT 提前访问或只展示“最好 take”，立即 `INVALID_EVIDENCE`。若无泄漏但协变量偏移显著，就把 fold 4 解释为数据组成效应，而不是泛化能力证明。

### B0：冻结 C7-G5

不训练；从不可变 C7 checkpoint 和输入缓存重放。验收要求每 fold 指标、聚合权重、视频帧 UID 与原报告一致。任何无法重现的 headline 都阻止 B1。

### B1：train-only robust head-rig SE(3)

为每个 OOF target fold 生成一个 `HeadRigFit`：translation median、SO(3) medoid、sample count、MAD、train-take list、输入 digest。推理时先由当前 root-local pose 构建 \(T_{R\leftarrow H}\)，再用上节闭式得到 \(T_{C\leftarrow R}^{(0)}\)。

对照组：零光心假设、全局常量外参、每 wearer 外参、随机打乱 wearer 外参。只有-beat-shuffle 是硬门，避免网络只利用 wearer ID。

### B2：有界 t-only residual

冻结 B1 的旋转与所有 local joints。网络只从 prefix-8 的 head motion、C7 detector、MoViNet、Compact-C validity 和 B1 uncertainty 预测重力坐标系增量：

\[
\Delta\mathbf t_G=
\mathbf b_t\odot\tanh f_t(\mathbf x_{1:8}),\qquad
\mathbf b_t=(0.15,0.15,0.30)\text{ m},
\]

\[
\mathbf t_{C\leftarrow R}=
\mathbf t^{(0)}_{C\leftarrow R}+
\mathbf R_{C\leftarrow G}\Delta\mathbf t_G.
\]

输出异方差 \(\log\sigma_t^2\)，loss 为 Huber-NLL、速度和加速度项：

\[
\mathcal L_t=\sum_k e^{-s_k}\rho_\delta(e_k)+\tfrac12s_k
+0.2\rho(\Delta e)+0.05\rho(\Delta^2 e).
\]

residual head 零初始化，step 0 必须逐 bit/容差等于 B1。任何预测触碰 bound 的比例超过 1% 视为结构失配，而不是继续放宽到任意深度。

### B3：有界 R-only residual

冻结 B2 translation，仅预测重力基的轴角：

\[
\Delta\boldsymbol\omega_G=
(8^\circ,8^\circ,15^\circ)\odot\tanh f_R(\mathbf x),
\]

\[
\mathbf R_{C\leftarrow R}=
\mathbf R_{C\leftarrow G}
\exp([\Delta\boldsymbol\omega_G]_\times)
\mathbf R_{G\leftarrow C}
\mathbf R^{(0)}_{C\leftarrow R}.
\]

主 loss 是 SO(3) geodesic；translation target 不进入 B3。必须报告角误差按 yaw/pitch/roll、相机角速度和 take 分层的结果。

### B4：joint SE(3) 只测试交互项

B2/B3 单独通过后才联合 fine-tune，学习率为单分支的 0.25，且保留原 bounds。评价交互：

\[
\Delta_{int}=M(B4)-\min(M(B2),M(B3)).
\]

若 B4 没有显著胜过最佳单分支，则保留更简单的 B2 或 B3，不因“端到端”叙事而 KEEP。

### B5/B6/B7：腕部先可观测，再共享移动

EgoJoint-59 中 body wrists `(9,10)` 与 hand-tree wrists `(17,38)` 是不同的节点。B5 先统计每帧 detector confidence、visibility、body/hand wrist seam、腕到肘骨长及误差；不可见帧不允许伪监督强修。

B6 的 residual 为

\[
\Delta\mathbf w=
q_{det}^2\,\mathbb 1_{visible}\,
0.12\tanh f_w(\mathbf x),
\]

同一个 \(\Delta\mathbf w\) 同时移动 body wrist 和对应完整 hand tree，从而保留手内骨形；目标包含四个 wrist 节点、seam 和 temporal loss。B7 只有在 B6 同时通过 world-wrist 与 root-relative 门后才运行，且只编辑 wrist-local fingers。

## 4. 预注册门禁

所有 paired delta 以 take 为重采样单位做 10,000 次 bootstrap；seed 仅作为重复测量，不把 window 当独立样本。门槛在看 B 结果前冻结：

| Gate | KEEP 条件 |
|---|---|
| B-DIAG | 无 UID 泄漏/预处理漂移；解释 micro/macro 和 fold 组成 |
| B1 | Root ATE 相对 B0 降 ≥3%，95% CI 不跨 0；SO(3) 退化 ≤1° |
| B2 | Root ATE 再降 ≥5%，World MPJPE 降 ≥3%；take win-rate ≥60%；最差 fold 退化 ≤2% |
| B3 | Root SO(3) 降 ≥5%；Root ATE 退化 ≤2 mm；local 指标差异在数值噪声内 |
| B4 | 胜过最佳单支，且额外参数 ≤3M、batch-1 p95 ≤10 ms |
| B6 | 左右 wrist world MPJPE 各降 ≥5%；Full59 root-relative 不退化 >1%；seam 不增大 |
| B7 | hand wrist-relative/PA 均改善；B6 wrist gate 仍成立 |

任一平均改进若只由 fold 4 驱动、shuffled observation 与真实 observation 等价、或 unseen-wearer 失败，结论降级为 `REJECT/INCONCLUSIVE`。

## 5. 测试清单

### 单元/合同

1. `T_A_B` roundtrip、compose order、m/mm、四元数顺序和 timestamp 对抗测试；
2. 用合成 rig 生成 GT，B1 能恢复已知 \(T_{H\leftarrow C}\)；交换 compose side 必须失败；
3. B2/B3/B6 step-0 identity；translation/rotation/wrist bounds 永不越界；
4. mask 后关节严格为 0；body/hand wrist 索引固定；不可见手 gate=0；
5. 每 target fold 的 calibration/training take 与 target take 交集为空；
6. loader 试图访问第 9 帧、future teacher 或 official-val GT 时 fail closed；
7. checkpoint、config、fold manifest、provider 和 source revision 全部 SHA 绑定。

### 小样本/训练

1. 单 take 过拟合只用于证明梯度与坐标方向；不得作为精度结论；
2. folds 0/4 × one seed pilot，验证恢复、checkpoint 和 artifact schema；
3. 全 5 folds × 3 seeds；每 cell 写入独占 `cell_id/row_shard`，16 个 row shard，稳定排序合并；
4. 原始预测先原子封存，评测作为只读独立任务；
5. 复算两次 matrix/receipt/metric digest 必须相同。

### 负对照

`identity`、`constant median`、`C6 detector`、`C7 detector`、`shuffled observations`、`shuffled wearer rig`、`take-only bias`、`unseen wearer` 都是必跑 cell，不能在结果不好后删除。

## 6. 资源执行图

fold-invariant 的 RGB/pose/detector 特征只物化一次并按 content digest 只读共享。实验 cell 是调度单位，cell 内按 row 分片；worker 只写 `cell_id/row_shard`，一个 merger 按 row key 稳定归并并原子发布。每 20 分钟 checkpoint，可从最后完整 shard 恢复。B1/B-DIAG 是 CPU 队列；B2–B7 是单 GPU cell，可并发占用明确空闲 GPU。依赖边只作用于对应 branch，不设置“所有 fold 完成才能开始所有后继”的全局 barrier。

独立 `resource-reviewer/external-v1` 位于人类审批之外。即使 `human_approved=true`，只要检测到 fold-invariant 重算、1 CPU 长 cell、无 row shard、无 checkpoint、输出碰撞、全局 barrier、计算与评测耦合或串行低利用率，就返回 `VETO/BLOCK_EXECUTION`；只能改计划，不能用人类同意覆盖。

## 7. 论文依据与使用边界

- RootNet 将绝对根深度与 root-relative pose 解耦，支持“先把 root translation 当独立估计问题”，但它不证明当前头戴外参应由网络任意学习：<https://openaccess.thecvf.com/content_ICCV_2019/html/Moon_Camera_Distance-Aware_Top-Down_Approach_for_3D_Multi-Person_Pose_Estimation_From_ICCV_2019_paper.html>
- WHAM 把相机角速度、人运动和 contact-aware trajectory 分开融合，支持 B 支线的 root/local 分解与独立轨迹门；本项目没有使用外部 SLAM，因此不能照搬其 world-grounded claim：<https://openaccess.thecvf.com/content/CVPR2024/html/Shin_WHAM_Reconstructing_World-grounded_Humans_with_Accurate_3D_Motion_CVPR_2024_paper.html>
- Synergistic Global-space Camera and Human Reconstruction 强调相机与人体共同约束，说明只改人体 root 而不审计相机链可能混淆误差来源：<https://openaccess.thecvf.com/content/CVPR2024/html/Zhao_Synergistic_Global-space_Camera_and_Human_Reconstruction_from_Videos_CVPR_2024_paper.html>
- Egocentric Pose Estimation From Human Vision Span 同时使用 SLAM 动态和身体视觉并显式约束几何一致性，支持 B1/B2 使用头动、视觉和几何但保持各自消融：<https://openaccess.thecvf.com/content/ICCV2021/html/Jiang_Egocentric_Pose_Estimation_From_Human_Vision_Span_ICCV_2021_paper.html>
- Hierarchical Temporal Transformer 说明 egocentric hand 的遮挡/歧义需要时间信息；B6 因此使用短时上下文和置信门，而不是在不可见帧强行修腕：<https://openaccess.thecvf.com/content/CVPR2023/html/Wen_Hierarchical_Temporal_Transformer_for_3D_Hand_Pose_Estimation_and_Action_CVPR_2023_paper.html>

这些论文是设计依据，不是本仓库的实验结果。B 支线的有效性只能由上述冻结矩阵和证据门决定。
