# Linux container: builds the authoritative Fortran TEP library natively and serves the UI on :8000
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends gfortran && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN rm -f simulator/tep/fortran/lib/*.dll && python scripts/build_fortran.py --method local --force
EXPOSE 8000
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
