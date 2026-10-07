FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgomp1 bzip2 ca-certificates cmake g++ make \
    && rm -rf /var/lib/apt/lists/*

# dlib has no wheel for this Python build, so pip compiles it. Cap jobs for a 2-vCPU host.
ENV CMAKE_BUILD_PARALLEL_LEVEL=2

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Public dlib 68-point landmark model. Not a secret.
RUN python -c "import urllib.request; urllib.request.urlretrieve('http://dlib.net/files/shape_predictor_68_face_landmarks.dat.bz2', '/tmp/shape.dat.bz2')" \
    && bunzip2 -c /tmp/shape.dat.bz2 > /app/shape_predictor_68_face_landmarks.dat \
    && rm -f /tmp/shape.dat.bz2

COPY advanced_video_analysis_api.py .

EXPOSE 5001

CMD ["python", "-u", "advanced_video_analysis_api.py"]
