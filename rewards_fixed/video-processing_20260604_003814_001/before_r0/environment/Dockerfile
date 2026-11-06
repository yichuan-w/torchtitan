FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install OpenCV system dependencies and Python pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip3 install --no-cache-dir --break-system-packages \
    opencv-python==4.10.0.84 \
    numpy==1.26.4 \
    toml==0.10.2

# Generate example video with known freeze segments for development
# Use /usr/bin/python3 (the system Python with cv2/numpy/toml installed)
COPY files/generate_example_video.py /tmp/generate_example_video.py
RUN /usr/bin/python3 /tmp/generate_example_video.py && rm /tmp/generate_example_video.py

