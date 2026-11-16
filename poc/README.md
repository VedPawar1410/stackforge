# StackForge POC code

Everything (how it works, setup, results, limitations, learning log) is in the
[root README](../README.md). Quick start from this folder:

```bash
cp .env.example .env          # add GROQ_API_KEY
pip install -r requirements-dev.txt
docker compose up -d && python db/migrate_all.py
python -m uvicorn api.main:app --reload       # API on :8000
python -m streamlit run ui/app.py             # UI on :8501
pytest
```

Measured runs: [`reports/`](reports/README.md). Eval checklist and scripts: [`evals/`](evals/).
