FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install system dependencies for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0t64 \
    libgomp1 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install OpenCV, numpy, and pytest for the system Python
RUN pip3 install --no-cache-dir --break-system-packages \
    "opencv-contrib-python>=4.11.0.86" \
    "numpy>=2.0.0" \
    "pytest>=8.0.0" \
    "pytest-json-ctrf>=0.3.5"

# Prepend /usr/bin to PATH so system python3 (with cv2) is found first
ENV PATH=/usr/bin:${PATH}

# Generate example video with known shot boundaries
COPY video_gen/generate_video.py /tmp/generate_video.py
RUN python3 /tmp/generate_video.py /app/example_video.mp4 && \
    rm /tmp/generate_video.py

