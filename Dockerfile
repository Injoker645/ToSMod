FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt requirements-collectors.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-collectors.txt

COPY . .

ENV TOSMOD_DB_PATH=/app/data/tosmod.db
EXPOSE 5050

CMD ["python", "dashboard/app.py"]
