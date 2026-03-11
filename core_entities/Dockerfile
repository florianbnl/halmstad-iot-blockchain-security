FROM python:3.9-slim
WORKDIR /app
RUN pip install web3 paho-mqtt python-dotenv eth-account
COPY . .
CMD ["python", "-u", "sim.py"]