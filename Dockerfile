# MultiTranslate - hosted version (Render / Hugging Face Spaces / any Docker host)
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
ENV MT_PUBLIC=1 PYTHONUNBUFFERED=1 PORT=7860
EXPOSE 7860
# threads: jobs run in background threads inside the one worker; 8 request threads is plenty for a small site
CMD gunicorn web:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 300
