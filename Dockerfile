FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system resona \
    && useradd --system --gid resona --home-dir /app --shell /usr/sbin/nologin resona

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=resona:resona . .
RUN mkdir -p /app/instance/storage /app/instance/exabyte_avatars \
    && chown -R resona:resona /app/instance

USER resona

EXPOSE 8001
VOLUME ["/app/instance"]

CMD ["gunicorn", "--workers", "3", "--timeout", "7200", "--graceful-timeout", "30", "--bind", "0.0.0.0:8001", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
