FROM python:3.12-slim
WORKDIR /app
COPY backend/server.py backend/engine.py ./
COPY backend/data ./data
COPY web ../web
ENV WL_HOST=0.0.0.0 WL_PORT=4731
EXPOSE 4731
CMD ["python3", "server.py"]
