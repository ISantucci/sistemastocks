FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Assets del front: si hay Node, se rebuildea static/dist/ para que la imagen
# quede con el minificado al dia. Si no hay Node (o falla), NO se corta el
# build: la app sirve los fuentes de static/js y static/css y funciona igual
# (ver _asset_servido en app.py). El deploy real de produccion es Windows +
# git pull, y ahi el dist viene commiteado.
RUN if command -v npm >/dev/null 2>&1; then npm ci --omit=dev && npm run build; \
    else echo "[build] sin npm: se sirven los fuentes de static/"; fi

EXPOSE 5000

CMD ["python", "serve.py"]