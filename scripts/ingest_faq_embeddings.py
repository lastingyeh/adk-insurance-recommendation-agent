import asyncpg
import os
import asyncio
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pgvector.asyncpg import register_vector

load_dotenv()  # Load environment variables from .env file

# Configuration
# Note: Use standard postgresql:// for asyncpg
DB_URL = os.getenv("ADK_SESSION_DB_URI") or os.getenv(
    "DATABASE_URL", "postgresql://user:password@localhost:5432/insurance"
)
if "postgresql+asyncpg://" in DB_URL:
    DB_URL = DB_URL.replace("postgresql+asyncpg://", "postgresql://")

MODEL_NAME = "gemini-embedding-001"
EMBED_DIM = 768
EMBED_BATCH_SIZE = 100  # 單次請求最大筆數，避免超過 API 上限


def _is_vertex_mode() -> bool:
    return os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _check_credentials() -> str | None:
    """
    依目前後端模式檢查必要憑證，缺少時回傳錯誤訊息（否則回傳 None）。
    Vertex 模式需要 GOOGLE_CLOUD_PROJECT + ADC；key 模式需要 GOOGLE_API_KEY。
    """
    if _is_vertex_mode():
        if not os.getenv("GOOGLE_CLOUD_PROJECT"):
            return (
                "Vertex 模式 (GOOGLE_GENAI_USE_VERTEXAI=1) 需設定 GOOGLE_CLOUD_PROJECT，"
                "並先完成 `gcloud auth application-default login`。"
            )
    else:
        if not os.getenv("GOOGLE_API_KEY"):
            return (
                "API key 模式 (GOOGLE_GENAI_USE_VERTEXAI=0) 需設定 GOOGLE_API_KEY，"
                "可至 https://aistudio.google.com/apikey 免費取得。"
            )
    return None


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """
    產生文字向量。改用 google-genai SDK，依 GOOGLE_GENAI_USE_VERTEXAI 自動選後端：
      =1 走 Vertex AI（需 ADC）、=0 走 Developer API（需 GOOGLE_API_KEY）。
    兩種後端共用同一段程式碼，沒有 GCP 的人也能用免費 key 完成 FAQ 向量化。
    以 cosine 距離檢索，降維後不需額外正規化。
    """
    client = genai.Client()  # 由環境變數自動判定 Vertex / Developer API
    config = types.EmbedContentConfig(
        task_type="RETRIEVAL_DOCUMENT", output_dimensionality=EMBED_DIM
    )
    results: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        resp = await client.aio.models.embed_content(
            model=MODEL_NAME, contents=batch, config=config
        )
        results.extend(embedding.values for embedding in resp.embeddings)
    return results


async def ingest_faq():
    cred_error = _check_credentials()
    if cred_error:
        print(f"Error: {cred_error}")
        return

    print(f"Connecting to database: {DB_URL}")
    conn = await asyncpg.connect(DB_URL)
    await register_vector(conn)
    try:
        # 1. Fetch FAQ knowledge
        print("Fetching FAQ knowledge...")
        rows = await conn.fetch("SELECT faq_id, question, answer FROM faq_knowledge")

        if not rows:
            print("No FAQ data found in faq_knowledge table.")
            return

        faq_ids = []
        texts_to_embed = []
        for row in rows:
            faq_ids.append(row["faq_id"])
            # Combine question and answer for better semantic representation
            texts_to_embed.append(
                f"Question: {row['question']}\nAnswer: {row['answer']}"
            )

        # 2. Generate embeddings
        print(
            f"Generating embeddings for {len(texts_to_embed)} items using {MODEL_NAME}..."
        )
        try:
            embeddings = await get_embeddings(texts_to_embed)
        except Exception as e:
            print(f"Error generating embeddings: {e}")
            return

        # 3. Insert into vec_faq_knowledge
        print("Inserting embeddings into vec_faq_knowledge...")
        # Clear existing embeddings to avoid duplicates if re-running
        await conn.execute("DELETE FROM vec_faq_knowledge")

        for faq_id, embedding in zip(faq_ids, embeddings):
            # pgvector expects a list of floats (as a string or list depending on driver)
            # asyncpg can handle list of floats directly if pgvector is enabled
            await conn.execute(
                "INSERT INTO vec_faq_knowledge (faq_id, embedding) VALUES ($1, $2)",
                faq_id,
                embedding,
            )

        print("Ingestion completed successfully.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(ingest_faq())
