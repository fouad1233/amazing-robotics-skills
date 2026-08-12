<!-- Vendored from spacemit-robotics/robot-skills @ main
     Path:    skills/spacemit-robot-llm/SKILL.md
     Licence: Apache-2.0
     Source:  https://github.com/spacemit-robotics/robot-skills/blob/main/skills/spacemit-robot-llm/SKILL.md
     Unmodified copy. See NOTICE for attribution. -->

---
name: spacemit-robot-llm
description: components/model_zoo/llm 的构建、模型准备、llama-server 验证、llm_chat 示例、llama-bench 性能采集与 C++ API 接入入口。
metadata:
  requires:
    bins: ["bash", "curl"]
  sdk:
    module_paths:
      - components/model_zoo/llm
    primary_docs:
      - components/model_zoo/llm/README.md
      - components/model_zoo/llm/include/llm_service.h
      - components/model_zoo/llm/package.xml
    build_hint: single_package_first
---

# SpacemiT Robot LLM

## Purpose / 作用

本模块用于 SpacemiT Robot SDK 的大语言模型能力接入，模块路径是 `components/model_zoo/llm`。

适合处理：

- 构建、运行、调试或集成 `components/model_zoo/llm`。
- 准备 GGUF 模型、验证 `llama-cli`、启动 `llama-server`、运行 `llm_chat` 示例。
- 采集 `llama-bench` 性能数据。
- 调用 `LLMService` C++ API，接入 OpenAI 兼容 HTTP API、Tool Calling、流式输出或多轮对话。

不适合处理：

- ASR、TTS、VAD、audio、voiceprint 等语音链路的完整规则；这些应交给 speech 相关 skill 或 SDK 文档。
- MCP、mlink、gateway 或整机应用的完整规则；本 skill 只处理 LLM 模块边界。

## Contract / 契约

- 模块路径：`components/model_zoo/llm`
- 构建倾向：`single_package_first`
- 文档入口：`components/model_zoo/llm/README.md`、`components/model_zoo/llm/include/llm_service.h`、`components/model_zoo/llm/package.xml`
- 对外 API 头文件只有 `components/model_zoo/llm/include/llm_service.h`；`src/` 下文件只作为内部实现、排障或修改 backend 时的参考。
- 基础规则：先用 `spacemit-robot-shared` 解析 mode、SDK root 和 target；构建走 `spacemit-robot-build`；remote/hybrid 执行走 `spacemit-robot-remote`；hybrid 同步走 `spacemit-robot-sync`。
- 模型默认目录遵循 SDK README 约定，优先使用 `~/.cache/models/llm` 或用户指定路径；不要把大模型文件放进 SDK 仓库。
- 性能采集优先使用 SDK README 中的 `llama-bench` 口径。
- 不默认调用 `scripts/test/robot-test`；只有 CI、回归验证或用户明确要求时才使用。
- 涉及模型下载、安装依赖、`sudo`、启动长驻服务、占用端口、停止已有服务或长时间 benchmark 时，先说明影响并确认。

## User Goals / 用户目标

| 用户目标 | 本模块支持情况 | 资料入口 |
| --- | --- | --- |
| 搭建环境 | 支持 | `references/setup.md` |
| 跑通示例 | 支持 | `references/examples.md` |
| 调用 API 做应用 | 支持 | `references/api.md` |
| 跑性能数据 | 支持 | `references/benchmarking.md` |
| 排障 | 支持 | `references/troubleshooting.md` |

## Workflow / 工作流程

1. 判断用户是在搭环境、准备模型、构建组件、跑示例、写 API 应用、跑性能、排障，还是做代码修改。
2. 先按 `spacemit-robot-shared` 确认 SDK root、mode 和 target 需求；没有完整 SDK 时转 `spacemit-robot-sdk-bootstrap`。
3. 只读取与当前任务匹配的 reference 文件；不要为了“完整”预读所有 reference 或 SDK 源码。
4. 构建请求交给 `spacemit-robot-build`，不要在组件 skill 写死 target。
5. `remote` 和 `hybrid` 场景下，模型验证、服务启动、示例运行、benchmark 和排障命令都在板端完成。
6. 执行型请求必须真实运行命令，并返回模型路径、完整命令、关键输出、指标、日志或失败点。
7. 如果命令细节、参数语义或 API 行为不清，按 `References / 资料入口` 回读 SDK README、头文件或源码。

## References / 资料入口

只列实际存在的 reference 文件。每个条目说明什么时候读取。

- `references/setup.md`: 准备 LLM 依赖、模型目录、模型源、模型解析与下载规则，或检查 `llama.cpp-tools-spacemit`、`libcurl`、`nlohmann-json` 等依赖前读取。
- `references/examples.md`: 构建 LLM 组件、验证 GGUF 模型、启动 `llama-server`、检查 OpenAI 兼容服务、运行 `llm_chat` 示例前读取。
- `references/api.md`: 用户要调用或修改 `LLMService` C++ API、OpenAI 兼容后端、Tool Calling、流式输出、多轮对话或 metrics 行为时读取。
- `references/benchmarking.md`: 用户明确要求性能、benchmark、延迟、吞吐、tokens/s 或前后对比时读取。
- `references/troubleshooting.md`: 命令失败、工具缺失、模型缺失、端口冲突、服务不可达、API Key、构建产物或 benchmark 异常时读取。

SDK 回源资料：

- `components/model_zoo/llm/README.md`: LLM 模块事实源；搭环境、模型准备、示例运行、benchmark 或版本信息不清时读取。
- `components/model_zoo/llm/include/llm_service.h`: API 事实源；调用或修改 `LLMService` 前读取。
- `components/model_zoo/llm/package.xml`: 系统依赖、组件版本或构建类型不清时读取。

如果未来 SDK 模块内存在 `AGENTS.md`，优先读取该模块的 `AGENTS.md`，再按需读取 README、API、header 或 source。

## Notes / 注意事项

- 原有 LLM 专项规则不要丢失；应放在 `references/` 中按任务按需读取，而不是塞回 `SKILL.md` 主体。
- 不要默认 `killall`、`pkill`、删除模型缓存、覆盖已有模型或停止用户正在使用的服务。
- 不要从非默认模型源下载用户点名的性能测试模型；默认模型源没有时说明当前未找到支持模型，或请用户提供模型路径。
- 不要编造模型输出、服务返回、tokens/s、TTFT、latency 或 benchmark 数据。
- 云端 OpenAI 兼容服务的 API Key 只从用户已配置的环境变量或安全凭据读取，不写入源码、日志或最终回复。
