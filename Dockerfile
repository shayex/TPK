FROM python:3.12-slim
WORKDIR /app
COPY app.py rappel.py ./
RUN mkdir /data && chown nobody /data
ENV REUNIONS_DB=/data/reunions.db REUNIONS_HOST=0.0.0.0 REUNIONS_PORT=8080
VOLUME /data
EXPOSE 8080
USER nobody
CMD ["python3", "app.py"]
