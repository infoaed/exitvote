FROM tiangolo/uvicorn-gunicorn-starlette:python3.11

COPY ./requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt

COPY ./app /app

ENV PORT=8080
