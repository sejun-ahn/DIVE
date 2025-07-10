# DIVE
Deep Inertial-Only Velocity Aided Estimation for Quadrotors 

---
## Environment

```
conda create -f environment.yaml
```
or
```
conda create -n dive python=3.10
conda activate dive
conda install nvidia/label/cuda-11.8.0::cuda-toolkit
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu118
pip install lightning tensorboard numpy h5py matplotlib pandas scipy seaborn torchviz pymap3d
pip install git+https://github.com/decargroup/pymlg.git
pip install git+https://github.com/decargroup/navlie.git
```
the navlie package will install pymlg as a dependency.


---
DIDO Dataset
```
git clone https://github.com/sejun-ahn/DIDO
```
the original repository is [here (@zhangkunyi/DIDO)](https://github.com/zhangkunyi/DIDO)

set the directory at `network/train.py`

---
## Train

```
python network/train.py
```
the trained checkpoints are at `lightning_logs/validation_checkpoints`
```
tensorboard --logdir .
```
and you can check the tensorboard logs
## Validate/Test
```
python syn_pseudo/velocity_regressor_dido.py
```
the estimated results are at `<root_dir>/<filter_output_folder/<file_target>/<filter_output_name>`
## Visualize
```
python visualizer/plot_syn_dido.py
```