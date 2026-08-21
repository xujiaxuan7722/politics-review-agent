from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    siliconflow_api_key: str
    siliconflow_base_url: str
    siliconflow_chat_model: str
    siliconflow_embed_model: str

    baidu_ocr_api_key: str
    baidu_ocr_secret_key: str

    database_url: str

    # 内置 RAG 索引文件位置（由 scripts/build_rag_index.py 生成）
    rag_index_path: str = "data/rag_index.json"

    class Config:
        env_file = ".env"


settings = Settings()
