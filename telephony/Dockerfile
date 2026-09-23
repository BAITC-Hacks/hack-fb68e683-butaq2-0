FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so editing code does not reinstall them.
COPY pyproject.toml README.md ./
COPY telephony ./telephony
# langchain/langgraph ship so TELEPHONY_AGENT_RUNTIME can be switched without
# a rebuild; langchain-openai is what those adapters' runnables are built from.
RUN pip install -e ".[openai,twilio,langchain,langgraph,serve]" langchain-openai

COPY example_app.py ./

EXPOSE 8000
CMD ["uvicorn", "example_app:app", "--host", "0.0.0.0", "--port", "8000"]
