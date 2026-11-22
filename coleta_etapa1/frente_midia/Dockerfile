# Ambiente pronto para o coletor (útil no Windows, onde o pycurl não compila fácil)
#   docker build -t riw-coletor .
#   docker run --rm -it -p 5000:5000 -v "$(pwd)/dados:/app/dados" riw-coletor
FROM python:3.8-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libcurl4-openssl-dev libssl-dev git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir --no-deps \
        "git+https://github.com/binux/pyspider.git@897891cafb21ea5b4ac08e728ad2ea212879f7fa"
COPY . .
EXPOSE 5000
CMD ["python", "coletar.py", "iniciar", "--porta", "5000"]
