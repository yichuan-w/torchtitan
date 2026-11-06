FROM ubuntu:24.04

WORKDIR /app

# Install Python and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch CPU version
RUN pip3 install --no-cache-dir --break-system-packages torch==2.2.0 --index-url https://download.pytorch.org/whl/cpu

# Copy the model and training scripts
COPY files/model.py /app/
COPY files/train.py /app/
COPY files/prepare_data.py /app/

# Generate the training data
RUN python3 prepare_data.py && rm prepare_data.py

