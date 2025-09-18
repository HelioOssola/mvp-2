# Imagem base
FROM python:3.13-slim

# Diretório de trabalho no container
WORKDIR /app

# Copiar dependências e instalar
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o restante do código
COPY . /app

# Expor a porta usada pela API secundária
EXPOSE 5001

# Comando de inicialização
CMD ["python", "app.py"]