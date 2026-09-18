FROM mcr.microsoft.com/playwright/python:v1.63.0-noble@sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-build-isolation .

RUN mkdir -p /data && chown pwuser:pwuser /data && chmod 700 /data
VOLUME ["/data"]
USER pwuser
ENTRYPOINT ["python", "-m", "vacancy_watcher"]
CMD ["run"]
