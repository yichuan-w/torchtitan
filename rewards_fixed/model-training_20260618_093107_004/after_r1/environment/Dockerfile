FROM aweaiteam/calibforge@sha256:5afd28d4ab0d211ed08632daf378f5a2c45b9ba216c255a46b31d410c38336f5

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends libgomp1=14.2.0-4ubuntu2~24.04.1 \
    && /opt/verifier/bin/python -m ensurepip --upgrade \
    && /opt/verifier/bin/python -m pip install --no-cache-dir \
        lightgbm==4.7.0 pandas==3.1.1 scikit-learn==1.9.1 \
    && rm -rf /var/lib/apt/lists/*
