FROM continuumio/anaconda3

# Enable fonts and dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create conda environment
RUN conda create -n worker python=3.10
SHELL ["conda", "run", "-n", "worker", "/bin/bash", "-c"]

# Install specific Python packages using pip
COPY requirements.txt .
# PyTorch, torchvision, and torchaudio will be installed with a specific version for CUDA 11.8 support
# RUN pip install --index-url https://download.pytorch.org/whl/cu118 torch torchvision torchaudio
RUN conda install pytorch torchvision torchaudio pytorch-cuda=12.4 -c pytorch -c nvidia
RUN pip install -r requirements.txt

# Move application files into container
COPY . .

# Install Fonts
COPY fonts /usr/share/fonts/
RUN fc-cache -fv

CMD ["/opt/conda/envs/worker/bin/celery", "-A", "tasks", "worker", "--loglevel=info"]