# Project paths and environment settings.

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "store.db"

CSV_FILES: dict[str, Path] = {
    "categories": DATA_DIR / "desafio_tecnico_ai_eng - categories.csv",
    "customers": DATA_DIR / "desafio_tecnico_ai_eng - customers.csv",
    "orders": DATA_DIR / "desafio_tecnico_ai_eng - orders.csv",
    "order_items": DATA_DIR / "desafio_tecnico_ai_eng - order_items.csv",
    "products": DATA_DIR / "desafio_tecnico_ai_eng - products.csv",
    "promotions": DATA_DIR / "desafio_tecnico_ai_eng - promotions.csv",
}

POLICIES_PDF_PATH = DATA_DIR / "políticas_da_loja.pdf"

SUPPORTED_PROVIDERS = ("gemini", "groq")
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

# Check if all required CSV files and the policy PDF exist on disk
def validate_data_files() -> None:
    missing_files: list[str] = []
    for table_name, file_path in CSV_FILES.items():
        if not file_path.is_file():
            missing_files.append(f"{table_name}: {file_path}")

    if not POLICIES_PDF_PATH.is_file():
        missing_files.append(f"policies_pdf: {POLICIES_PDF_PATH}")

    if missing_files:
        joined = "\n  - ".join(missing_files)
        raise FileNotFoundError(f"Missing required data files:\n  - {joined}")

# Return validated provider, model name, and API key from environment.
def get_llm_settings(provider_override: str | None = None) -> dict[str, str]:
    provider = (provider_override or os.getenv("LLM_PROVIDER", "gemini")).strip().lower()

    if provider not in SUPPORTED_PROVIDERS:
        allowed = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{provider}'. Choose one of: {allowed}."
        )

    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        if not api_key or api_key == "your_gemini_api_key_here":
            raise ValueError(
                "Missing GEMINI_API_KEY. Set a valid key in your .env file."
            )
        return {"provider": provider, "model": model, "api_key": api_key}

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL
    if not api_key or api_key == "your_groq_api_key_here":
        raise ValueError(
            "Missing GROQ_API_KEY. Set a valid key in your .env file."
        )
    return {"provider": provider, "model": model, "api_key": api_key}

