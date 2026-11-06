FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

RUN /opt/verifier/bin/python -m ensurepip --upgrade \
    && /opt/verifier/bin/python -m pip install --no-cache-dir \
        numpy==2.3.4 \
        scipy==1.16.3 \
        pandas==2.3.3 \
        matplotlib==3.10.7

# Copy the data file
COPY files/annual_maxima.csv /app/
