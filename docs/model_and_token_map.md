# Model And Token Map

This document lists the concrete model names currently used in the codebase, the expert that uses them, and the token or API-key application link.

| Model Name | Expert | Required Key / Token | Application Link |
| --- | --- | --- | --- |
| `gpt-5.4` | `OrchestratorAgent`, `KnowledgeAgent`, and the default text-LLM path | `OPENAI_API_KEY` | [OpenAI API Key](https://platform.openai.com/docs/quickstart/step-2-set-up-your-api-key%23.class) |
| `gpt-image-1.5` | `ImageGenerationAgent` (`gpt_image`) | `OPENAI_API_KEY` | [OpenAI API Key](https://platform.openai.com/docs/quickstart/step-2-set-up-your-api-key%23.class) |
| `gemini-3.1-flash-image-preview` | `ImageGenerationAgent` (`nano_banana`), `ImageEditingAgent` (`nano_banana`) | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | [Google AI Studio API Key](https://ai.google.dev/gemini-api/docs/api-key) |
| `gemini-3-pro-preview` | `ImageToPromptAgent` | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | [Google AI Studio API Key](https://ai.google.dev/gemini-api/docs/api-key) |
| `veo-3.1-generate-preview` | `VideoGenerationAgent` (`veo`) | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | [Google AI Studio API Key](https://ai.google.dev/gemini-api/docs/api-key) |
| `doubao-seedream-5-0-260128` | `ImageGenerationAgent` (`seedream`), `ImageEditingAgent` (`seedream`) | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| `doubao-seedance-1-0-pro-250528` | `VideoGenerationAgent` (`seedance`) | `ARK_API_KEY` | [Volcengine Ark API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey) |
| `DINO-XSeek-1.0` | `ImageGroundingAgent` | `DDS_API_KEY` | [DeepDataSpace DINO-X Platform](https://cloud.deepdataspace.com/zh/dashboard/token-key) |
| `DINO-X-1.0` | `ImageSegmentationAgent` | `DDS_API_KEY` | [DeepDataSpace DINO-X Platform](https://cloud.deepdataspace.com/zh/dashboard/token-key) |

## Notes

- The table lists concrete model names that are explicitly used by the current code.
- The text-LLM layer supports more providers than the single default example `gpt-5.4`; this page focuses on the concrete models that appear in the current implementation.
- Some experts select providers dynamically. In those cases, the table records the model names that are actually invoked by the provider-specific code paths.
