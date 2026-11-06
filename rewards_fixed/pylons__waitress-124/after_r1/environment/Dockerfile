FROM docker.io/swerebenchv2/pylons-waitress:124-7b9c4b3
# The base image ships the gold patch, the test patch and the graded
# node ids at /swebench_matterhorn/swebench_instance.json.
RUN rm -rf /swebench_matterhorn
RUN curl -fsSL https://www.rfc-editor.org/rfc/rfc7230.txt -o /RFC7230.txt \
 && echo 'c7fdc8bebdf1f8195f731592c47f5ea822b489436fd905b01b55ef531fca4120  /RFC7230.txt' | sha256sum -c -
