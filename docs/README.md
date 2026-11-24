# 文档索引

本目录是项目文档的唯一来源；根目录 [README](../README.md) 只保留快速开始与导航。文档按“产品定义 → 功能验收 → 技术设计 → 交付/运维”组织。

## 推荐阅读路径

| 角色 | 顺序 |
| --- | --- |
| 新成员 | [产品总览](product/PRODUCT.md) → [架构](architecture.md) → [功能与 API](features.md) → [运维](operations.md) |
| 产品/设计 | [产品 Brief](product/产品Brief.md) → [PRD](product/PRD.md) → [FRD](product/FRD.md) → [设计系统](design.md) |
| 后端开发 | [架构](architecture.md) → [FRD](product/FRD.md) → [功能与 API](features.md) → [运维](operations.md) |
| 全体成员 | [团队协作与 Git 工作流](collaboration.md) → [文档索引维护约定](#维护约定) |
| 测试/发布 | [FRD 验收与内测门槛](product/FRD.md) → [运维 Runbook](operations.md) → [交付计划](plans/2026-11-17-rongxing-full-stack.md) |

## 文档清单

| 文件 | 说明 | 维护时机 |
| --- | --- | --- |
| [product/PRODUCT.md](product/PRODUCT.md) | 产品定位、目标用户、能力边界、事实源优先级 | 定位或范围变化 |
| [product/PRD.md](product/PRD.md) | 产品需求：范围、旅程、指标、商业模式、风险 | 需求评审通过后 |
| [product/FRD.md](product/FRD.md) | 功能需求、错误码、固定文案、验收标准（冲突时以此为准） | 功能或规则变更时 |
| [product/产品Brief.md](product/产品Brief.md) | 对内沟通用的一页纸产品说明 | 对外叙事变化 |
| [product/项目规划与设计参考.md](product/项目规划与设计参考.md) | Figma 基准、优先级、里程碑与启动清单 | 设计/排期变化 |
| [architecture.md](architecture.md) | 系统架构、后端目录、数据模型、API、部署 | 模块边界或接口变化 |
| [design.md](design.md) | 设计令牌、组件、状态卡与动效规范 | 视觉规范变化 |
| [features.md](features.md) | 当前功能行为、可选服务联调、本地 API 范围 | 功能交付/接口变化 |
| [operations.md](operations.md) | 环境变量、日志与脱敏、迁移/备份、告警、回归矩阵 | 运维流程变化 |
| [collaboration.md](collaboration.md) | 分支、提交、合并门禁、发布与管理员验收步骤 | 协作方式或流程变化 |
| [plans/](plans/) | 历史任务拆分与执行记录（按日期归档） | 只追加，不回填 |

## 维护约定

1. **事实源优先级**：功能规则与验收以 [FRD](product/FRD.md) 为准；产品范围以 [PRD](product/PRD.md) 为准；接口与数据结构以 [architecture.md](architecture.md) 为准。其他文档冲突时应回改而不是另立规则。
2. **固定文案**：面向用户的免责声明、红线提示、错误文案由 `backend/tests/test_copy_audit.py` 锁定；修改文案必须同步 FRD 与测试。
3. **版本化规则**：`backend/rules/*.yaml` 的 `version` 字段是契约的一部分，调整规则需升版本并在 FRD/功能文档记录。
4. **计划文档是历史快照**：`plans/` 记录当时的任务与证据，不随实现回填；当前行为以 features/architecture/FRD 为准。
5. **链接使用相对路径**，移动或重命名文档时同步更新所有引用。
6. **不提交密钥**：文档中只出现变量名与占位值，真实账号、密码、API Key 放在被忽略的 `backend/.env`。

## 归档说明

`DESIGN_REVIEW.md`、`RELEASE.md`、`TASK_SUMMARY.md` 已在 2026-11 的文档整理中删除，当前仓库不再包含其内容；如需追溯请查阅版本历史。现行设计约束见 [design.md](design.md)，发布与运维流程见 [operations.md](operations.md)，交付记录见 [plans/](plans/)。
