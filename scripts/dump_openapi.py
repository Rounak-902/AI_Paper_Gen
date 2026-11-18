import json
import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5432/papergen"
os.environ["JWT_SECRET"] = "dummy_secret_that_is_at_least_32_bytes_long"
os.environ["JWT_REFRESH_SECRET"] = "dummy_secret_that_is_at_least_32_bytes_long_refresh"
os.environ["GEMINI_API_KEY"] = "dummy"
os.environ["AI_MODEL_GENERATE"] = "dummy"
os.environ["AI_MODEL_CLASSIFY"] = "dummy"
os.environ["AI_EMBEDDING_MODEL"] = "dummy"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"

# Add backend directory to sys.path so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.main import app

output_path = Path(__file__).parent.parent / "openapi.json"
output_path.write_text(
    json.dumps(app.openapi(), indent=2) + "\n",
    encoding="utf-8",
    newline="\n",
)

print(f"OpenAPI schema dumped to {output_path}")
