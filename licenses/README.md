# 第三方来源与许可

知途使用第三方开源软件、检索模型及资料，并接入地图、天气和模型等在线服务。本说明列明相关来源及许可信息，供使用者查阅。各项内容分别适用其对应的许可证或服务条款，应用根目录的 `LICENSE` 不取代第三方许可。

文中第三方名称及链接仅用于来源标识，不表示相关机构或权利人对知途作出认可、推荐或担保。

## 1. 开源软件

| 组件与来源 | 版本 | 主要用途 | 许可标识 | 许可资料 |
| --- | --- | --- | --- | --- |
| [Vue](https://github.com/vuejs/core) | 3.5.44 | 界面与交互 | MIT | [许可记录](software/frontend/vue-3.5.44/LICENSE) |
| [Lucide](https://github.com/lucide-icons/lucide) | 0.468.0 | 界面功能图标 | ISC；所含Feather部分为MIT | [许可记录](software/frontend/lucide-vue-next-0.468.0/LICENSE) |
| [FastAPI](https://github.com/fastapi/fastapi) | 0.115.14 | 应用服务与实时响应 | MIT | [许可记录](software/python/fastapi-0.115.14/fastapi-0.115.14.dist-info/licenses/LICENSE) |
| [Uvicorn](https://github.com/encode/uvicorn) | 0.34.3 | 本地服务运行 | BSD-3-Clause | [许可记录](software/python/uvicorn-0.34.3/uvicorn-0.34.3.dist-info/licenses/LICENSE.md) |
| [OpenAI Python SDK](https://github.com/openai/openai-python) | 1.109.1 | 模型服务接入 | Apache-2.0 | [许可记录](software/python/openai-1.109.1/openai-1.109.1.dist-info/licenses/LICENSE) |
| [HTTPX](https://github.com/encode/httpx) | 0.28.1 | 外部服务请求 | BSD-3-Clause | [许可记录](software/python/httpx-0.28.1/httpx-0.28.1.dist-info/licenses/LICENSE.md) |
| [Pydantic](https://github.com/pydantic/pydantic) | 2.11.10 | 数据格式校验 | MIT | [许可记录](software/python/pydantic-2.11.10/pydantic-2.11.10.dist-info/licenses/LICENSE) |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 0.6.11 | 行程规划流程管理 | MIT | [许可记录](software/python/langgraph-0.6.11/langgraph-0.6.11.dist-info/licenses/LICENSE) |
| [LangChain Core](https://github.com/langchain-ai/langchain) | 0.3.86 | 模型交互与工具调用支持 | MIT | [许可记录](COMPONENTS.json) |
| [FastEmbed](https://github.com/qdrant/fastembed) | 0.9.0 | 资料语义检索与结果排序 | Apache-2.0 | [许可记录](software/python/fastembed-0.9.0/fastembed-0.9.0.dist-info/licenses/LICENSE) |
| [Qdrant Client](https://github.com/qdrant/qdrant-client) | 1.17.0 | 本地向量索引 | Apache-2.0 | [许可记录](software/python/qdrant-client-1.17.0/qdrant_client-1.17.0.dist-info/licenses/LICENSE) |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | 1.19.2 | 本地检索模型运行 | MIT | [许可记录](software/python/onnxruntime-1.19.2/onnxruntime/LICENSE) |
| [Pillow](https://github.com/python-pillow/Pillow) | 12.3.0 | 照片读取与处理 | MIT-CMU | [许可记录](software/python/pillow-12.3.0/pillow-12.3.0.dist-info/licenses/LICENSE) |
| [Vite](https://github.com/vitejs/vite) | 7.3.7 | 前端构建 | MIT | [许可记录](software/frontend/vite-7.3.7/LICENSE.md) |
| [TypeScript](https://github.com/microsoft/TypeScript) | 5.9.3 | 前端类型检查 | Apache-2.0 | [许可记录](software/frontend/typescript-5.9.3/LICENSE.txt) |

铁路查询功能采用[drfccv/mcp-server-12306](https://github.com/drfccv/mcp-server-12306)，版本为 `0.5.0.post20260930`，适用MIT许可。原版权声明见[许可证](railway-mcp/LICENSE)，具体来源版本及文件校验信息见[来源记录](railway-mcp/UPSTREAM.json)。该许可适用于工具代码，车次、票价及余票等信息的使用仍受数据提供方的相应规则约束。

便携版包含CPython 3.11.9运行环境，其适用条件见[Python及所含组件的许可声明](runtime/Python-3.11.9-LICENSE.txt)。启动程序采用PyInstaller 6.22.3制作，相关GPL许可、引导程序例外条款（Bootloader Exception）及其他声明见[许可原文](launcher/COPYING.txt)。

本版本所用的应用组件、铁路查询组件、界面组件及构建工具详见[组件清单](COMPONENTS.json)。随组件提供的许可证、版权声明及NOTICE文件保存在 `software/` 目录。未随附许可原文的条目已在清单中注明，其适用条件仍以相应项目公布的许可为准。

## 2. 本地检索模型

| 用途 | 原模型 | 实际权重来源与固定版本 | 许可依据 |
| --- | --- | --- | --- |
| 中文语义向量 | [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) | [Qdrant/bge-small-zh-v1.5](https://huggingface.co/Qdrant/bge-small-zh-v1.5)，`46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59`，ONNX | 原模型和转换仓库标注MIT；[模型卡](models/embedding-card.md)、[FlagEmbedding原版权声明](models/FlagEmbedding-LICENSE.txt) |
| 可选交叉编码器重排 | [BAAI/bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) | [onnx-community/bge-reranker-v2-m3-ONNX](https://huggingface.co/onnx-community/bge-reranker-v2-m3-ONNX)，`6f5ff65298512715a1e669753bc754d2bc8f367b`，INT8 ONNX | 原模型标注Apache-2.0；[转换方模型卡](models/reranker-card.md)、[许可正文](models/Apache-2.0.txt) |

本版本使用上述来源提供的模型权重及分词器文件，文件校验信息随资源包的 `rag/models.json` 提供。文本生成与图像理解由配置的在线模型服务提供，相应模型权重不包含在本产品的分发包中。

## 3. 上海图书馆研学资料

随附研学资源收录上海图书馆开放数据平台的20篇历史地点文字。统一来源与许可说明位于资料集的 `study/shanghai_youth_v1/README.md`；源码版安装资料后位于 `.runtime/study/shanghai_youth_v1/README.md`，便携版位于 `app/.runtime/study/shanghai_youth_v1/README.md`。各篇资料的出处、所引书籍及整理说明随原文和 `SOURCES.json` 来源清单提供。

资料适用平台默认的CC BY-NC-SA 2.0许可依据，特别注明者除外，具体使用条件见随附统一说明。许可说明和来源清单仅用于许可告知与来源查验，不参与研学内容检索或回答生成。

## 4. 图形、截图与在线服务

- **界面图标**：功能图标由Lucide提供，所含Feather部分的版权及MIT许可声明随Lucide的ISC许可文件保留。
- **场景图形**：首页城市主图以及城市、山丘、车船和飞机背景为知途界面配套的SVG图形。分发包不包含第三方景点照片或用户上传的照片。
- **产品截图**：使用说明中的插图为知途界面截图。截图中的地图内容由高德提供，并保留相应标识、版权声明及审图号。
- **地图与天气**：地图、地点及路线服务由[高德开放平台](https://lbs.amap.com/)提供；天气信息由高德或配置的[和风天气](https://dev.qweather.com/)提供。服务的使用范围及权限以对应服务条款和账号授权为准。
- **模型与联网检索**：默认文本模型及联网搜索、网页读取服务由[DeepSeek](https://api-docs.deepseek.com/)提供。模型服务提供方以实际配置为准；OpenAI Python SDK用于接口适配。更换服务提供方后，应遵守相应服务条款。
- **网页参考资料**：出行参考及照片问答可按需读取相关网页，并在回答中保留来源链接和必要短证据。网页全文不纳入随产品分发的本地资料集，相关内容的权利归原权利人所有。引用链接不构成对转载或再分发的额外授权。

使用、复制、修改或分发相关第三方内容时，请遵守各自适用的许可条件。本说明仅提供来源与许可信息，不取代许可证原文，亦不授予第三方未明确授予的商标、资料或商业使用权。

## 5. 仓颉运行库


仓颉运行程序随附本地 DLL，无需安装 SDK。运行库许可与开源软件声明保留在 源码版 `cangjie/bin/SDK-LICENSE` 和 `cangjie/bin/Open_Source_Software_Notice.docx`（便携版位于 `app/cangjie/bin`）。仓颉源码与运行程序的对应关系由 `journey_planner.build.json` 校验。
