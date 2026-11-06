FROM ubuntu:24.04

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    make \
    flex \
    bison \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY files/ /app/

# Add dependencies needed for the task environment below.
# Choose carefully: if the task tests the solver's ability to install
# something, do NOT pre-install it here.

# RUN apt-get update && apt-get install -y --no-install-recommends \
#     <your-packages> \
#     && rm -rf /var/lib/apt/lists/*

# RUN pip install --no-cache-dir <your-python-packages>

# Copy additional files from environment/files/ into the container
# COPY files/<filename> /app/

