from dataclasses import dataclass

from pydantic_settings import BaseSettings


@dataclass(frozen=True)
class LLMEndpoint:
    """一个模型服务端点：平台名 + OpenAI 兼容地址 + 密钥 + 模型名。"""

    provider: str
    base_url: str
    api_key: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"


class Settings(BaseSettings):
    # 基础组（SiliconFlow）：向量组、通用对话组留空时都回落到这里
    siliconflow_api_key: str
    siliconflow_base_url: str
    siliconflow_chat_model: str
    siliconflow_embed_model: str

    # 向量组：RAG 建索引与检索用。留空 = SILICONFLOW_*。换向量模型必须重建索引。
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = ""

    # 通用对话组：讲解 / 出题 / 判卷 / 错题分析 / 问答。留空 = SILICONFLOW_*。
    # provider 决定"思考开关"翻译成哪个字段：siliconflow=enable_thinking，sensenova=reasoning_effort。
    chat_provider: str = "siliconflow"
    chat_base_url: str = ""
    chat_api_key: str = ""
    chat_model: str = ""
    # 全局思考总闸：false 时判卷/错题分析等原本开思考的调用也一律关闭（省 token、提速）。
    chat_thinking: bool = True

    # 判卷专用组（裁决型任务，建议用纪律最好的模型）。留空 = 通用对话组。
    # GRADER_THINKING 留空则跟随 CHAT_THINKING。
    grader_provider: str = ""
    grader_base_url: str = ""
    grader_api_key: str = ""
    grader_model: str = ""
    grader_thinking: bool | None = None

    # 学习管家专用组（多步工具调用，需要更强的 function calling）。
    # 留空 = 通用对话组；填了以后失败会自动降级到通用对话组。
    manager_provider: str = ""
    manager_base_url: str = ""
    manager_api_key: str = ""
    manager_model: str = ""

    baidu_ocr_api_key: str
    baidu_ocr_secret_key: str

    database_url: str

    # 内置 RAG 索引文件位置（由 scripts/build_rag_index.py 生成）
    rag_index_path: str = "data/rag_index.json"

    class Config:
        env_file = ".env"

    @property
    def embed_endpoint(self) -> LLMEndpoint:
        return LLMEndpoint(
            provider="siliconflow",
            base_url=(self.embed_base_url or self.siliconflow_base_url).rstrip("/"),
            api_key=self.embed_api_key or self.siliconflow_api_key,
            model=self.embed_model or self.siliconflow_embed_model,
        )

    @property
    def base_chat_endpoint(self) -> LLMEndpoint:
        """基础组（SiliconFlow）对话端点：通用对话组失败时的降级目标。"""
        return LLMEndpoint(
            provider="siliconflow",
            base_url=self.siliconflow_base_url.rstrip("/"),
            api_key=self.siliconflow_api_key,
            model=self.siliconflow_chat_model,
        )

    @property
    def chat_endpoint(self) -> LLMEndpoint:
        return LLMEndpoint(
            provider=(self.chat_provider or "siliconflow").lower(),
            base_url=(self.chat_base_url or self.siliconflow_base_url).rstrip("/"),
            api_key=self.chat_api_key or self.siliconflow_api_key,
            model=self.chat_model or self.siliconflow_chat_model,
        )

    def _override(self, provider: str, base_url: str, api_key: str, model: str) -> LLMEndpoint:
        """在通用对话组之上按需覆盖：只填模型名就沿用通用组的平台/地址/密钥。"""
        chat = self.chat_endpoint
        return LLMEndpoint(
            provider=(provider or chat.provider).lower(),
            base_url=(base_url or chat.base_url).rstrip("/"),
            api_key=api_key or chat.api_key,
            model=model,
        )

    @property
    def grader_endpoint(self) -> LLMEndpoint:
        if not self.grader_model:
            return self.chat_endpoint
        return self._override(self.grader_provider, self.grader_base_url, self.grader_api_key, self.grader_model)

    @property
    def grader_thinking_enabled(self) -> bool:
        return self.chat_thinking if self.grader_thinking is None else self.grader_thinking

    @property
    def manager_endpoint(self) -> LLMEndpoint:
        if not self.manager_model:
            return self.chat_endpoint
        return self._override(self.manager_provider, self.manager_base_url, self.manager_api_key, self.manager_model)


settings = Settings()
